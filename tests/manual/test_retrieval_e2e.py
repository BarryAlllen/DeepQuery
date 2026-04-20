"""端到端检索回归脚本：直连 QueryService，验证 MCP filesystem 真的把答案带回来了。

用法：uv run python tests/manual/test_retrieval_e2e.py
"""

import asyncio
import time

from deepquery.core.models import Query
from deepquery.config.settings import get_settings
from deepquery.services.query_service import QueryService

CASES = [
    {
        "name": "订单超时默认值",
        "question": "订单服务的请求超时默认是多少毫秒？只回答数字+单位。",
        "must_contain": ["3000"],
    },
    {
        "name": "支付回调禁用重试",
        "question": "如果上游是支付回调，订单重试应该怎么配置？为什么？",
        "must_contain": ["ORDER_RETRY", "0"],
    },
    {
        "name": "订单状态机超时取消",
        "question": "订单创建后多久未支付会被自动取消？",
        "must_contain": ["30"],
    },
    {
        "name": "K8s 探针失败后果",
        "question": "Kubernetes liveness 探针失败会导致什么后果？",
        "must_contain": ["重启"],
    },
]


async def main() -> None:
    svc = QueryService(get_settings())
    passed = 0
    for case in CASES:
        print(f"\n=== {case['name']} ===")
        print(f"Q: {case['question']}")
        t0 = time.perf_counter()
        try:
            ans = await svc.ask(Query(question=case["question"]))
            dt = time.perf_counter() - t0
            print(f"[{dt:.1f}s] A: {ans.content[:300]}")
            ok = all(kw in ans.content for kw in case["must_contain"])
            print("✅ PASS" if ok else f"❌ FAIL（期望包含 {case['must_contain']})")
            passed += int(ok)
        except Exception as e:
            print(f"❌ ERROR: {e}")
    print(f"\n总计: {passed}/{len(CASES)} 通过")


if __name__ == "__main__":
    asyncio.run(main())
