from fastapi import Request

from deepquery.knowledge import KnowledgeService
from deepquery.services.query_service import QueryService


def get_knowledge_service(request: Request) -> KnowledgeService:
    # 单例：在 lifespan 里初始化后挂在 app.state
    return request.app.state.knowledge


def get_query_service(request: Request) -> QueryService:
    return QueryService(request.app.state.settings, request.app.state.knowledge)
