"""请求链路上下文中间件，为一次 HTTP 请求关联全部日志。"""

from __future__ import annotations

import re
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import bind_log_context, reset_log_context


_SAFE_CONTEXT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """复用合法请求/链路 ID，并提取路径中的会话与店铺上下文。"""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """校验、绑定并在请求完成后清理当前链路上下文。"""
        incoming_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            incoming_request_id
            if _SAFE_CONTEXT_ID.fullmatch(incoming_request_id)
            else uuid4().hex
        )
        incoming_trace_id = request.headers.get("X-Trace-ID", "")
        trace_id = (
            incoming_trace_id
            if _SAFE_CONTEXT_ID.fullmatch(incoming_trace_id)
            else request_id
        )

        # Router 在中间件调用下游之后才写入 path_params；这里从标准资源路径
        # 提取 ID，保证业务日志和访问日志都能拿到相同上下文。
        path = request.url.path
        shop_match = re.search(r"/shops/([^/]+)", path)
        conversation_match = re.search(r"/conversations/([^/]+)", path)
        context: dict[str, str] = {
            "request_id": request_id,
            "trace_id": trace_id,
        }
        if shop_match:
            context["shop_id"] = shop_match.group(1)
        if conversation_match:
            context["conversation_id"] = conversation_match.group(1)

        request.state.request_id = request_id
        request.state.trace_id = trace_id
        tokens = bind_log_context(**context)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace_id
            return response
        finally:
            reset_log_context(tokens)
