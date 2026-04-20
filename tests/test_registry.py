from deepquery.cli_adapters import list_adapters


def test_builtin_adapters_registered():
    names = list_adapters()
    assert "claude_code" in names
    assert "opencode" in names
    assert "cursor" in names
