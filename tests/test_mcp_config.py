import json
from pathlib import Path

from deepquery.config.settings import Settings
from deepquery.mcp import (
    build_mcp_config,
    build_opencode_config,
    write_mcp_config,
    write_opencode_config,
)


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


def test_build_opencode_config_shape(tmp_path: Path):
    s = _settings(tmp_path)
    kb = tmp_path / "kb" / "shared"
    cfg = build_opencode_config(s, [kb])

    assert cfg["$schema"].startswith("https://opencode.ai")
    fs = cfg["mcp"]["filesystem"]
    assert fs["type"] == "local"
    # command 必须以 npx -y @modelcontextprotocol/server-filesystem 开头，然后跟目录
    assert fs["command"][:3] == ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
    assert str(kb.resolve()) in fs["command"][3:]
    assert fs["enabled"] is True


def test_write_opencode_config_produces_dir_with_json(tmp_path: Path):
    s = _settings(tmp_path)
    target_dir = tmp_path / "oc"
    returned = write_opencode_config(s, [tmp_path / "kb" / "shared"], target_dir)

    # 返回的是目录（供 --dir 使用），不是 JSON 文件路径
    assert returned == target_dir
    assert (target_dir / "opencode.json").exists()
    parsed = json.loads((target_dir / "opencode.json").read_text(encoding="utf-8"))
    assert parsed["mcp"]["filesystem"]["type"] == "local"
