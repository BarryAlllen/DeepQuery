from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from deepquery.api.deps import get_query_service
from deepquery.core.models import Answer, Query
from deepquery.services.query_service import QueryService

router = APIRouter()


@router.post("/query", response_model=Answer)
async def ask(query: Query, svc: QueryService = Depends(get_query_service)) -> Answer:
    return await svc.ask(query)


@router.post("/query/stream")
async def ask_stream(
    query: Query, svc: QueryService = Depends(get_query_service)
) -> StreamingResponse:
    return StreamingResponse(svc.stream(query), media_type="text/plain")
