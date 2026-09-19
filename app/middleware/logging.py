"""HTTP 访问日志中间件，统一记录请求结果和处理耗时。"""

import logging
from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_log_context, reset_log_context


logger = logging.getLogger(__name__)


def _state_context(request: Request) -> dict[str, object]:
    path_params = request.scope.get("path_params") or {}
    values: dict[str, object] = {}
    for field in ("conversation_id", "shop_id"):
        value = getattr(request.state, field, None)
        if value is None:
            value = path_params.get(field)
        if value is not None:
            values[field] = value
    return values


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """记录请求状态和耗时，但不记录可能包含敏感信息的请求体。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """调用下游处理器，并按成功或异常结果写入结构化日志。"""
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((perf_counter() - started) * 1000, 2)
            tokens = bind_log_context(**_state_context(request))
            try:
                logger.exception(
                    "http_request_failed",
                    extra={
                        "event": "http_request_failed",
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": 500,
                        "duration_ms": duration_ms,
                    },
                )
            finally:
                reset_log_context(tokens)
            raise

        duration_ms = round((perf_counter() - started) * 1000, 2)
        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        tokens = bind_log_context(**_state_context(request))
        try:
            logger.log(
                level,
                "http_request_completed",
                extra={
                    "event": "http_request_completed",
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        finally:
            reset_log_context(tokens)
        return response
