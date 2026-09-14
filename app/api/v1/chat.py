"""第一版聊天接口，只负责参数接收、服务调用和统一响应封装。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.dependencies import get_chat_service
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.common import ApiResponse
from app.services.chat_service import ChatService


router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ApiResponse[ChatResponse])
async def chat(
    request: ChatRequest,
    service: Annotated[ChatService, Depends(get_chat_service)],
) -> ApiResponse[ChatResponse]:
    """调用配置好的大模型，为用户消息生成客服回复。"""
    response = await service.chat(request.message)
    return ApiResponse(data=response)
