import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from deepquery.api.deps import get_query_service
from deepquery.core.exceptions import AdapterError
from deepquery.core.models import Answer, Query
from deepquery.services.query_service import QueryService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/query", response_model=Answer)
async def ask(query: Query, svc: QueryService = Depends(get_query_service)) -> Answer:
    # AdapterError 由全局 handler 处理，这里只关心成功路径
    return await svc.ask(query)


@router.post("/query/stream")
async def ask_stream(
    query: Query, svc: QueryService = Depends(get_query_service)
) -> StreamingResponse:
    async def _gen():
        try:
            async for chunk in svc.stream(query):
                yield chunk
        except AdapterError as e:
            # 流已开始返回，不能再改 status；以约定标记把错误推到流尾，前端按行解析
            logger.info("stream AdapterError: %s", e.message)
            yield f"\n[[DEEPQUERY_ERROR]] {e.code}: {e.message}\n"

    return StreamingResponse(_gen(), media_type="text/plain")
