from fastapi import FastAPI

from deepquery.api import errors
from deepquery.api.routes import adapters, query
from deepquery.config.settings import get_settings


def create_app() -> FastAPI:
    # 工厂函数：uvicorn --factory 会调用它，便于在测试中创建干净实例
    settings = get_settings()
    app = FastAPI(title="DeepQuery", version="0.1.0")

    # 把配置挂在 app.state 上，路由里可通过 request.app.state 取
    app.state.settings = settings

    # 把 AdapterError 翻译成稳定的 JSON 错误响应
    errors.install(app)

    # 所有业务路由统一走 /api 前缀，预留给后续的 /ui 前端
    app.include_router(query.router, prefix="/api", tags=["query"])
    app.include_router(adapters.router, prefix="/api", tags=["adapters"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        # 容器健康检查端点
        return {"status": "ok", "env": settings.env}

    return app
