"""跨 CLI 检索 + 记忆 e2e：claude 起会话、opencode 续会话。

验证两件事：
  1. 两家 CLI 都能通过共享 MCP 命中本地知识库（回答含事实关键词）
  2. 同一 session 从 claude 切 opencode 后，记忆仍在（应用层 replay 生效）

用法：uv run python tests/manual/test_cross_cli_e2e.py
前置：claude / opencode 均已装好，opencode 全局 provider 或 DEEPQUERY_ADAPTER_OPTIONS 已配。
"""

import asyncio
import time

from deepquery.api.app import create_app
from deepquery.core.models import Query


async def _ask(svc, adapter: str, question: str, session_id: str | None):
    q = Query(question=question, adapter=adapter, session_id=session_id)
    t0 = time.perf_counter()
    ans = await svc.ask(q)
    dt = time.perf_counter() - t0
    return ans, dt


async def main() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        from deepquery.services.query_service import QueryService

        svc = QueryService(
            app.state.settings,
            app.state.knowledge,
            app.state.history,
            app.state.mcp_shared,
        )

        results: list[tuple[str, bool, str]] = []

        # --- Turn 1: claude 起会话，问事实 ---
        q1 = "订单服务的请求超时默认是多少毫秒？只回答数字+单位。"
        print(f"\n=== [claude] Turn 1 ===\nQ: {q1}")
        ans1, dt1 = await _ask(svc, "claude_code", q1, None)
        print(f"[{dt1:.1f}s] A: {ans1.content[:300]}")
        ok1 = "3000" in ans1.content
        results.append(("claude 命中知识库", ok1, ans1.content))
        print("✅ PASS" if ok1 else "❌ FAIL")
        session_id = ans1.session_id
        print(f"session_id = {session_id}")

        # --- Turn 2: 同 session 切 opencode，依赖 turn1 的上下文 ---
        q2 = "那如果上游是支付回调，这个超时相关的重试怎么配？为什么？"
        print(f"\n=== [opencode] Turn 2（换 CLI 续聊） ===\nQ: {q2}")
        ans2, dt2 = await _ask(svc, "opencode", q2, session_id)
        print(f"[{dt2:.1f}s] A: {ans2.content[:400]}")
        # 续聊成功：opencode 能理解"这个超时"指订单，且给出重试禁用建议
        ok2 = ("ORDER_RETRY" in ans2.content or "重试" in ans2.content) and "0" in ans2.content
        results.append(("opencode 续会话+命中知识库", ok2, ans2.content))
        print("✅ PASS" if ok2 else "❌ FAIL")

        # --- Turn 3: 切回 claude，再问一个需要历史的问题 ---
        q3 = "刚才我一共问了几个和订单相关的问题？只回答数字。"
        print(f"\n=== [claude] Turn 3（切回 claude，测双向记忆） ===\nQ: {q3}")
        ans3, dt3 = await _ask(svc, "claude_code", q3, session_id)
        print(f"[{dt3:.1f}s] A: {ans3.content[:300]}")
        ok3 = "2" in ans3.content
        results.append(("claude 切回仍记得历史", ok3, ans3.content))
        print("✅ PASS" if ok3 else "❌ FAIL")

        # 汇总
        print("\n" + "=" * 50)
        passed = sum(1 for _, ok, _ in results if ok)
        for name, ok, _ in results:
            print(f"{'✅' if ok else '❌'} {name}")
        print(f"\n总计: {passed}/{len(results)} 通过")


if __name__ == "__main__":
    asyncio.run(main())
