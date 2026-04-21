"""共享 MCP 配置：启动时和知识库变化后生成一次，所有用户/所有 CLI 共用。

设计：
- v1 单租户：所有 public scope 的知识库目录对所有人可见，共享一份配置足矣。
- v2 多租户（待实现）：共享配置只包含 public 目录；team/private 目录由"用户私有 MCP"层
  额外注入（options["user_mcp_path"] / options["user_opencode_dir"] 预留钩子）。

落地：
- 启动时 KnowledgeService.initialize 完成后调用 SharedMCPConfig.rebuild
- 知识库 sync / webhook 后同样 rebuild
- 适配器 build_command 时从 options 读路径，不再自己写盘
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from deepquery.config.settings import Settings
from deepquery.knowledge import DEFAULT_USER, KnowledgeService
from deepquery.mcp.config import write_mcp_config, write_opencode_config

logger = logging.getLogger(__name__)


class SharedMCPConfig:
    """持有共享 MCP 配置的路径，对外只读。

    `claude_config_path` → 用于 `claude --mcp-config <file>`
    `opencode_config_dir` → 用于 `opencode run --dir <dir>`，目录里含 opencode.json
    """

    def __init__(self, settings: Settings, knowledge: KnowledgeService) -> None:
        self.settings = settings
        self.knowledge = knowledge
        root = Path(tempfile.gettempdir()) / "deepquery" / "shared"
        self._claude_path = root / "claude_mcp.json"
        self._opencode_dir = root / "opencode"
        # 初始为空；调用 rebuild 后才可用
        self._ready = False

    @property
    def claude_config_path(self) -> Path | None:
        return self._claude_path if self._ready else None

    @property
    def opencode_config_dir(self) -> Path | None:
        return self._opencode_dir if self._ready else None

    def rebuild(self) -> None:
        """根据当前知识库 public 目录重写两份配置。幂等，可反复调用。"""
        # v1 口径：共享只暴露 default 用户可见目录（= 所有 public 目录）。
        # v2 改这里时只需换数据源（如 dirs_public_only()），其余代码不动。
        dirs = self.knowledge.dirs_for_user(DEFAULT_USER)
        if not dirs:
            logger.info("shared MCP: 无可见知识库目录，跳过生成")
            self._ready = False
            return
        if self.settings.mcp_enabled:
            write_mcp_config(self.settings, dirs, self._claude_path)
            write_opencode_config(self.settings, dirs, self._opencode_dir)
            self._ready = True
            logger.info(
                "shared MCP 已生成：claude=%s opencode_dir=%s dirs=%s",
                self._claude_path,
                self._opencode_dir,
                [str(d) for d in dirs],
            )
        else:
            self._ready = False
