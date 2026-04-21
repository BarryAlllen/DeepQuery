from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from deepquery.cli_adapters import get_adapter
from deepquery.config.settings import Settings
from deepquery.core.exceptions import AdapterError
from deepquery.core.models import Answer, Query
from deepquery.history import HistoryService
from deepquery.knowledge import DEFAULT_USER, KnowledgeService

logger = logging.getLogger(__name__)


class QueryService:
    """查询编排服务：把一次提问路由到对应的 CLI 适配器，并汇总结果。"""

    def __init__(
        self,
        settings: Settings,
        knowledge: KnowledgeService,
        history: HistoryService,
    ) -> None:
        self.settings = settings
        self.knowledge = knowledge
        self.history = history

    def _resolve_adapter(self, name: str | None, user_id: str):
        # 单次请求可以覆盖默认适配器，实现运行时切换 CLI 工具
        adapter_name = name or self.settings.default_adapter
        options = dict(self.settings.adapter_options.get(adapter_name, {}))
        # 注入按用户解析后的 MCP 可见目录；v2 加鉴权后只需改 user_id 来源
        options["mcp_dirs"] = [str(p) for p in self.knowledge.dirs_for_user(user_id)]
        options["user_id"] = user_id
        return get_adapter(
            adapter_name,
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
        adapter = self._resolve_adapter(query.adapter, user_id)
        sess = await self.history.ensure_session(
            user_id, query.session_id, query.question, adapter.name
        )
        await self.history.add_user_message(sess.id, query.question)
        chunks: list[str] = []
        try:
            async for chunk in adapter.run(query.question):
                chunks.append(chunk)
        except AdapterError as e:
            # 失败也要落一条 assistant 行，便于前端回放时看到错误
            await self.history.add_assistant_message(
                sess.id, adapter.name, content="".join(chunks).strip(), error_code=e.code
            )
            raise
        content = "".join(chunks).strip()
        await self.history.add_assistant_message(sess.id, adapter.name, content)
        return Answer(
            session_id=sess.id,
            adapter=adapter.name,
            question=query.question,
            content=content,
        )

    async def stream(self, query: Query) -> AsyncIterator[str]:
        # 流式：直接把 CLI 输出转发给 HTTP 客户端，前端可做打字机效果
        user_id = self._user_of(query)
        adapter = self._resolve_adapter(query.adapter, user_id)
        sess = await self.history.ensure_session(
            user_id, query.session_id, query.question, adapter.name
        )
        await self.history.add_user_message(sess.id, query.question)
        # 占位：先落一条空的 assistant 行，流结束/失败都更新它。
        # 这样连接中途断开也能保留已产出的内容。
        placeholder = await self.history.start_assistant_message(sess.id, adapter.name)
        chunks: list[str] = []
        try:
            async for chunk in adapter.run(query.question):
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
