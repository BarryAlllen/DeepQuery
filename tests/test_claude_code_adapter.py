from pathlib import Path

from deepquery.cli_adapters.claude_code import ClaudeCodeAdapter
from deepquery.config.settings import Settings


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        env="testing",
        knowledge_dir=tmp_path / "kb",
        mcp_enabled=True,
        mcp_skip_permissions=True,
    )
    base.update(overrides)
    return Settings(**base)


def test_build_command_without_settings_has_no_mcp_flags():
    # 适配器不带 settings 时退化为最小命令，向后兼容
    cmd = ClaudeCodeAdapter().build_command("hello")
    assert cmd == ["claude", "-p", "hello"]


def test_build_command_appends_mcp_config_and_skip_permissions(tmp_path: Path):
    s = _settings(tmp_path)
    adapter = ClaudeCodeAdapter(settings=s)

    cmd = adapter.build_command("讲讲超时配置")

    assert cmd[:3] == ["claude", "-p", "讲讲超时配置"]
    assert "--mcp-config" in cmd
    cfg_path = Path(cmd[cmd.index("--mcp-config") + 1])
    # 真实写盘了，并且是 JSON
    assert cfg_path.exists()
    assert cfg_path.read_text().strip().startswith("{")
    assert "--dangerously-skip-permissions" in cmd
    # 必须注入 system prompt 引导模型先查知识库
    assert "--append-system-prompt" in cmd
    sp = cmd[cmd.index("--append-system-prompt") + 1]
    assert "filesystem MCP" in sp
    assert str((tmp_path / "kb").resolve()) in sp


def test_build_command_respects_skip_permissions_off(tmp_path: Path):
    s = _settings(tmp_path, mcp_skip_permissions=False)
    cmd = ClaudeCodeAdapter(settings=s).build_command("hi")
    assert "--mcp-config" in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_build_command_respects_mcp_disabled(tmp_path: Path):
    s = _settings(tmp_path, mcp_enabled=False)
    cmd = ClaudeCodeAdapter(settings=s).build_command("hi")
    assert "--mcp-config" not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_build_env_injects_api_key_and_base_url():
    adapter = ClaudeCodeAdapter(api_key="sk-test", options={"base_url": "https://gw.example"})
    env = adapter.build_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    assert env["ANTHROPIC_BASE_URL"] == "https://gw.example"
