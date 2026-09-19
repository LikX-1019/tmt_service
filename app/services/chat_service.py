"""统一客服聊天编排：规则优先，商品事实与通用 QA 分流。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from app.agent.chat_runtime import ChatStateRuntime
from app.agent.state import AgentState
from app.core.exceptions import AppException, LLMInvocationError, ProductServiceUnavailableError
from app.models.chat import ChatConversationProduct
from app.qa.models import QAResult
from app.qa.service import QAService
from app.repositories.conversation_repository import ConversationProductRepository
from app.repositories.product_repository import ProductRepository
from app.rules.base import RuleContext
from app.rules.registry import RuleRegistry, default_rule_registry
from app.schemas.chat import (
    ChatProductCandidateView,
    ChatProductView,
    ChatRequest,
    ChatResponse,
    ChatSourceView,
)
from app.services.product_resolver import ConversationProductReference, ProductResolver
from app.services.social_router import SocialDecision, SocialRouter
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


def _social_response(
    request: ChatRequest,
    decision: SocialDecision,
    binding: ChatConversationProduct | None,
) -> ChatResponse:
    assert decision.intent in {"small_talk", "empathy"}
    assert decision.response is not None
    return ChatResponse(
        conversation_id=request.conversation_id,
        answer=decision.response,
        source="rule",
        route=decision.intent,
        product_resolution=(
            "conversation" if binding is not None and binding.product_id else "none"
        ),
        rule_name="SocialRouter",
        reason_code=decision.reason_code,
        confidence=decision.confidence,
    )


def _log_chat_response(message: str, response: ChatResponse) -> None:
    logger.info(
        "chat_response_completed",
        extra={
            "event": "chat_response_completed",
            "message_length": len(message),
            "route": response.route,
            "intent": response.route,
            "social_intent": (
                response.route
                if response.route in {"small_talk", "empathy"}
                else None
            ),
            "product_resolution": response.product_resolution,
            "resolved_product": response.product.id if response.product else None,
            "answer_source": response.source,
            "fallback_reason": (
                response.route if response.route == "fallback" else None
            ),
        },
    )


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
        state_runtime: ChatStateRuntime | None = None,
        social_router: SocialRouter | None = None,
    ) -> None:
        self._rules = rule_registry or default_rule_registry()
        self._conversations = conversation_repository
        self._products = product_repository
        self._product_answers = product_answer_service or ProductAnswerService()
        self._qa_provider = qa_provider
        self._product_resolver = product_resolver or ProductResolver()
        self._state_runtime = state_runtime or ChatStateRuntime()
        self._social_router = social_router or SocialRouter()

    async def chat(self, request: ChatRequest) -> ChatResponse:
        state = self._state_runtime.create_state(
            message=request.message,
            conversation_id=request.conversation_id,
            customer_id=request.customer_id,
            service_stage=request.service_stage,
        )
        try:
            await self._state_runtime.start(state)
            response = await self._route_chat(request, state)
            await self._state_runtime.complete(state, response)
            _log_chat_response(request.message, response)
            return response
        except Exception as exc:
            try:
                await self._state_runtime.fail(state, exc)
            except Exception:
                logger.exception(
                    "chat_state_failure_checkpoint_failed",
                    extra={
                        "event": "chat_state_failure_checkpoint_failed",
                        "run_id": state.run_id,
                    },
                )
            raise

    async def _route_chat(
        self,
        request: ChatRequest,
        state: AgentState,
    ) -> ChatResponse:
        binding = await self._load_binding(request.conversation_id)
        if binding is not None and binding.product_id:
            self._state_runtime.hydrate_product_binding(state, binding.product_id)
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
        self._state_runtime.record_rule(state, rule_result)

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

        social_decision = (
            await self._social_router.classify(request.message)
            if not rule_result.requires_product and not request.product_id
            else SocialDecision()
        )
        if social_decision.intent != "other" and social_decision.response:
            return _social_response(request, social_decision, binding)

        recent_products = self._recent_products(binding)
        resolution = self._product_resolver.resolve(
            request_product_id=request.product_id,
            message=request.message,
            conversation_product_id=binding.product_id if binding else None,
            recent_products=recent_products,
        )
        explicit_product = resolution.source in {"request", "url", "message_id", "history"}
        name_query = None
        keep_bound_product = (
            resolution.source == "conversation"
            and binding is not None
            and rule_result.requires_product
        )
        if not explicit_product and not keep_bound_product:
            name_query = self._product_resolver.extract_name_query(
                request.message,
                product_intent=rule_result.requires_product,
            )
        if name_query is not None:
            name_response = await self._resolve_product_name(
                request,
                name_query,
                rule_result,
                state,
                recent_products=recent_products,
            )
            if name_response is not None:
                return name_response

        if not explicit_product and self._product_resolver.has_url(request.message):
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer=(
                    "无法从该链接确认商品，请提供当前系统的商品详情链接或商品名称。"
                ),
                source="product",
                route=(
                    "product_link_invalid"
                    if self._product_resolver.has_unresolved_known_url(request.message)
                    else "product_link_unsupported"
                ),
                product_resolution="none",
                rule_name=self._matched_product_rule(rule_result),
            )

        product_question = explicit_product or rule_result.requires_product

        if product_question and resolution.product_id is not None:
            self._state_runtime.record_product_pending(
                state,
                product_id=resolution.product_id,
                source=resolution.source,
            )

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
            return await self._answer_with_qa(request, state)

        assert self._products is not None
        assert resolution.product_id is not None
        try:
            product = await self._products.get_product_by_id(resolution.product_id)
        except ProductNotFoundError:
            if resolution.source == "conversation":
                await self._clear_binding_if_present(request.conversation_id)
                self._state_runtime.clear_product_binding(state)
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer=(
                    "未找到对应商品，请确认商品链接。"
                    if resolution.source == "url"
                    else "未找到对应商品，请确认商品信息后重试。"
                ),
                source="product",
                route="product_not_found",
                product_resolution=resolution.source,
                rule_name=self._matched_product_rule(rule_result),
            )
        except ProductLookupError as exc:
            raise ProductServiceUnavailableError() from exc

        await self._bind_product(
            request.conversation_id,
            product,
            recent_products=recent_products,
        )
        self._state_runtime.record_product_resolved(
            state,
            product,
            source=resolution.source,
        )
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

    @staticmethod
    def _recent_products(
        binding: ChatConversationProduct | None,
    ) -> list[ConversationProductReference]:
        references: list[ConversationProductReference] = []
        seen: set[str] = set()

        def add(product_id: str | None, product_name: str | None) -> None:
            normalized_id = (product_id or "").strip()
            normalized_name = (product_name or "").strip()
            if not normalized_id or not normalized_name or normalized_id in seen:
                return
            seen.add(normalized_id)
            references.append(
                ConversationProductReference(
                    product_id=normalized_id,
                    product_name=normalized_name,
                )
            )

        if binding is not None:
            add(binding.product_id, binding.product_name)
            for product in getattr(binding, "recent_products", None) or []:
                if isinstance(product, dict):
                    add(product.get("product_id"), product.get("product_name"))
        return references[:5]

    async def _bind_product(
        self,
        conversation_id: str | None,
        product: ProductProfile,
        *,
        recent_products: Sequence[ConversationProductReference] | None = None,
    ) -> None:
        if not conversation_id or self._conversations is None:
            return
        await self._conversations.bind_product(
            conversation_id,
            product_id=product.id,
            product_name=product.name,
            recent_products=[
                {"product_id": item.product_id, "product_name": item.product_name}
                for item in (recent_products or [])
            ],
        )

    async def _clear_binding_if_present(self, conversation_id: str | None) -> None:
        if not conversation_id or self._conversations is None:
            return
        await self._conversations.clear_binding(conversation_id)

    async def _answer_with_qa(
        self,
        request: ChatRequest,
        state: AgentState,
    ) -> ChatResponse:
        if self._qa_provider is None:
            raise RuntimeError("QA provider is not configured")
        qa_service = await self._qa_provider()
        result: QAResult = await qa_service.answer(
            request.message,
            service_stage=request.service_stage,
        )
        self._state_runtime.record_qa(state, result)
        return ChatResponse(
            conversation_id=request.conversation_id,
            answer=result.answer,
            source="qa",
            route=result.route,
            product_resolution="none",
            confidence=result.confidence,
            qa_hit=result.route != "fallback",
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

    async def _resolve_product_name(
        self,
        request: ChatRequest,
        query: str,
        rule_result: Any,
        state: AgentState,
        *,
        recent_products: Sequence[ConversationProductReference] | None = None,
    ) -> ChatResponse | None:
        assert self._products is not None
        try:
            candidates = await self._products.search_products_by_name(query, limit=5)
        except ProductLookupError as exc:
            raise ProductServiceUnavailableError() from exc
        if not candidates:
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer="没有找到对应商品，请检查商品名称后重试。",
                source="product",
                route="product_not_found",
                product_resolution="none",
                rule_name=self._matched_product_rule(rule_result),
            )

        exact = [candidate for candidate in candidates if candidate.match_type == "exact"]
        if len(exact) == 1:
            selected = exact[0]
            resolution_source = "name_exact"
        elif not exact and len(candidates) == 1:
            selected = candidates[0]
            resolution_source = "name_unique_contains"
        else:
            selected = None
            resolution_source = "name_candidates"

        if selected is not None:
            try:
                product = await self._products.get_product_by_id(selected.id)
            except ProductNotFoundError:
                return ChatResponse(
                    conversation_id=request.conversation_id,
                    answer="没有找到对应商品，请检查商品名称后重试。",
                    source="product",
                    route="product_not_found",
                    product_resolution="none",
                    rule_name=self._matched_product_rule(rule_result),
                )
            except ProductLookupError as exc:
                raise ProductServiceUnavailableError() from exc
            await self._bind_product(
                request.conversation_id,
                product,
                recent_products=recent_products,
            )
            self._state_runtime.record_product_resolved(
                state,
                product,
                source=resolution_source,
            )
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
            return self._product_response(
                request,
                product,
                answer,
                resolution_source,
                rule_result,
            )

        return ChatResponse(
            conversation_id=request.conversation_id,
            answer="找到多个可能的商品，请选择您咨询的商品。",
            source="product_selection",
            route="product_selection",
            product_resolution=resolution_source,
            rule_name=self._matched_product_rule(rule_result),
            products=[
                ChatProductCandidateView(
                    id=candidate.id,
                    name=candidate.name,
                    summary=candidate.summary,
                    internal_code=candidate.internal_code,
                    specifications=candidate.specifications,
                )
                for candidate in candidates[:5]
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
