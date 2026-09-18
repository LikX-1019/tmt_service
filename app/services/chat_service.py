"""统一客服聊天编排：规则优先，商品事实与通用 QA 分流。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.exceptions import AppException, LLMInvocationError, ProductServiceUnavailableError
from app.models.chat import ChatConversationProduct
from app.qa.models import QAResult
from app.qa.service import QAService
from app.repositories.conversation_repository import ConversationProductRepository
from app.repositories.product_repository import ProductRepository
from app.rules.base import RuleContext
from app.rules.registry import RuleRegistry, default_rule_registry
from app.schemas.chat import ChatRequest, ChatResponse, ChatSourceView, ChatProductView
from app.services.product_resolver import ProductResolver
from app.services.product_service import (
    ProductAnswer,
    ProductAnswerService,
    ProductLookupError,
    ProductNotFoundError,
    ProductProfile,
)


logger = logging.getLogger(__name__)


def _content_to_text(content: Any) -> str:
    """兼容 LangChain 字符串和文本块响应，保留给既有 QA 生成器复用。"""
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
    """封装 Terminal Rule → Product → QA 的后端主路由。"""

    def __init__(
        self,
        *,
        rule_registry: RuleRegistry | None = None,
        conversation_repository: ConversationProductRepository | None = None,
        product_repository: ProductRepository | None = None,
        product_answer_service: ProductAnswerService | None = None,
        qa_provider: Callable[[], Awaitable[QAService]] | None = None,
        product_resolver: ProductResolver | None = None,
    ) -> None:
        self._rules = rule_registry or default_rule_registry()
        self._conversations = conversation_repository
        self._products = product_repository
        self._product_answers = product_answer_service or ProductAnswerService()
        self._qa_provider = qa_provider
        self._product_resolver = product_resolver or ProductResolver()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        binding = await self._load_binding(request.conversation_id)
        rule_result = self._rules.evaluate(
            request.message,
            RuleContext(
                session_id=request.conversation_id,
                customer_id=request.customer_id,
                current_product_id=binding.product_id if binding else None,
                service_stage=request.service_stage,
                channel="unified_chat",
            ),
        )

        terminal = rule_result.terminal_decision
        if terminal is not None and terminal.fixed_reply:
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer=terminal.fixed_reply,
                source="rule",
                route=terminal.route,
                product_resolution=(
                    "request"
                    if request.product_id
                    else "conversation"
                    if binding is not None and binding.product_id
                    else "none"
                ),
                rule_name=terminal.rule_name,
                reason_code=terminal.reason_code,
                confidence=terminal.confidence,
            )

        resolution = self._product_resolver.resolve(
            request_product_id=request.product_id,
            message=request.message,
            conversation_product_id=binding.product_id if binding else None,
        )
        explicit_product = resolution.source in {"request", "message"}
        product_question = explicit_product or rule_result.requires_product

        if product_question and resolution.product_id is None:
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer="当前还没有识别到您咨询的具体商品，请提供商品信息或商品 ID。",
                source="product",
                route="product_missing",
                product_resolution="none",
                rule_name=self._matched_product_rule(rule_result),
            )

        if not product_question:
            return await self._answer_with_qa(request)

        assert self._products is not None
        assert resolution.product_id is not None
        try:
            product = await self._products.get_product_by_id(resolution.product_id)
        except ProductNotFoundError:
            await self._clear_binding_if_present(request.conversation_id)
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer="未找到对应商品，请确认商品信息后重试。",
                source="product",
                route="product_not_found",
                product_resolution=resolution.source,
                rule_name=self._matched_product_rule(rule_result),
            )
        except ProductLookupError as exc:
            raise ProductServiceUnavailableError() from exc

        await self._bind_product(request.conversation_id, product)
        try:
            answer = await self._product_answers.answer(
                request.message,
                product,
                conversation_id=request.conversation_id,
            )
        except AppException:
            raise
        except Exception as exc:
            raise LLMInvocationError() from exc
        return self._product_response(request, product, answer, resolution.source, rule_result)

    async def _load_binding(self, conversation_id: str | None) -> ChatConversationProduct | None:
        if not conversation_id or self._conversations is None:
            return None
        return await self._conversations.get_binding(conversation_id)

    async def _bind_product(self, conversation_id: str | None, product: ProductProfile) -> None:
        if not conversation_id or self._conversations is None:
            return
        await self._conversations.bind_product(
            conversation_id,
            product_id=product.id,
            product_name=product.name,
        )

    async def _clear_binding_if_present(self, conversation_id: str | None) -> None:
        if not conversation_id or self._conversations is None:
            return
        await self._conversations.clear_binding(conversation_id)

    async def _answer_with_qa(self, request: ChatRequest) -> ChatResponse:
        if self._qa_provider is None:
            raise RuntimeError("QA provider is not configured")
        qa_service = await self._qa_provider()
        result: QAResult = await qa_service.answer(
            request.message,
            service_stage=request.service_stage,
        )
        return ChatResponse(
            conversation_id=request.conversation_id,
            answer=result.answer,
            source="qa",
            route=result.route,
            product_resolution="none",
            confidence=result.confidence,
            sources=[
                ChatSourceView(
                    chunk_id=source.chunk_id,
                    title=source.title,
                    source=source.source,
                    score=source.score,
                )
                for source in result.sources
            ],
        )

    def _product_response(
        self,
        request: ChatRequest,
        product: ProductProfile,
        answer: ProductAnswer,
        resolution_source: str,
        rule_result: Any,
    ) -> ChatResponse:
        return ChatResponse(
            conversation_id=request.conversation_id,
            answer=answer.answer,
            source="product",
            route="product",
            product=ChatProductView(id=product.id, name=product.name),
            product_resolution=resolution_source,
            rule_name=self._matched_product_rule(rule_result),
            confidence=answer.confidence,
        )

    @staticmethod
    def _matched_product_rule(rule_result: Any) -> str | None:
        for decision in rule_result.decisions:
            if decision.requires_product:
                return decision.rule_name
        return None
