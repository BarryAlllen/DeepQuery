"""MCP 配置生成。

负责为 CLI 工具（目前是 Claude Code）拼装 `.mcp.json`，
默认接入官方 `@modelcontextprotocol/server-filesystem`。

暴露给 MCP 的目录是"按用户解析后的可见知识库目录列表"，
v1 调用方传 `dirs`（通常来自 `KnowledgeService.dirs_for_user(user_id)`），
v2 加用户系统时不需要改本模块。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from deepquery.config.settings import Settings


def build_mcp_config(settings: Settings, dirs: Iterable[Path]) -> dict:
    """根据传入的目录列表生成 Claude Code 兼容的 MCP 配置字典。"""
    resolved: list[str] = []
    for d in dirs:
        s = str(Path(d).resolve())
        if s not in resolved:
            resolved.append(s)
    # 追加 mcp_extra_dirs（与用户无关的系统级目录）
    for extra in settings.mcp_extra_dirs:
        s = str(Path(extra).resolve())
        if s not in resolved:
            resolved.append(s)

    return {
        "mcpServers": {
            # 通过 npx 拉起官方 filesystem server，stdio 通信
            "filesystem": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-filesystem", *resolved],
            }
        }
    }


def write_mcp_config(settings: Settings, dirs: Iterable[Path], target: Path) -> Path:
    """把 MCP 配置写入指定路径，返回该路径。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(build_mcp_config(settings, dirs), indent=2), encoding="utf-8"
    )
    return target
