import json
from pathlib import Path

from deepquery.config.settings import Settings
from deepquery.mcp import build_mcp_config, write_mcp_config


def _settings(tmp_path: Path, **overrides) -> Settings:
    base = dict(
        env="testing",
        knowledge_dir=tmp_path / "kb",
        mcp_enabled=True,
        mcp_skip_permissions=True,
        mcp_extra_dirs=[],
    )
    base.update(overrides)
    return Settings(**base)


def test_build_mcp_config_uses_passed_dirs(tmp_path: Path):
    s = _settings(tmp_path)
    cfg = build_mcp_config(s, [tmp_path / "kb" / "shared"])

    fs = cfg["mcpServers"]["filesystem"]
    assert fs["command"] == "npx"
    assert fs["args"][:2] == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert str((tmp_path / "kb" / "shared").resolve()) in fs["args"][2:]


def test_build_mcp_config_dedupes_and_appends_extra_dirs(tmp_path: Path):
    extra = tmp_path / "extra"
    primary = tmp_path / "kb" / "shared"
    s = _settings(tmp_path, mcp_extra_dirs=[extra, primary])  # extra_dirs 也含 primary

    args = build_mcp_config(s, [primary, primary])["mcpServers"]["filesystem"]["args"]
    dirs = args[2:]
    # 同一目录只出现一次
    assert dirs.count(str(primary.resolve())) == 1
    assert str(extra.resolve()) in dirs


def test_write_mcp_config_creates_file(tmp_path: Path):
    s = _settings(tmp_path)
    target = tmp_path / "nested" / "claude_mcp.json"

    written = write_mcp_config(s, [tmp_path / "kb" / "shared"], target)

    assert written == target
    assert target.exists()
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["mcpServers"]["filesystem"]["command"] == "npx"
