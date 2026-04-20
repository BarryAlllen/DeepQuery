from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import get_adapter, list_adapters, register

# Import built-in adapters so they self-register
from deepquery.cli_adapters import claude_code, opencode, cursor  # noqa: F401

__all__ = ["BaseCLIAdapter", "get_adapter", "list_adapters", "register"]
