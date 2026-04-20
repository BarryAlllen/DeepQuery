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


def test_build_mcp_config_includes_knowledge_dir(tmp_path: Path):
    s = _settings(tmp_path)
    cfg = build_mcp_config(s)

    assert "filesystem" in cfg["mcpServers"]
    fs = cfg["mcpServers"]["filesystem"]
    assert fs["command"] == "npx"
    # 第一个 arg 是 -y，第二个是包名，剩下都是允许目录
    assert fs["args"][:2] == ["-y", "@modelcontextprotocol/server-filesystem"]
    assert str((tmp_path / "kb").resolve()) in fs["args"][2:]


def test_build_mcp_config_appends_extra_dirs_unique(tmp_path: Path):
    extra = tmp_path / "extra"
    s = _settings(
        tmp_path,
        # 故意把 knowledge_dir 也放一遍，验证去重
        mcp_extra_dirs=[extra, tmp_path / "kb"],
    )
    args = build_mcp_config(s)["mcpServers"]["filesystem"]["args"]
    dirs = args[2:]
    assert dirs.count(str((tmp_path / "kb").resolve())) == 1
    assert str(extra.resolve()) in dirs


def test_write_mcp_config_creates_file(tmp_path: Path):
    s = _settings(tmp_path)
    target = tmp_path / "nested" / "claude_mcp.json"

    written = write_mcp_config(s, target)

    assert written == target
    assert target.exists()
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["mcpServers"]["filesystem"]["command"] == "npx"
