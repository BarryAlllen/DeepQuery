from __future__ import annotations

import re
from pathlib import Path

from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register
from deepquery.core.exceptions import AdapterAuthError, AdapterRuntimeError

# 命中即视为鉴权问题；opencode 的错误信息风格接近 claude，但不保证稳定
_AUTH_PATTERNS = re.compile(
    r"\b(401|403)\b"
    r"|unauthor[ie]zed"
    r"|forbidden"
    r"|invalid[\s_-]?api[\s_-]?key"
    r"|authentication[\s_-]?(failed|error)"
    r"|api[\s_-]?key[\s_-]?(not[\s_]?found|missing|invalid)"
    r"|please run.*login"
    r"|no[\s_-]?credentials"
    r"|未授权|鉴权失败|认证失败|无效.*api[\s_-]?key|请先登录",
    re.IGNORECASE,
)


@register
class OpenCodeAdapter(BaseCLIAdapter):
    """opencode CLI 适配器。

    与 claude_code 的主要差异：
    - Session：opencode 的 session id 是 ses_xxx 自定义格式，与 DeepQuery 的 UUID 不兼容。
      所以本适配器 supports_native_session=False，续聊由 QueryService 走应用层 replay。
    - MCP：opencode 只接受 opencode.json 配置式注入。共享 MCP 路径由
      SharedMCPConfig 在启动时写好，本适配器只读 options["opencode_config_dir"]，
      并通过 `opencode run --dir <dir>` 让 opencode 读到。
    - 凭据：opencode 不读 ANTHROPIC_API_KEY/ANTHROPIC_BASE_URL，靠 `providers login`
      或 ~/.config/opencode/opencode.json 里的 provider 定义自带。
    - 模型：必须通过 adapter_options['opencode']['model']（provider/model 格式）指定，
      或依赖全局 opencode.json 的 default model。
    """

    name = "opencode"
    supports_native_session = False

    @property
    def mcp_dirs(self) -> list[Path]:
        # 与 ClaudeCodeAdapter 对齐：由 QueryService 注入，v1 来自 dirs_for_user
        raw = self.options.get("mcp_dirs") or []
        return [Path(p) for p in raw]

    def _resolve_opencode_dir(self) -> Path | None:
        # 共享单例：路径由 SharedMCPConfig 在启动 / 知识库变化时写好，本类只读。
        # v2 用户私有 MCP 时再叠 options["user_opencode_dir"]。
        if not self._settings or not self._settings.mcp_enabled:
            return None
        if not self.mcp_dirs:
            return None
        shared = self.options.get("opencode_config_dir")
        if not shared:
            return None
        path = Path(shared)
        if not path.exists():
            return None
        return path

    def build_command(self, prompt: str) -> list[str]:
        # 若有 MCP 目录，就把"先查 filesystem MCP 再回答"的引导前置到 prompt。
        # 原因：opencode 没有 --append-system-prompt，也没有命令行 system prompt 注入，
        # 唯一靠谱的引导渠道就是 prompt 正文。不加的话，模型默认只搜当前 cwd。
        cfg_dir = self._resolve_opencode_dir()
        if cfg_dir and self.mcp_dirs:
            dirs_text = "、".join(str(p.resolve()) for p in self.mcp_dirs)
            guide = (
                f"你是 DeepQuery 知识库助手。已通过 filesystem MCP 暴露目录：{dirs_text}。"
                "回答任何问题前必须先用 filesystem MCP 的工具（list_directory / search_files / read_file）"
                "检索这些目录下的文档，再基于实际命中内容作答；如果检索后确实无相关内容，"
                "必须先明确说明\"知识库未找到\"，再决定是否凭通识补充。严禁绕过知识库直接凭印象回答。\n\n"
                "用户的问题：\n"
            )
            effective_prompt = guide + prompt
        else:
            effective_prompt = prompt

        cmd: list[str] = ["opencode", "run", effective_prompt]
        # 注：base.run 会把原始 prompt 也写进子进程 stdin，但 opencode 优先读 positional，
        # stdin 自动被忽略。引导只出现在 positional 里，不会重复。

        if cfg_dir:
            cmd += ["--dir", str(cfg_dir)]

        # 模型：opencode 启动必须指定（若 opencode.json 有 default model 也可省略）
        model = self.options.get("model")
        if model:
            cmd += ["-m", model]

        # 容器内无人值守：跳过工具权限确认
        if self._settings and self._settings.mcp_skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        return cmd

    def build_cwd(self) -> str | None:
        # opencode 通过 --dir 读取配置，cwd 交给父进程默认值即可；
        # 这里显式覆盖 base 的"切到第一个 mcp_dir"默认行为——opencode 自己
        # 不像 claude 那样会把 cwd 作为 MCP Roots 推给 server，所以无需切。
        return None

    def build_env(self) -> dict[str, str]:
        # opencode 不读 ANTHROPIC_API_KEY。只有当用户在 adapter_options['opencode']
        # 里显式配置了 env 覆盖时才透传。
        extra = self.options.get("env")
        if isinstance(extra, dict):
            return {str(k): str(v) for k, v in extra.items()}
        return {}

    def classify_failure(self, returncode: int, stderr: str) -> AdapterRuntimeError:
        if _AUTH_PATTERNS.search(stderr):
            hint = (
                "鉴权失败：opencode 需要先 `opencode providers login`，"
                "或在 ~/.config/opencode/opencode.json 配置 provider 凭据。"
            )
            return AdapterAuthError(
                f"{hint} 原始 stderr: {stderr.strip()[:500]}",
                adapter=self.name,
            )
        return super().classify_failure(returncode, stderr)
