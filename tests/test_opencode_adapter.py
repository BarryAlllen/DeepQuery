from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquery.cli_adapters.opencode import OpenCodeAdapter
from deepquery.config.settings import Settings
from deepquery.core.exceptions import AdapterAuthError, AdapterRuntimeError
from deepquery.mcp import write_opencode_config


def _shared_opencode(settings: Settings, dirs: list[Path], tmp_path: Path) -> str:
    return str(write_opencode_config(settings, dirs, tmp_path / "shared_opencode"))


def _settings(**overrides) -> Settings:
    base = dict(env="testing", mcp_skip_permissions=True, mcp_enabled=True)
    base.update(overrides)
    return Settings(**base)


def test_build_command_minimal():
    # 不带 settings 时只拼 `opencode run <prompt>`
    cmd = OpenCodeAdapter().build_command("hi")
    assert cmd == ["opencode", "run", "hi"]


def test_build_command_with_model_and_skip_permissions():
    s = _settings()
    adapter = OpenCodeAdapter(settings=s, options={"model": "cchClaude/claude-opus-4-7"})
    cmd = adapter.build_command("hi")
    assert cmd[:3] == ["opencode", "run", "hi"]
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "cchClaude/claude-opus-4-7"
    assert "--dangerously-skip-permissions" in cmd


def test_build_command_does_not_pass_session_id():
    # opencode 的 session id 是 ses_xxx，不接受 DeepQuery 的 UUID，
    # 所以即便 options 里给了 session_id 也必须被忽略
    adapter = OpenCodeAdapter(options={"session_id": "some-uuid", "resume": True})
    cmd = adapter.build_command("hi")
    assert "--session" not in cmd
    assert "--resume" not in cmd


def test_does_not_support_native_session():
    # 标志位决定 QueryService 续聊走 replay 前缀而不是 CLI 原生续接
    assert OpenCodeAdapter.supports_native_session is False


def test_build_command_injects_dir_when_mcp_dirs_present(tmp_path: Path):
    s = _settings()
    kb = tmp_path / "kb"
    kb.mkdir()
    cfg_dir = _shared_opencode(s, [kb], tmp_path)
    adapter = OpenCodeAdapter(
        settings=s,
        options={
            "mcp_dirs": [str(kb)],
            "user_id": "alice",
            "opencode_config_dir": cfg_dir,
        },
    )
    cmd = adapter.build_command("hi")
    assert cmd[:2] == ["opencode", "run"]
    # 有 MCP 时 prompt 前置了引导语，原 prompt 必须出现在末尾
    assert cmd[2].endswith("hi")
    assert "filesystem MCP" in cmd[2]
    assert "--dir" in cmd
    cfg_dir_p = Path(cmd[cmd.index("--dir") + 1])
    # 目录存在，且里面有 opencode.json 声明 filesystem MCP
    assert cfg_dir_p.is_dir()
    cfg = json.loads((cfg_dir_p / "opencode.json").read_text(encoding="utf-8"))
    assert cfg["mcp"]["filesystem"]["type"] == "local"
    assert str(kb.resolve()) in cfg["mcp"]["filesystem"]["command"]


def test_build_command_no_guide_when_no_mcp(tmp_path: Path):
    s = _settings(mcp_enabled=False)
    kb = tmp_path / "kb"
    kb.mkdir()
    adapter = OpenCodeAdapter(settings=s, options={"mcp_dirs": [str(kb)]})
    cmd = adapter.build_command("hi")
    # 没 MCP 配置时引导语也应该省略，prompt 保持原样
    assert cmd == ["opencode", "run", "hi", "--dangerously-skip-permissions"]


def test_build_command_skips_dir_when_no_mcp_dirs():
    s = _settings()
    adapter = OpenCodeAdapter(settings=s)
    cmd = adapter.build_command("hi")
    assert "--dir" not in cmd


def test_build_cwd_returns_none(tmp_path: Path):
    # opencode 不像 claude 会把 cwd 作为 MCP Roots，不需要切 cwd
    s = _settings()
    kb = tmp_path / "kb"
    kb.mkdir()
    adapter = OpenCodeAdapter(settings=s, options={"mcp_dirs": [str(kb)]})
    assert adapter.build_cwd() is None


def test_build_env_empty_by_default():
    # opencode 不读 ANTHROPIC_API_KEY，所以默认不透传 api_key
    assert OpenCodeAdapter(api_key="sk-xxx").build_env() == {}


def test_build_env_passthrough_from_options():
    # 用户可在 adapter_options.opencode.env 显式注入 env（扩展点）
    adapter = OpenCodeAdapter(options={"env": {"FOO": "bar"}})
    assert adapter.build_env() == {"FOO": "bar"}


@pytest.mark.parametrize(
    "stderr",
    [
        "401 unauthorized",
        "No credentials configured. Run `opencode providers login`.",
        "invalid api key",
        "鉴权失败",
    ],
)
def test_classify_failure_detects_auth(stderr: str):
    err = OpenCodeAdapter().classify_failure(1, stderr)
    assert isinstance(err, AdapterAuthError)
    assert err.adapter == "opencode"


def test_classify_failure_falls_back_to_runtime():
    err = OpenCodeAdapter().classify_failure(2, "some generic failure")
    assert isinstance(err, AdapterRuntimeError)
    assert not isinstance(err, AdapterAuthError)
