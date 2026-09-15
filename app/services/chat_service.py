"""基础客服对话服务，负责组装消息、调用模型并规范化结果。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import get_settings
from app.core.exceptions import AppException, LLMInvocationError
from app.factories.llm_factory import LLMFactory
from app.prompts.chat import CUSTOMER_SERVICE_SYSTEM_PROMPT
from app.schemas.chat import ChatResponse


logger = logging.getLogger(__name__)


def _content_to_text(content: Any) -> str:
    """兼容 LangChain 字符串和文本块两种响应格式，统一提取纯文本。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts).strip()
    return str(content).strip() if content is not None else ""


class ChatService:
    """封装客服对话主流程，并将底层模型异常转换为业务异常。"""

    def __init__(self, llm: Any | None = None) -> None:
        """初始化服务；可注入模型替身以支持不联网的自动化测试。"""
        self._llm = llm

    async def chat(self, message: str) -> ChatResponse:
        """异步调用模型生成客服回复，全程不记录完整用户消息。"""
        settings = get_settings()
        model_name = settings.model_for("response")
        logger.info(
            "llm_invocation_started",
            extra={
                "event": "llm_invocation_started",
                "model": model_name,
            },
        )
        try:
            # 首次导入模型 SDK 在部分 Windows 环境中较慢，放到工作线程可避免
            # 阻塞 FastAPI 事件循环，其他健康检查和页面请求仍能正常响应。
            llm = self._llm or await asyncio.to_thread(LLMFactory.create)
            result = await llm.ainvoke(
                [
                    SystemMessage(content=CUSTOMER_SERVICE_SYSTEM_PROMPT),
                    HumanMessage(content=message),
                ]
            )
            answer = _content_to_text(result.content)
            if not answer:
                raise RuntimeError("LLM returned empty content")
        except AppException:
            raise
        except Exception as exc:
            logger.exception(
                "llm_invocation_failed",
                extra={
                    "event": "llm_invocation_failed",
                    "model": model_name,
                },
            )
            raise LLMInvocationError() from exc

        logger.info(
            "llm_invocation_succeeded",
            extra={
                "event": "llm_invocation_succeeded",
                "model": model_name,
            },
        )
        return ChatResponse(answer=answer)
