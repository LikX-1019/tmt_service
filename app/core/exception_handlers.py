"""FastAPI 异常处理模块，将内部异常转换为稳定且不泄密的响应。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppException, LLMInvocationError
from app.schemas.common import ApiResponse


logger = logging.getLogger(__name__)


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    """按统一响应结构创建错误响应。"""
    body = ApiResponse[None](code=code, message=message, data=None)
    return JSONResponse(status_code=status_code, content=body.model_dump())


async def app_exception_handler(
    request: Request,
    exc: AppException,
) -> JSONResponse:
    """处理可预期的业务异常，并按异常定义返回 HTTP 状态码。"""
    log = logger.error if exc.status_code >= 500 else logger.warning
    log(
        "application_error code=%s method=%s path=%s",
        exc.code,
        request.method,
        request.url.path,
        extra={
            "event": "application_error",
            "method": request.method,
            "path": request.url.path,
            "status_code": exc.status_code,
        },
    )
    # LLM wrappers may carry provider diagnostics; expose only the stable public
    # message and keep custom details server-side.
    public_message = (
        type(exc).default_message
        if isinstance(exc, LLMInvocationError)
        else exc.message
    )
    return _error_response(exc.code, public_message, exc.status_code)


async def validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    """处理 Pydantic 参数校验错误，不在响应和日志中记录用户原文。"""
    locations = [".".join(map(str, error["loc"])) for error in exc.errors()]
    logger.warning(
        "request_validation_failed fields=%s",
        ",".join(locations),
        extra={
            "event": "request_validation_failed",
            "method": request.method,
            "path": request.url.path,
            "status_code": 422,
        },
    )
    return _error_response("VALIDATION_ERROR", "请求参数校验失败", 422)


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """兜底处理未知异常：日志保留堆栈，客户端只收到通用提示。"""
    logger.error(
        "unhandled_exception method=%s path=%s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={
            "event": "unhandled_exception",
            "method": request.method,
            "path": request.url.path,
            "status_code": 500,
        },
    )
    return _error_response("INTERNAL_SERVER_ERROR", "服务器内部错误", 500)


def register_exception_handlers(app: FastAPI) -> None:
    """集中注册业务异常、参数异常和未知异常处理器。"""
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
