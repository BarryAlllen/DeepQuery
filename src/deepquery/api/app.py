from contextlib import asynccontextmanager

from fastapi import FastAPI

from deepquery.api import errors
from deepquery.api.routes import adapters, knowledge, query, sessions
from deepquery.config.settings import get_settings
from deepquery.history import HistoryService
from deepquery.knowledge import KnowledgeService
from deepquery.mcp import SharedMCPConfig


def create_app() -> FastAPI:
    # 工厂函数：uvicorn --factory 会调用它，便于在测试中创建干净实例
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 启动：初始化知识库（含必要时 git clone），开后台自动 pull 任务
        ks = KnowledgeService(settings)
        await ks.initialize()
        # 共享 MCP 配置：一次写盘，所有用户 / 所有 CLI 复用。
        # v2 加用户私有 MCP 时会再叠一层；这里永远只管 public 目录的共享部分。
        mcp_shared = SharedMCPConfig(settings, ks)
        mcp_shared.rebuild()
        # 知识库后续变化时需要重写共享配置（git sync / webhook / 文档写入会影响目录树）
        ks.on_dirs_changed = mcp_shared.rebuild  # type: ignore[attr-defined]
        # 初始化历史库：开发/测试兜底 create_all；生产镜像里由 alembic 负责
        hs = HistoryService(settings)
        await hs.create_schema()
        app.state.settings = settings
        app.state.knowledge = ks
        app.state.mcp_shared = mcp_shared
        app.state.history = hs
        task = None
        if settings.knowledge_auto_pull_seconds > 0:
            import asyncio

            task = asyncio.create_task(ks.run_auto_pull())
        try:
            yield
        finally:
            if task:
                task.cancel()
            await hs.dispose()

    app = FastAPI(title="DeepQuery", version="0.1.0", lifespan=lifespan)

    # 把 AdapterError 翻译成稳定的 JSON 错误响应
    errors.install(app)

    # 所有业务路由统一走 /api 前缀，预留给后续的 /ui 前端
    app.include_router(query.router, prefix="/api", tags=["query"])
    app.include_router(adapters.router, prefix="/api", tags=["adapters"])
    app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
    app.include_router(sessions.router, prefix="/api", tags=["sessions"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        # 容器健康检查端点
        return {"status": "ok", "env": settings.env}

    return app
