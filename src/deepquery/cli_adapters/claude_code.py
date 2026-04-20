from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register


@register
class ClaudeCodeAdapter(BaseCLIAdapter):
    # 对应 npm 包 @anthropic-ai/claude-code 提供的 `claude` 命令
    name = "claude_code"

    def build_command(self, prompt: str) -> list[str]:
        # `claude -p` 为非交互式模式：读取 prompt 并把回答写到 stdout
        return ["claude", "-p", prompt]

    def build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        # 透传 API Key；生产中会是从 token.cvte.com 拿到的 key
        if self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key
        # 允许走公司代理 / 自建网关
        base_url = self.options.get("base_url")
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        return env
