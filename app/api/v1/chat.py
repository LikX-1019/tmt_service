"""统一聊天接口，只负责参数接收、服务调用和响应封装。"""

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
    """由后端统一执行规则、商品和 QA 路由。"""
    response = await service.chat(request)
    return ApiResponse(data=response)
