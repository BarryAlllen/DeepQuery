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
    kb = tmp_path / "kb" / "shared"
    kb.mkdir(parents=True)
    adapter = ClaudeCodeAdapter(settings=s, options={"mcp_dirs": [str(kb)]})

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
    assert str(kb.resolve()) in sp


def test_build_command_respects_skip_permissions_off(tmp_path: Path):
    s = _settings(tmp_path, mcp_skip_permissions=False)
    kb = tmp_path / "kb" / "shared"
    kb.mkdir(parents=True)
    cmd = ClaudeCodeAdapter(settings=s, options={"mcp_dirs": [str(kb)]}).build_command("hi")
    assert "--mcp-config" in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_build_command_respects_mcp_disabled(tmp_path: Path):
    s = _settings(tmp_path, mcp_enabled=False)
    cmd = ClaudeCodeAdapter(settings=s).build_command("hi")
    assert "--mcp-config" not in cmd
    assert "--dangerously-skip-permissions" not in cmd


def test_build_command_skips_mcp_when_no_dirs(tmp_path: Path):
    # 没有可见目录时不挂 MCP，避免 server 因缺参启动失败
    s = _settings(tmp_path)
    cmd = ClaudeCodeAdapter(settings=s).build_command("hi")
    assert "--mcp-config" not in cmd


def test_build_env_injects_api_key_and_base_url():
    adapter = ClaudeCodeAdapter(api_key="sk-test", options={"base_url": "https://gw.example"})
    env = adapter.build_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-test"
    # 同时设置 AUTH_TOKEN 兜底，部分 SDK 走这个变量
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert env["ANTHROPIC_BASE_URL"] == "https://gw.example"


def test_build_env_falls_back_to_settings_base_url(tmp_path: Path):
    # 没有 options.base_url 时应使用 settings.api_base_url
    s = _settings(tmp_path, api_base_url="https://token.cvte.com")
    env = ClaudeCodeAdapter(api_key="sk", settings=s).build_env()
    assert env["ANTHROPIC_BASE_URL"] == "https://token.cvte.com"


def test_options_base_url_overrides_settings(tmp_path: Path):
    # 单适配器 options 优先级高于全局 settings
    s = _settings(tmp_path, api_base_url="https://global")
    env = ClaudeCodeAdapter(
        api_key="sk", options={"base_url": "https://local"}, settings=s
    ).build_env()
    assert env["ANTHROPIC_BASE_URL"] == "https://local"


def test_build_env_skips_keys_when_unset():
    env = ClaudeCodeAdapter().build_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_BASE_URL" not in env


import pytest

from deepquery.core.exceptions import AdapterAuthError, AdapterRuntimeError


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: 401 Unauthorized",
        "request failed: 403 forbidden",
        "Invalid API key provided",
        "authentication failed: token expired",
        "API key not found. Please run `claude login`.",
        "鉴权失败，请检查 token",
        "请先登录后重试",
    ],
)
def test_classify_failure_detects_auth_errors(stderr: str):
    err = ClaudeCodeAdapter().classify_failure(1, stderr)
    assert isinstance(err, AdapterAuthError)
    assert err.http_status == 401
    assert err.adapter == "claude_code"


@pytest.mark.parametrize(
    "stderr",
    [
        "Error: connection refused",
        "Internal server error",
        "无法解析模型输出",
        "",
    ],
)
def test_classify_failure_falls_back_to_runtime(stderr: str):
    err = ClaudeCodeAdapter().classify_failure(2, stderr)
    assert isinstance(err, AdapterRuntimeError)
    # 不应被误判成 auth
    assert not isinstance(err, AdapterAuthError)
    assert err.http_status == 502
