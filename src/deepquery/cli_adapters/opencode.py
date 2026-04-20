from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register


@register
class OpenCodeAdapter(BaseCLIAdapter):
    name = "opencode"

    def build_command(self, prompt: str) -> list[str]:
        # Placeholder — adjust to the actual opencode CLI invocation.
        return ["opencode", "run", "-"]

    def build_env(self) -> dict[str, str]:
        return {"OPENCODE_API_KEY": self.api_key} if self.api_key else {}
