"""把 AdapterError 统一翻译成结构化 JSON 响应。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from deepquery.core.exceptions import AdapterError

logger = logging.getLogger(__name__)


def install(app: FastAPI) -> None:
    @app.exception_handler(AdapterError)
    async def _adapter_error_handler(request: Request, exc: AdapterError) -> JSONResponse:
        # 业务可预期的错误：按异常自带的 http_status 返回，body 是稳定的 {code, adapter, message}
        logger.info("AdapterError on %s: %s", request.url.path, exc.message)
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})
