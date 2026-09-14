"""顶层 API 路由组装模块，统一管理版本前缀和子路由。"""

from fastapi import APIRouter

from app.api.v1.chat import router as chat_router


# 第一阶段仅注册聊天接口，后续业务接口仍通过此处统一扩展。
api_router = APIRouter(prefix="/api/v1")
api_router.include_router(chat_router)
