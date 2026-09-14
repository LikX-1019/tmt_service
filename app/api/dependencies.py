"""FastAPI 依赖提供模块，集中管理接口所需服务实例。"""

from functools import lru_cache

from app.services.chat_service import ChatService


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    """返回进程级无状态聊天服务，便于缓存复用和测试替换。"""
    return ChatService()
