from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from deepquery.cli_adapters import get_adapter
from deepquery.config.settings import Settings
from deepquery.core.exceptions import AdapterError
from deepquery.core.models import Answer, Query
from deepquery.history import HistoryService, MessageRole
from deepquery.history.service import MessageView
from deepquery.knowledge import DEFAULT_USER, KnowledgeService
from deepquery.mcp import SharedMCPConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _TurnPlan:
    """一次请求下发前的编排结果，封装三路分支的差异。

    三种情况（只看"当前 adapter"和"上次成功 adapter"）：
      1. 首轮 (first_turn=True)：CLI 侧无上下文，用 --session-id 建新会话
      2. 同 CLI 续轮：走 CLI 原生续接（如 claude --resume），最便宜也最完整
      3. 换 CLI 续轮：CLI 原生机制不互通，把历史消息拼进 prompt 做应用层重放
    """

    session_id: str
    adapter_name: str
    # 传给适配器的 session_id（首轮/同 CLI 都传，换 CLI 时清空避免各家 CLI 搞混）
    cli_session_id: str | None
    # True 表示本轮应以"续接上轮"的模式启动，交给适配器映射到各家参数
    resume: bool
    # 换 CLI 场景下，需要前置到 prompt 的历史文本；其他场景为空串
    replay_prefix: str


class QueryService:
    """查询编排服务：把一次提问路由到对应的 CLI 适配器，并汇总结果。"""

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeService,
        history: HistoryService,
        mcp_shared: SharedMCPConfig | None = None,
    ) -> None:
        self.settings = settings
        self.knowledge = knowledge
        self.history = history
        self.mcp_shared = mcp_shared

    def _resolve_adapter(self, plan: _TurnPlan, user_id: str):
        options = dict(self.settings.adapter_options.get(plan.adapter_name, {}))
        # 注入按用户解析后的 MCP 可见目录；v2 加鉴权后只需改 user_id 来源
        options["mcp_dirs"] = [str(p) for p in self.knowledge.dirs_for_user(user_id)]
        options["user_id"] = user_id
        # 共享 MCP 配置路径：适配器只读，不再每请求写盘。
        # v2 用户私有 MCP 时，再额外注入 options["user_mcp_path"] /
        # options["user_opencode_dir"]，由适配器决定是否合并。
        if self.mcp_shared is not None:
            claude_path = self.mcp_shared.claude_config_path
            if claude_path is not None:
                options.setdefault("mcp_config_path", str(claude_path))
            opencode_dir = self.mcp_shared.opencode_config_dir
            if opencode_dir is not None:
                options.setdefault("opencode_config_dir", str(opencode_dir))
        # CLI 原生续接（仅在首轮 / 同 CLI 续轮传递；换 CLI 时故意留空）
        if plan.cli_session_id:
            options["session_id"] = plan.cli_session_id
            options["resume"] = plan.resume
        return get_adapter(
            plan.adapter_name,
            api_key=self.settings.api_key,
            options=options,
            settings=self.settings,
        )

    @staticmethod
    def _user_of(query: Query) -> str:
        return query.user_id or DEFAULT_USER

    async def ask(self, query: Query) -> Answer:
        # 非流式：收齐所有输出后一次性返回，适合前端简单调用
        user_id = self._user_of(query)
        plan = await self._plan_turn(user_id, query)
        adapter = self._resolve_adapter(plan, user_id)
        # DB 里只存用户原始问题；拼好的 prompt 是即用即丢，避免历史滚雪球
        await self.history.add_user_message(plan.session_id, query.question)
        prompt = plan.replay_prefix + query.question
        chunks: list[str] = []
        try:
            async for chunk in adapter.run(prompt):
                chunks.append(chunk)
        except AdapterError as e:
            # 失败也要落一条 assistant 行，便于前端回放时看到错误
            await self.history.add_assistant_message(
                plan.session_id, adapter.name, content="".join(chunks).strip(), error_code=e.code
            )
            raise
        content = "".join(chunks).strip()
        await self.history.add_assistant_message(plan.session_id, adapter.name, content)
        return Answer(
            session_id=plan.session_id,
            adapter=adapter.name,
            question=query.question,
            content=content,
        )

    async def stream(self, query: Query) -> AsyncIterator[str]:
        # 流式：直接把 CLI 输出转发给 HTTP 客户端，前端可做打字机效果
        user_id = self._user_of(query)
        plan = await self._plan_turn(user_id, query)
        adapter = self._resolve_adapter(plan, user_id)
        await self.history.add_user_message(plan.session_id, query.question)
        prompt = plan.replay_prefix + query.question
        # 占位：先落一条空的 assistant 行，流结束/失败都更新它。
        # 这样连接中途断开也能保留已产出的内容。
        placeholder = await self.history.start_assistant_message(plan.session_id, adapter.name)
        chunks: list[str] = []
        try:
            async for chunk in adapter.run(prompt):
                chunks.append(chunk)
                yield chunk
        except AdapterError as e:
            await self.history.finalize_assistant_message(
                placeholder.id, "".join(chunks).strip(), error_code=e.code
            )
            raise
        else:
            await self.history.finalize_assistant_message(
                placeholder.id, "".join(chunks).strip()
            )

    async def _plan_turn(self, user_id: str, query: Query) -> _TurnPlan:
        """决定本轮走哪条分支。见 _TurnPlan 注释。"""
        sess = await self.history.ensure_session(
            user_id, query.session_id, query.question, query.adapter
        )
        adapter_name = query.adapter or self.settings.default_adapter
        # 查适配器类是否声明支持用 DeepQuery 的 UUID 作为 CLI 原生 session id
        from deepquery.cli_adapters.registry import _REGISTRY

        adapter_cls = _REGISTRY.get(adapter_name)
        supports_native = bool(adapter_cls and adapter_cls.supports_native_session)

        # 首轮：没传 session_id（新会话）或虽传了 session_id 但还没有成功轮次
        # （比如上一轮全部失败，要重头来）
        if not query.session_id or not await self.history.has_completed_turn(sess.id):
            return _TurnPlan(
                session_id=sess.id,
                adapter_name=adapter_name,
                # 仅当适配器支持原生 session 时才把 UUID 传下去（用作 --session-id 建会话）
                cli_session_id=sess.id if supports_native else None,
                resume=False,
                replay_prefix="",
            )

        last_adapter = await self.history.last_successful_adapter(sess.id)
        if last_adapter == adapter_name and supports_native:
            # 同 CLI 续轮且适配器有原生续接能力（如 claude --resume），最高效
            return _TurnPlan(
                session_id=sess.id,
                adapter_name=adapter_name,
                cli_session_id=sess.id,
                resume=True,
                replay_prefix="",
            )

        # 其余情况（换 CLI 续轮 / 同 CLI 但不支持原生 session）：走应用层 replay，
        # 把历史拼进 prompt。CLI 侧一律当无状态调用。
        messages = await self.history.messages_for_replay(sess.id)
        return _TurnPlan(
            session_id=sess.id,
            adapter_name=adapter_name,
            cli_session_id=None,
            resume=False,
            replay_prefix=_format_replay(messages),
        )


def _format_replay(messages: list[MessageView]) -> str:
    """把历史消息拼成纯文本，放在当前问题前面。

    故意用极简格式，不引入 JSON / 角色 token / 特殊分隔符，任何 CLI 吃进去都不会出歧义。
    当前 user 轮已经由调用方 append，所以这里只拼到最后一条已落库的 assistant。
    """
    if not messages:
        return ""
    lines: list[str] = ["[以下是之前的对话历史]"]
    for m in messages:
        prefix = "用户" if m.role == MessageRole.user.value else "助手"
        lines.append(f"{prefix}：{m.content}")
    lines.append("")
    lines.append("[请基于以上历史继续回答下面这个问题]")
    lines.append("")
    return "\n".join(lines)
