from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register


@register
class CursorAdapter(BaseCLIAdapter):
    name = "cursor"

    def build_command(self, prompt: str) -> list[str]:
        # Placeholder — adjust to the actual cursor CLI invocation.
        return ["cursor-agent", "-p", prompt]

    def build_env(self) -> dict[str, str]:
        return {"CURSOR_API_KEY": self.api_key} if self.api_key else {}
