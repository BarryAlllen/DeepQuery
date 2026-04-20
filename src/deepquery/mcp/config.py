"""MCP 配置生成。

负责为 CLI 工具（目前是 Claude Code）拼装 `.mcp.json`，
默认接入官方 `@modelcontextprotocol/server-filesystem`，
把 `knowledge_dir` 暴露成可被 LLM 按需 list / search / read 的目录树。
"""

from __future__ import annotations

import json
from pathlib import Path

from deepquery.config.settings import Settings


def build_mcp_config(settings: Settings) -> dict:
    """生成 Claude Code 兼容的 MCP 配置字典。"""
    # 允许暴露的目录列表：knowledge_dir 必含，再追加用户自定义
    dirs: list[str] = [str(settings.knowledge_dir.resolve())]
    for extra in settings.mcp_extra_dirs:
        resolved = str(Path(extra).resolve())
        if resolved not in dirs:
            dirs.append(resolved)

    return {
        "mcpServers": {
            # 通过 npx 拉起官方 filesystem server，stdio 通信
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", *dirs],
            }
        }
    }


def write_mcp_config(settings: Settings, target: Path) -> Path:
    """把 MCP 配置写入指定路径，返回该路径。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_mcp_config(settings), indent=2), encoding="utf-8")
    return target
