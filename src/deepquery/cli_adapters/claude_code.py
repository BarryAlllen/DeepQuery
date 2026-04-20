from __future__ import annotations

import tempfile
from pathlib import Path

from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register
from deepquery.mcp import write_mcp_config


@register
class ClaudeCodeAdapter(BaseCLIAdapter):
    # 对应 npm 包 @anthropic-ai/claude-code 提供的 `claude` 命令
    name = "claude_code"

    _mcp_config_path: Path | None = None

    def _ensure_mcp_config(self) -> Path | None:
        # 未启用或未注入 settings 时不挂 MCP，保持兼容
        if not self._settings or not self._settings.mcp_enabled:
            return None
        if self._mcp_config_path and self._mcp_config_path.exists():
            return self._mcp_config_path
        # 写到临时目录，进程级别复用一份即可
        tmp = Path(tempfile.gettempdir()) / "deepquery" / "claude_mcp.json"
        self._mcp_config_path = write_mcp_config(self._settings, tmp)
        return self._mcp_config_path

    def build_command(self, prompt: str) -> list[str]:
        # `claude -p` 为非交互式模式：读取 prompt 并把回答写到 stdout
        cmd: list[str] = ["claude", "-p", prompt]
        mcp_path = self._ensure_mcp_config()
        if mcp_path:
            cmd += ["--mcp-config", str(mcp_path)]
            # 强制引导：让模型把"先查知识库"作为默认动作，避免凭通识乱答
            kb = self._settings.knowledge_dir.resolve() if self._settings else None
            if kb:
                system_prompt = (
                    f"你是 DeepQuery 知识库助手。已通过 filesystem MCP 暴露目录 {kb}，"
                    "回答任何问题前必须先用工具检索（list_directory / search_files / read_file）该目录下的文档，"
                    "再基于实际命中内容作答；如果检索后确实无相关内容，必须先明确说明 \"知识库未找到\"，"
                    "再决定是否凭通识补充。严禁绕过知识库直接凭印象回答。"
                )
                cmd += ["--append-system-prompt", system_prompt]
            # 容器内无人值守：默认跳过工具权限确认
            if self._settings and self._settings.mcp_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
        return cmd

    def build_env(self) -> dict[str, str]:
        env: dict[str, str] = {}
        # 透传 API Key；生产中会是从 token.cvte.com 拿到的 key
        if self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key
        # 允许走公司代理 / 自建网关
        base_url = self.options.get("base_url")
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        return env
