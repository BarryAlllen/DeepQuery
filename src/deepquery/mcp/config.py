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


def _resolve_unique_dirs(settings: Settings, dirs: Iterable[Path]) -> list[str]:
    resolved: list[str] = []
    for d in dirs:
        s = str(Path(d).resolve())
        if s not in resolved:
            resolved.append(s)
    for extra in settings.mcp_extra_dirs:
        s = str(Path(extra).resolve())
        if s not in resolved:
            resolved.append(s)
    return resolved


def build_opencode_config(settings: Settings, dirs: Iterable[Path]) -> dict:
    """生成 opencode.json 的 mcp 节。

    opencode 自己的配置 schema：顶层 `mcp.<name>` 里 `type=local` + `command=[...]`。
    与 claude 的 mcpServers 结构不同，字段也不兼容，所以单独一个生成器。
    只包含 mcp 节；其他配置（model、provider）由用户自己的 ~/.config/opencode/opencode.json
    负责，opencode 会合并两处配置（--dir 指向的优先覆盖）。
    """
    resolved = _resolve_unique_dirs(settings, dirs)
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "filesystem": {
                "type": "local",
                "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", *resolved],
                "enabled": True,
                "timeout": 15000,
            }
        },
    }


def write_opencode_config(settings: Settings, dirs: Iterable[Path], target_dir: Path) -> Path:
    """把 opencode 配置写到 target_dir/opencode.json；返回该目录（供 --dir 使用）。

    opencode 通过 `--dir <target_dir>` 读项目级配置，所以这里写的是"目录里放一份
    opencode.json"，而不是像 claude_mcp_xxx.json 那样直接给文件路径。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    cfg = target_dir / "opencode.json"
    cfg.write_text(
        json.dumps(build_opencode_config(settings, dirs), indent=2), encoding="utf-8"
    )
    return target_dir

