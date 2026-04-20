# DeepQuery

基于 AI CLI 工具（Claude Code / OpenCode / Cursor）的知识检索服务。

## 快速开始

```bash
uv sync
cp .env.example .env.development
# 填入 DEEPQUERY_API_KEY (来自 https://token.cvte.com)

uv run deepquery
# 或 uv run uvicorn deepquery.api.app:create_app --factory --reload
```

API:
- `GET  /health`
- `GET  /api/adapters` — 列出可用 CLI
- `POST /api/query` — `{"question": "...", "adapter": "claude_code"}`
- `POST /api/query/stream` — 流式响应

## 架构

| 模块 | 作用 |
|---|---|
| `config/` | pydantic-settings，按 `DEEPQUERY_ENV` 加载 `.env.{development,production,testing}` |
| `cli_adapters/` | CLI 工具抽象 + 注册表，新增工具 = 子类 + `@register` |
| `services/` | `QueryService` 编排：Query → Adapter → Answer |
| `api/` | FastAPI 路由 |
| `knowledge/` `datasources/` `history/` | 占位，后续迭代 |

## 新增一个 CLI 工具

```python
# src/deepquery/cli_adapters/my_tool.py
from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register

@register
class MyToolAdapter(BaseCLIAdapter):
    name = "my_tool"
    def build_command(self, prompt): return ["my-tool", "-p", prompt]
    def build_env(self): return {"MY_TOOL_KEY": self.api_key}
```

然后在 `cli_adapters/__init__.py` import 一下即可自动注册。

## 构建镜像（OrbStack）

```bash
docker build -f docker/Dockerfile -t deepquery:dev .
docker run --rm -p 8000:8000 --env-file .env.production deepquery:dev
```

## 渐进式路线图

1. MVP：HTTP → CLI 子进程 → 回答
2. 本地 Markdown 知识库检索（注入 prompt 上下文）
3. Git / HTTP 文档同步
4. MCP + 数据库数据源
5. Skills 加载
6. 问答历史（SQLite）
7. Web 前端（打包进镜像）
