"""FastAPI 应用入口，负责组装配置、路由、中间件和异常处理器。"""

from fastapi import FastAPI

from app.api.router import api_router
from app.api.v1.health import router as health_router
from app.core.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用，不在入口文件中放置业务逻辑。"""
    settings = get_settings()
    configure_logging(settings)
    application = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
    )
    register_exception_handlers(application)
    application.include_router(health_router)
    application.include_router(api_router)

    # Starlette 会优先执行最后添加的中间件，因此请求 ID 中间件必须最后注册，
    # 才能包裹访问日志中间件，确保同一次请求的全部日志使用同一个 request_id。
    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(RequestIdMiddleware)
    return application


app = create_app()
