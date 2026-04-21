# DeepQuery 项目说明

> 跨 agent 上下文文档。读完即可接手开发。最后更新：2026-04-21。

## 1. 项目定位

**DeepQuery** 是一个把 AI CLI 工具（Claude Code、OpenCode、Cursor）作为后端引擎的"知识库检索服务"。

核心思路：不直接调 LLM API，而是把已经具备工具调用能力的 CLI（如 `claude -p`）当作"带脑子的 worker"在子进程里跑，通过 MCP filesystem server 把本地 Markdown 知识库挂给它，由它自己决定检索 → 阅读 → 组织答案。

- **服务形态**：FastAPI HTTP 服务，`POST /api/query` 接收问题、转发给 CLI 子进程、把回答透传回去。
- **部署形态**：单镜像（OrbStack/Docker）打包 Python 服务 + Node CLI + MCP server。
- **目标用户**：v1 单租户内部使用；v2 接入公司用户系统后做多租户隔离。

## 2. 技术栈

| 层 | 选型 | 备注 |
|---|---|---|
| 语言 | Python 3.12 | 用户硬性要求，不要降级 |
| 包管理 | uv | `uv sync` / `uv run deepquery` |
| Web | FastAPI 0.115+ + uvicorn[standard] | factory + lifespan |
| 配置 | pydantic-settings 2.6+ | `DEEPQUERY_` 前缀，多环境 .env |
| 子进程 | asyncio.create_subprocess_exec | 流式读 stdout、整体 wait_for 超时 |
| Git | 直接 shell 调 git | 不引入 GitPython |
| 历史持久化 | SQLAlchemy 2.x async + aiosqlite + alembic | 默认 SQLite，可切 PG |
| LLM 接入 | `@anthropic-ai/claude-code` (npm) + `opencode-ai` (npm) + `@modelcontextprotocol/server-filesystem` | Dockerfile 全局装 |
| 容器 | python:3.12-slim + nodejs 20 + uv | 非 root 用户 deepquery (uid=1000) |
| 测试 | pytest + pytest-asyncio + TestClient | `asyncio_mode=auto` |
| Lint | ruff（已包含在 deps） | line-length=100, py312 |

## 3. 目录结构

```
src/deepquery/
├── main.py                  # uvicorn 启动入口（uv run deepquery）
├── config/settings.py       # Settings + KnowledgeRepo + 多环境 .env 加载
├── core/
│   ├── models.py            # Query / Answer pydantic 模型
│   └── exceptions.py        # AdapterError 体系（auth/timeout/runtime/...）
├── cli_adapters/
│   ├── base.py              # BaseCLIAdapter ABC：subprocess + 超时 + build_cwd
│   ├── registry.py          # @register 装饰器 + get_adapter / list_adapters
│   ├── claude_code.py       # 主力：MCP + --add-dir + --session-id/--resume
│   ├── opencode.py          # 次力：opencode.json 读 MCP + replay 续聊
│   └── cursor.py            # 占位
├── mcp/
│   ├── config.py            # 生成 claude --mcp-config / opencode.json 的纯函数
│   └── shared.py            # SharedMCPConfig：进程级共享 MCP 配置（v2 留私有钩子）
├── knowledge/
│   ├── git_sync.py          # 异步 git 子进程封装
│   └── service.py           # KnowledgeService：多仓初始化/同步/写文件/log/revert
│                            #   on_dirs_changed 钩子：sync/init 后通知 SharedMCPConfig 重写
├── history/
│   ├── models.py            # SQLAlchemy 模型：Session / Message
│   ├── service.py           # HistoryService：会话 CRUD、replay、占位+finalize
│   └── __init__.py
├── services/query_service.py # 编排：Query → 解析目录 → 取适配器 → 跑 → 落库
├── api/
│   ├── app.py               # FastAPI factory + lifespan（KS + SharedMCPConfig + HS）
│   ├── deps.py              # get_{knowledge,history,query}_service
│   ├── errors.py            # AdapterError → JSONResponse 全局 handler
│   └── routes/{query,adapters,knowledge,sessions}.py
├── datasources/             # 占位（数据库等其他数据源，未实现）

alembic/                     # SQLAlchemy 迁移脚本，git 跟踪
alembic.ini                  # 配置（sqlite:///./deepquery.db）
tests/                       # 14 个文件，106 个测试，全绿
docker/Dockerfile            # OrbStack 镜像
.env.example                 # 配置模板
.env.development             # 本地（含真实 key，不提交）
```

## 4. 关键设计决策（含"为什么"）

### 4.1 CLI 适配器机制
- 所有 CLI 走统一的 `BaseCLIAdapter.run(prompt)` async generator，外层不知道是哪家 CLI。
- 子类只实现 `build_command` / `build_env` / `classify_failure` 三个钩子；可选覆盖 `build_cwd`。
- 注册靠模块 import 时执行 `@register` 副作用，`cli_adapters/__init__.py` 负责 import 所有子模块。
- 单次请求可通过 `Query.adapter` 字段覆盖默认适配器，实现运行时切换。
- 类属性 `supports_native_session: bool` 决定 QueryService 续聊时的策略：
  - `True`（claude_code）：DeepQuery 的 UUID 直接当 `--session-id`/`--resume`，走 CLI 原生续接，省 token
  - `False`（opencode 等）：续聊走应用层 replay，QueryService 把历史拼成文本前置到 prompt

### 4.2 Claude Code 适配
[src/deepquery/cli_adapters/claude_code.py](src/deepquery/cli_adapters/claude_code.py) `build_command` 拼装：

1. `-p <prompt>`：非交互式
2. `--session-id <uuid>` / `--resume <uuid>`：首轮建会话、后续续接（DeepQuery 的 session_id 是合法 UUID）
3. `--mcp-config <json>`：让 claude 通过 MCP 访问知识库；**路径来自共享单例**（见 §4.2c），不再每请求写盘
4. **`--add-dir <path>`**：⚠️ 必须有！claude 自身的 Read/Glob/Grep 工具有 cwd-based sandbox，容器里 cwd=/app，不加这个会"目录无访问权限"
5. `--append-system-prompt`：强制引导"先查知识库再回答"，否则模型会凭通识乱答
6. `--dangerously-skip-permissions`：⚠️ claude 拒绝以 root 跑这个 flag，所以 Dockerfile 必须 `USER deepquery`

⚠️ **MCP Roots 覆盖坑**（容器内才会暴露）：claude CLI 会把进程 cwd 作为 MCP Roots 推给 stdio MCP server，**覆盖 server args 里声明的允许目录**。容器中 cwd=/app 时 filesystem server 实际只暴露 /app，知识库被锁在外。修复：[base.py](src/deepquery/cli_adapters/base.py) `build_cwd()` 默认把子进程 cwd 切到第一个 mcp_dir（绝对路径，不存在时回退 None）。逻辑放在 base 是因为这是"用 stdio MCP 暴露目录"的 CLI 共性，不止 claude_code。

环境变量同时设 `ANTHROPIC_API_KEY` 和 `ANTHROPIC_AUTH_TOKEN`（公司网关 SDK 兜底）+ `ANTHROPIC_BASE_URL`。

`classify_failure` 用正则扫 stderr 关键词识别鉴权失败 → 抛 `AdapterAuthError`（401）；claude CLI 没稳定 exit code 区分 auth。

### 4.2b OpenCode 适配（与 claude 有结构差异）
[src/deepquery/cli_adapters/opencode.py](src/deepquery/cli_adapters/opencode.py)：
- **MCP 注入方式不同**：opencode 不支持 `--mcp-config` 这种运行时参数，只读 `opencode.json`。
  共享路径 `/tmp/deepquery/shared/opencode/opencode.json` 由 `SharedMCPConfig` 写好，
  `opencode run --dir <dir>` 让 opencode 读到。配置生成在 [mcp/config.py](src/deepquery/mcp/config.py) `write_opencode_config`。
- **Session 不兼容**：opencode 的 session id 是 `ses_xxx` 自定义格式，与 DeepQuery 的 UUID 不能直接互通。
  所以 `supports_native_session = False`，续聊一律走应用层 replay（QueryService 把历史拼进 prompt）。
- **没有 `--append-system-prompt`**：引导语只能直接拼进 prompt 正文（放 positional message 首段）。
- **不用 `build_cwd()`**：opencode 不像 claude 那样会把 cwd 作为 MCP Roots 推给 server，
  `OpenCodeAdapter.build_cwd()` 显式返回 None 覆盖 base 默认行为。
- **凭据路径**：opencode 用 `providers login` 写 `~/.local/share/opencode/auth.json`，
  不读 `ANTHROPIC_API_KEY`；与 claude 完全两套体系，镜像分别挂 volume。
- **模型必填**：通过 `DEEPQUERY_ADAPTER_OPTIONS={"opencode":{"model":"provider/model"}}` 注入，
  或依赖全局 `~/.config/opencode/opencode.json` 的 default model。

### 4.2c 共享 MCP 配置（避免每请求写盘）

[src/deepquery/mcp/shared.py](src/deepquery/mcp/shared.py) `SharedMCPConfig` 在 lifespan 启动时把 public scope 的目录集合写到固定路径：

- `/tmp/deepquery/shared/claude_mcp.json` → `claude --mcp-config <file>`
- `/tmp/deepquery/shared/opencode/opencode.json` → `opencode run --dir <dir>`

适配器只读 `options["mcp_config_path"]` / `options["opencode_config_dir"]`，不再每请求写盘。
路径由 `QueryService._resolve_adapter` 从 `app.state.mcp_shared` 透传到 options。

知识库 `initialize` / `sync_one` 成功后通过 `KnowledgeService.on_dirs_changed` 钩子触发 `SharedMCPConfig.rebuild()`，配置随目录变化重写。`write_file`/`delete_file` 不变更可见目录集合，故不触发。钩子异常被 `_notify_dirs_changed` 吞掉、只 log，不影响主流程。

v2 用户私有 MCP 扩展点（已留接口，未实现）：
- `options["user_mcp_path"]` / `options["user_opencode_dir"]`
- 适配器在那时把共享 servers 与用户 servers merge 后落到独立路径

### 4.3 多仓 + 多租户骨架
- `KnowledgeRepo` 有 `scope: public/team/private` 字段。
- `KnowledgeService.dirs_for_user(user_id)` 是**唯一**的可见性过滤入口。
- v1：`user_id` 永远是 `'default'`，public+team 全部可见，private 仅 default 可见。
- v2：只改 `dirs_for_user` 这一个方法即可接入真实用户系统，其他代码不动。
- `QueryService` 把 `dirs_for_user(user_id)` 结果作为 `mcp_dirs` 注入 adapter options。

### 4.4 Git 同步策略
- 远程仓：`git fetch + reset --hard origin/branch`（不用 pull，避免本地误改 merge 冲突）。把远端视为唯一事实源。
- 本地仓：`git init -b main` + 仓库级 author 配置（不污染 global）。
- 每仓一把 `asyncio.Lock`，多仓并发同步内部互斥。
- HTTP 写文件 → `commit_all` → 失败时 `git checkout -- file` 回滚工作区。
- 乐观锁：调用方传 `base_commit_sha`，与当前 HEAD 不一致返回 409 + `current_head`。

### 4.5 安全
- `_safe_resolve` 先判 `startswith("/")` 再 `lstrip("/")`，否则 `/tmp/abs.md` 会绕过。最后用 `Path.relative_to(root)` 边界校验。
- 单文件大小限制 `knowledge_max_file_bytes`（默认 5MB）。
- Webhook 用 `hmac.compare_digest` 比对共享密钥。

### 4.6 流式错误
`POST /api/query/stream` 已经开始 200 流，中途 AdapterError 不能改 status code，约定在流尾追加 `[[DEEPQUERY_ERROR]] <code>: <message>`，前端按行解析。

### 4.7 对话记忆（跨 CLI）

`QueryService._plan_turn` 三路分支（见 `_TurnPlan`）：

1. **首轮**：未传 `session_id` 或会话内还没有成功的 assistant turn → 新建会话；若适配器 `supports_native_session=True`，把 DeepQuery 的 UUID 当 `--session-id` 传下去
2. **同 CLI 续轮**：上一次成功的 adapter 与本次相同 + 适配器支持原生续接 → 走 `--resume`，零 token 浪费
3. **换 CLI 续轮 / 不支持原生**：从 `HistoryService.messages_for_replay` 取历史，用 `_format_replay` 拼成纯文本前置到 prompt；CLI 侧当无状态调用

历史落库走 `add_user_message` + `start_assistant_message`(占位) + `finalize_assistant_message`，流式中途断也能保留已产出的内容。`AdapterError` 路径会把错误码也写进同一占位行。

时间戳用 Python 侧 `datetime.now(timezone.utc)`（避免 SQLite 秒精度丢失，破坏 cursor 分页 + 避开 3.12 `utcnow` deprecation）。

## 5. HTTP 接口清单

| Method | Path | 说明 |
|---|---|---|
| GET | `/health` | 健康检查（容器 HEALTHCHECK 用） |
| GET | `/api/adapters` | 列出已注册适配器 |
| POST | `/api/query` | 同步问答，返回完整 Answer |
| POST | `/api/query/stream` | 流式 text/plain，错误见 4.6 |
| GET | `/api/knowledge/status` | 各仓状态（head/branch/dirty/...） |
| POST | `/api/knowledge/sync?name=` | 手动同步，省略 name 同步全部 |
| POST | `/api/knowledge/webhook` | git 平台回调，header `X-DeepQuery-Secret` |
| POST | `/api/knowledge/{name}/commit` | 手动 commit 工作区改动 |
| GET | `/api/knowledge/{name}/log?limit=` | 查 commit 历史 |
| POST | `/api/knowledge/{name}/revert` | 硬回滚到指定 commit |
| POST | `/api/knowledge/{name}/files` | 写/覆盖单文件，自动 commit，支持乐观锁 |
| DELETE | `/api/knowledge/{name}/files` | 删单文件，自动 commit |
| GET | `/api/sessions` | 列出会话（cursor 分页） |
| GET | `/api/sessions/{id}/messages` | 取某会话所有消息 |
| DELETE | `/api/sessions/{id}` | 删会话 |

## 6. 配置项（环境变量，前缀 `DEEPQUERY_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ENV` | development | development/production/testing，决定加载哪个 .env |
| `API_KEY` | "" | 上游 LLM key，公司从 https://token.cvte.com 申请 |
| `API_BASE_URL` | "" | 上游网关地址，留空走官方 |
| `DEFAULT_ADAPTER` | claude_code | 单次请求可覆盖 |
| `HOST` / `PORT` | 0.0.0.0 / 8000 | |
| `CLI_TIMEOUT_SECONDS` | 120 | 0 = 不限 |
| `KNOWLEDGE_DIR` | ./knowledge_base | 容器里 /data/knowledge_base |
| `KNOWLEDGE_REPOS` | 默认单仓 shared | JSON 数组覆盖 |
| `KNOWLEDGE_AUTO_PULL_SECONDS` | 0 | 后台 pull 间隔，0 关闭 |
| `KNOWLEDGE_WEBHOOK_SECRET` | "" | webhook 共享密钥 |
| `KNOWLEDGE_MAX_FILE_BYTES` | 5MB | HTTP 写入大小上限 |
| `MCP_ENABLED` | true | |
| `MCP_SKIP_PERMISSIONS` | true（生产）/ false（开发） | claude 工具权限确认 |
| `MCP_EXTRA_DIRS` | [] | 额外暴露的系统级目录 |
| `HISTORY_DB_URL` | sqlite+aiosqlite:///./deepquery.db | 历史库连接串，可换 PG |

`Settings` 中还有 `adapter_options: dict[str, dict]` 用来给某适配器塞自定义 base_url / model / env 等。

## 7. 测试覆盖

14 个测试文件，106 个测试，全部通过：
- `test_api_health.py` `test_api_errors.py`：HTTP 入口
- `test_registry.py` `test_claude_code_adapter.py` `test_opencode_adapter.py` `test_base_adapter_errors.py`：适配器层
- `test_mcp_config.py`：MCP json / opencode.json 生成
- `test_git_sync.py` `test_knowledge_service.py` `test_knowledge_routes.py` `test_knowledge_files.py`：知识库
- `test_history_service.py` `test_history_routes.py` `test_query_with_history.py`：历史 + 编排
- `tests/manual/test_retrieval_e2e.py`：手动 e2e（需要真 claude）
- `conftest.py` 提供 `bare_repo` fixture（本地 bare git）

⚠️ TestClient 必须用 `with TestClient(create_app()) as c:` 才能触发 lifespan 初始化 `app.state.{knowledge,history,mcp_shared}`。

## 8. Git 历史（最近 → 最早）

```
529b794 chore: alembic 迁移 + 容器启动跑 upgrade head
e3401b7 feat: /api/sessions 路由 + QueryService 接入历史双写
bd6c94f feat: 问答历史持久化基础（SQLAlchemy async + aiosqlite）
ddc51a9 fix: 子进程 cwd 切到首个 mcp_dir，绕过 MCP Roots 覆盖坑
b49118b docs: 新增 PROJECT.md 跨 agent 上下文文档
1b7eba5 fix: 容器镜像升级 3.12 + 非 root + claude --add-dir sandbox 修复
78e0102 feat: 知识库多仓 Git 集成 + HTTP 文档读写接口
98baf49 fix: 优化配置参数名（CLAUDE_BASE_URL → API_BASE_URL）
d493eae feat: 接入公司 Key 网关与统一错误处理
94a2080 feat: filesystem MCP 知识库检索
a9f4508 fix: gitignore不提交db文件
3c50f85 refactor: 完整单词命名运行环境（development/production/testing）
99be9e5 feat: 初始化 DeepQuery 项目骨架
```

⚠️ 工作树有未提交改动（待拆 commit）：
- 共享 MCP 重构（mcp/shared.py 新增 + 适配器改读路径 + KnowledgeService on_dirs_changed 钩子）
- OpenCode adapter 完整实现 + 测试
- QueryService cross-CLI replay + native resume 三路分支
- Dockerfile 加 opencode npm 包 + 卷规划
- 测试改动覆盖以上

## 9. 已完成 ✅

1. 项目骨架（uv + FastAPI + pydantic-settings 多环境）
2. CLI 适配器抽象 + 注册表：claude_code / opencode 都完整接 MCP（filesystem 知识库检索）；cursor 占位
3. 公司 Key 网关接入 + auth 错误识别（关键词）
4. filesystem MCP 接入 + 容器内 `--add-dir` sandbox 修复 + 子进程 cwd 修 MCP Roots 坑
5. 多仓知识库（远程 git + 本地 git，都走 `KnowledgeRepo`）
6. Git 同步：clone / fetch+reset / init / commit / log / revert
7. HTTP 文档读写接口（write/delete + 路径越界防御 + 乐观锁 + 大小限制）
8. 后台自动 pull（间隔可配）
9. Webhook（共享密钥）
10. 多租户骨架（scope + `dirs_for_user` 单点钩子）
11. 流式响应 + 流尾错误约定
12. OrbStack/Docker 镜像（非 root + healthcheck + bundle CLI + volume 规划）
13. 问答历史持久化（SQLAlchemy async + aiosqlite + alembic，支持切 PG）
14. **对话记忆**：`supports_native_session` 决定走 CLI 原生 resume 还是应用层 replay；
    同会话内自由切换 CLI（claude↔opencode）记忆不丢
15. **共享 MCP 配置**（`SharedMCPConfig`）：进程级单例，避免每请求写盘；
    `KnowledgeService.on_dirs_changed` 钩子驱动重写；v2 用户私有 MCP 钩子已留

## 10. 待办

### P1（下一步）
- 拆分并提交当前工作树改动（4 个 commit：共享 MCP 重构 / opencode adapter / QueryService 三路分支 / Dockerfile）
- 主机 e2e 验证 cross-CLI 检索（claude → opencode 切换记忆是否保留 + 共享 MCP 仍命中）
- 简单 deployment 文档（OrbStack 一键起 + 公司 key 申请说明）

### P2
- Web 前端（聊天界面 + 知识库浏览/编辑），打包进同一镜像
- Skills 加载（claude code 支持 user-level skills，复用现有目录）
- 数据库数据源（`datasources/` 目录已留位）
- replay 前缀截断：长会话拼历史会让 token 爆炸，v1 无截断；策略建议保留首轮 + 最近 N 轮
- 用户私有 MCP（`options["user_mcp_path"]` 钩子落地）

### P3
- v2 多租户：把 `dirs_for_user` 接到真实用户系统（团队/私有过滤）
- cursor 适配器实测
- 速率限制 / 计费埋点

## 11. 协作注意事项（给后续 agent）

- **不要**把 Python 降到 3.11 以下；用户明确要 3.12。
- **不要**给 `.env.development` 加 `DEEPQUERY_KNOWLEDGE_REPOS=`（空字符串），pydantic-settings 会尝试 JSON parse 然后炸。要么删掉这行要么填 JSON。
- **不要**在容器里以 root 跑 claude（refuses `--dangerously-skip-permissions`）。Dockerfile 已经 `USER deepquery`。
- **不要**改 `_safe_resolve` 里 `startswith("/")` 的判断顺序，必须在 lstrip 之前。
- **不要**让适配器再往 `/tmp/deepquery/{claude_mcp_xxx,opencode_xxx}` 写盘——共享配置改动走 `SharedMCPConfig.rebuild()`。
- **不要**用 `datetime.utcnow()`（3.12 deprecation）；统一 `datetime.now(timezone.utc)`。
- 用户偏好**简洁**：不写多余 docstring、不加 emoji、不主动 commit 除非明说、回答尽量短。
- 用户的提交信息都是中文 + 类型前缀（feat/fix/refactor/chore/docs），延续这个风格。
- 添加 CLI 适配器：见 [README.md](README.md#新增一个-cli-工具) 三步流程。
- 用 `uv run pytest` 跑测试；`uv run deepquery` 起服务；`uv run alembic upgrade head` 跑迁移。

## 12. 用户身份与协作模式

- 角色：在公司内部做 AI 工具基础设施的工程师。
- 偏好渐进式开发，每个阶段交付能跑的东西，再迭代。
- 重视代码可读性（中文注释解释 why）和未来可扩展性（如 v2 多租户钩子、用户私有 MCP）。
- 决策时倾向给出 A/B 选项让其挑，而不是直接动手。
- 关注 I/O 浪费：把"每请求写盘"识别为反模式，要求改成共享单例。
