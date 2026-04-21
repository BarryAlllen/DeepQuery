from __future__ import annotations

import re
import tempfile
from pathlib import Path

from deepquery.cli_adapters.base import BaseCLIAdapter
from deepquery.cli_adapters.registry import register
from deepquery.core.exceptions import AdapterAuthError, AdapterRuntimeError
from deepquery.mcp import write_mcp_config

# 命中即视为鉴权问题：HTTP 401/403、常见英文/中文鉴权关键字、缺少 key 提示
_AUTH_PATTERNS = re.compile(
    r"\b(401|403)\b"
    r"|unauthor[ie]zed"
    r"|forbidden"
    r"|invalid[\s_-]?api[\s_-]?key"
    r"|authentication[\s_-]?(failed|error)"
    r"|api[\s_-]?key[\s_-]?(not[\s_]?found|missing|invalid)"
    r"|please run.*login"
    r"|未授权|鉴权失败|认证失败|无效.*api[\s_-]?key|请先登录",
    re.IGNORECASE,
)


@register
class ClaudeCodeAdapter(BaseCLIAdapter):
    # 对应 npm 包 @anthropic-ai/claude-code 提供的 `claude` 命令
    name = "claude_code"

    _mcp_config_path: Path | None = None

    @property
    def mcp_dirs(self) -> list[Path]:
        # 由 QueryService 在构造时通过 options['mcp_dirs'] 注入；
        # v1 来自 KnowledgeService.dirs_for_user，v2 接入鉴权后无需改本类。
        raw = self.options.get("mcp_dirs") or []
        return [Path(p) for p in raw]

    def _ensure_mcp_config(self) -> Path | None:
        # 未启用或未注入 settings 时不挂 MCP，保持兼容
        if not self._settings or not self._settings.mcp_enabled:
            return None
        # 没有可见目录就不挂 MCP，否则 server 会因缺参启动失败
        dirs = self.mcp_dirs
        if not dirs:
            return None
        # 每个用户独立的配置文件，避免互相覆盖
        user_id = self.options.get("user_id") or "default"
        tmp = Path(tempfile.gettempdir()) / "deepquery" / f"claude_mcp_{user_id}.json"
        self._mcp_config_path = write_mcp_config(self._settings, dirs, tmp)
        return self._mcp_config_path

    def build_command(self, prompt: str) -> list[str]:
        # `claude -p` 为非交互式模式：读取 prompt 并把回答写到 stdout
        cmd: list[str] = ["claude", "-p", prompt]
        mcp_path = self._ensure_mcp_config()
        if mcp_path:
            cmd += ["--mcp-config", str(mcp_path)]
            # 同时通过 --add-dir 把目录加入 claude 自身工具（Read/Glob/Grep）的可访问范围，
            # 否则在容器中（cwd=/app）调 Read 会被 sandbox 拒绝
            for d in self.mcp_dirs:
                cmd += ["--add-dir", str(d.resolve())]
            # 强制引导：让模型把"先查知识库"作为默认动作，避免凭通识乱答
            dirs_text = "、".join(str(p.resolve()) for p in self.mcp_dirs)
            system_prompt = (
                f"你是 DeepQuery 知识库助手。已通过 filesystem MCP 暴露目录：{dirs_text}。"
                "回答任何问题前必须先用工具检索（list_directory / search_files / read_file）这些目录下的文档，"
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
        # 透传 API Key；生产中是从 https://token.cvte.com 申请的 key
        if self.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_key
            # 公司网关同时签发的 token，部分 SDK 会读 ANTHROPIC_AUTH_TOKEN，一并设置兜底
            env["ANTHROPIC_AUTH_TOKEN"] = self.api_key
        # base_url 优先级：单适配器 options > 全局 settings.api_base_url
        base_url = self.options.get("base_url")
        if not base_url and self._settings:
            base_url = getattr(self._settings, "api_base_url", "") or ""
        if base_url:
            env["ANTHROPIC_BASE_URL"] = base_url
        return env

    def classify_failure(self, returncode: int, stderr: str) -> AdapterRuntimeError:
        # claude CLI 在鉴权问题上没有稳定的 exit code，只能扫 stderr 关键词
        if _AUTH_PATTERNS.search(stderr):
            hint = (
                "鉴权失败：请检查 DEEPQUERY_API_KEY 是否有效、"
                "DEEPQUERY_API_BASE_URL 是否正确，或本机 `claude login` 状态。"
            )
            return AdapterAuthError(
                f"{hint} 原始 stderr: {stderr.strip()[:500]}",
                adapter=self.name,
            )
        return super().classify_failure(returncode, stderr)
