"""Unified Chat 商品解析、加载与回答节点。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent.dependencies import AgentCapabilities
from app.agent.constants import PRODUCT_LOAD_NODE, RAG_CONTEXT_NODE
from app.agent.state import (
    AgentProductCandidateState,
    AgentReplyState,
    AgentState,
    HumanState,
    ProductReference,
    ProductResolutionState,
    ResolvedProductContext,
    StatePatch,
)
from app.core.exceptions import ProductServiceUnavailableError
from app.core.exceptions import AppException, LLMInvocationError
from app.services.product_resolver import (
    ConversationProductReference,
    ProductResolution,
    ProductResolver,
)
from app.services.product_service import (
    ProductAnswer,
    ProductLookupError,
    ProductNotFoundError,
    ProductProfile,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _refs(state: AgentState) -> list[ConversationProductReference]:
    if state.turn is None:
        return []
    return [
        ConversationProductReference(
            product_id=item.product_id, product_name=item.product_name
        )
        for item in state.turn.product.recent_products
    ]


def _terminal_product_patch(
    state: AgentState,
    *,
    status: str,
    route: str,
    answer: str,
    resolution_source: str,
    reason_code: str | None = None,
    products: list[AgentProductCandidateState] | None = None,
) -> StatePatch:
    """生成商品解析终态：不再进入 ProductLoad。"""
    return StatePatch(
        product=ProductResolutionState(
            reference=state.turn.product.reference if state.turn else None,
            recent_products=state.turn.product.recent_products if state.turn else [],
            status=status,  # type: ignore[arg-type]
            resolution_source=resolution_source,  # type: ignore[arg-type]
            requires_product=state.turn.product.requires_product
            if state.turn
            else False,
            product_rule_name=state.turn.product.product_rule_name
            if state.turn
            else None,
            answer_required=False,
            error_code=route,
        ),
        route=route,  # type: ignore[arg-type]
        final_answer=answer,
        reply=AgentReplyState(
            source="product_selection" if status == "selection_required" else "product",
            route=route,
            answer=answer,
            product_resolution=resolution_source,  # type: ignore[arg-type]
            rule_name=state.turn.product.product_rule_name if state.turn else None,
            reason_code=reason_code,
            products=products or [],
        ),
        next_node="response",
    )


def _pdd_product_handoff_patch(
    state: AgentState,
    *,
    answer: str,
    reason_code: str,
) -> StatePatch:
    """把 PDD Legacy 商品安全失败表达为 Graph Human workflow。"""
    turn = state.turn
    product_id = (
        state.session.current_product.product_id
        if state.session and state.session.current_product
        else None
    )
    product_name = next(
        (
            item.product_name
            for item in turn.product.recent_products
            if item.product_id == product_id
        ),
        None,
    ) if turn else None
    return StatePatch(
        product=ProductResolutionState(
            reference=turn.product.reference if turn else None,
            recent_products=turn.product.recent_products if turn else [],
            status="failed" if product_id else "missing",
            resolution_source="conversation" if product_id else "none",
            requires_product=True,
            product_rule_name=turn.product.product_rule_name if turn else None,
            answer_required=False,
            error_code=reason_code,
        ),
        route="human",
        risk_level="high",
        risk_reasons=[reason_code],
        final_answer=answer,
        human=HumanState(
            required=True,
            status="requested",
            reason_code=reason_code,
            reason=answer,
        ),
        reply=AgentReplyState(
            source="product",
            route="human",
            answer=answer,
            product_id=product_id,
            product_name=product_name,
            product_resolution="conversation" if product_id else "none",
            reason_code=reason_code,
        ),
        next_node="human_transfer",
    )


class ProductResolveNode:
    """调用 ProductResolver / SemanticProductResolver，控制商品分支。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        session, turn = state.session, state.turn
        if session is None or turn is None:
            raise ValueError("ProductResolveNode 前必须完成 hydration")

        recent = _refs(state)
        if session.channel == "pdd":
            if session.current_product is None:
                if turn.product.requires_product:
                    return _pdd_product_handoff_patch(
                        state,
                        answer="商品咨询缺少可确认的商品卡片",
                        reason_code="PRODUCT_CONTEXT_MISSING",
                    )
                return StatePatch(
                    product=ProductResolutionState(
                        recent_products=turn.product.recent_products,
                        status="not_required",
                        requires_product=False,
                    ),
                    next_node=RAG_CONTEXT_NODE,
                )
            return StatePatch(
                current_product=session.current_product,
                product=ProductResolutionState(
                    reference=session.current_product,
                    recent_products=turn.product.recent_products,
                    status="pending",
                    resolution_source="conversation",
                    requires_product=True,
                    product_rule_name=turn.product.product_rule_name,
                    answer_required=True,
                ),
                next_node=PRODUCT_LOAD_NODE,
            )
        request_product_id = (
            turn.product.reference.product_id
            if turn.product.reference is not None
            and turn.product.resolution_source == "request"
            else None
        )
        resolution = self._capabilities.product_resolver.resolve(
            request_product_id=request_product_id,
            message=turn.original_query,
            conversation_product_id=(
                session.current_product.product_id
                if session.current_product is not None
                else None
            ),
            recent_products=recent,
        )

        if (
            resolution.source == "conversation"
            and ProductResolver.has_historical_reference(turn.original_query)
            and recent
        ):
            semantic = await self._capabilities.semantic_products.resolve(
                turn.original_query,
                candidates=recent,
                current_product_id=(
                    session.current_product.product_id
                    if session.current_product is not None
                    else None
                ),
            )
            if semantic.ambiguous:
                return _terminal_product_patch(
                    state,
                    status="selection_required",
                    route="product_selection",
                    answer="您提到了多个商品，请告诉我具体想咨询哪一个。",
                    resolution_source="history_semantic",
                    reason_code=semantic.reason_code,
                )
            if semantic.matched and semantic.product_id:
                resolution = ProductResolution(
                    semantic.product_id,
                    "history_semantic",
                )

        explicit = resolution.source in {
            "request",
            "url",
            "message_id",
            "history",
            "history_semantic",
        }
        keep_bound = (
            resolution.source == "conversation"
            and session.current_product is not None
            and turn.product.requires_product
        )
        name_query = None
        if not explicit and not keep_bound:
            name_query = self._capabilities.product_resolver.extract_name_query(
                turn.original_query,
                product_intent=turn.product.requires_product,
            )

        if name_query is not None:
            if self._capabilities.products is None:
                raise ValueError("商品名称解析需要 ProductRepository")
            try:
                candidates = await self._capabilities.products.search_products_by_name(
                    name_query,
                    limit=5,
                )
            except ProductLookupError as exc:
                raise ProductServiceUnavailableError() from exc
            if not candidates:
                return _terminal_product_patch(
                    state,
                    status="not_found",
                    route="product_not_found",
                    answer="没有找到对应商品，请检查商品名称后重试。",
                    resolution_source="name_candidates",
                )
            exact = [item for item in candidates if item.match_type == "exact"]
            if len(exact) == 1:
                selected, source = exact[0], "name_exact"
            elif not exact and len(candidates) == 1:
                selected, source = candidates[0], "name_unique_contains"
            else:
                return _terminal_product_patch(
                    state,
                    status="selection_required",
                    route="product_selection",
                    answer="找到多个可能的商品，请选择您咨询的商品。",
                    resolution_source="name_candidates",
                    products=[
                        AgentProductCandidateState(
                            id=item.id,
                            name=item.name,
                            summary=item.summary,
                            internal_code=item.internal_code,
                            specifications=item.specifications,
                        )
                        for item in candidates[:5]
                    ],
                )
            resolution = ProductResolution(selected.id, source)

        if not explicit and self._capabilities.product_resolver.has_url(
            turn.original_query
        ):
            invalid = self._capabilities.product_resolver.has_unresolved_known_url(
                turn.original_query
            )
            return _terminal_product_patch(
                state,
                status="invalid_link" if invalid else "unsupported_link",
                route=(
                    "product_link_invalid" if invalid else "product_link_unsupported"
                ),
                answer="无法从该链接确认商品，请提供当前系统的商品详情链接或商品名称。",
                resolution_source="none",
            )

        product_question = explicit or turn.product.requires_product
        if product_question and resolution.product_id is None:
            return _terminal_product_patch(
                state,
                status="missing",
                route="product_missing",
                answer="当前还没有识别到您咨询的具体商品，请提供商品信息或商品 ID。",
                resolution_source="none",
            )
        if resolution.product_id is None:
            return StatePatch(
                product=ProductResolutionState(
                    recent_products=state.turn.product.recent_products
                    if state.turn
                    else [],
                    status="not_required",
                    requires_product=turn.product.requires_product,
                    product_rule_name=turn.product.product_rule_name,
                    answer_required=False,
                ),
                next_node="rag_context",
            )
        source = (
            turn.product.reference.source
            if turn.product.reference is not None
            and turn.product.reference.product_id == resolution.product_id
            else "message_extraction"
        )
        return StatePatch(
            product=ProductResolutionState(
                reference=ProductReference(
                    product_id=resolution.product_id,
                    source="manual" if resolution.source == "conversation" else source,
                ),
                recent_products=state.turn.product.recent_products
                if state.turn
                else [],
                status="pending",
                resolution_source=resolution.source,
                requires_product=turn.product.requires_product,
                product_rule_name=turn.product.product_rule_name,
                answer_required=product_question,
            ),
            next_node="product_load",
        )


class ProductLoadNode:
    """加载商品事实、写入当前 Turn snapshot，并保持 MySQL binding 行为。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        session, turn = state.session, state.turn
        if session is None or turn is None or self._capabilities.products is None:
            raise ValueError("ProductLoadNode 需要 Session/Turn 和 ProductRepository")
        reference = turn.product.reference
        if reference is None:
            raise ValueError("ProductLoadNode 前必须解析商品 ID")

        try:
            product = await self._capabilities.products.get_product_by_id(
                reference.product_id
            )
        except ProductNotFoundError:
            if session.channel == "pdd":
                return _pdd_product_handoff_patch(
                    state,
                    answer="商品资料不可用或与会话商品不一致",
                    reason_code="PRODUCT_DATA_MISSING",
                )
            if (
                turn.product.reference is not None
                and turn.product.reference.source == "manual"
            ):
                if (
                    state.conversation_id
                    and self._capabilities.conversations is not None
                ):
                    await self._capabilities.conversations.clear_binding(
                        state.conversation_id
                    )
                return StatePatch(
                    clear_current_product=True,
                    product=ProductResolutionState(
                        recent_products=turn.product.recent_products,
                        status="not_found",
                        error_code="product_not_found",
                    ),
                    route="product",
                    final_answer="未找到对应商品，请确认商品信息后重试。",
                    reply=AgentReplyState(
                        source="product",
                        route="product_not_found",
                        answer="未找到对应商品，请确认商品信息后重试。",
                        product_resolution="conversation",
                        rule_name=turn.product.product_rule_name,
                    ),
                    next_node="response",
                )
            return _terminal_product_patch(
                state,
                status="not_found",
                route="product_not_found",
                answer="未找到对应商品，请确认商品信息后重试。",
                resolution_source=turn.product.resolution_source,
            )
        except ProductLookupError as exc:
            if session.channel == "pdd":
                return _pdd_product_handoff_patch(
                    state,
                    answer="商品资料不可用或与会话商品不一致",
                    reason_code="PRODUCT_DATA_MISSING",
                )
            raise ProductServiceUnavailableError() from exc

        if (
            session.channel != "pdd"
            and state.conversation_id
            and self._capabilities.conversations is not None
        ):
            await self._capabilities.conversations.bind_product(
                state.conversation_id,
                product_id=product.id,
                product_name=product.name,
                recent_products=[
                    {"product_id": item.product_id, "product_name": item.product_name}
                    for item in turn.product.recent_products
                ],
            )

        context = ResolvedProductContext(
            product_id=product.id,
            name=product.name,
            category=product.category_name,
            summary=product.summary,
            selling_points=product.selling_points,
            specifications=product.specifications,
            usage=[item for item in (product.usage or "").splitlines() if item],
            suitable_for=[
                item for item in (product.suitable_for or "").splitlines() if item
            ],
            warnings=[item for item in (product.warnings or "").splitlines() if item],
            after_sales_limits=[
                item for item in (product.after_sales_limits or "").splitlines() if item
            ],
            source_updated_at=None,
            resolved_at=_now(),
        )
        return StatePatch(
            current_product=ProductReference(product_id=product.id, source="manual"),
            product=ProductResolutionState(
                reference=reference,
                recent_products=turn.product.recent_products,
                status="resolved",
                context=context,
                profile=product.model_dump(mode="json"),
                resolution_source=turn.product.resolution_source,
                requires_product=turn.product.requires_product,
                product_rule_name=turn.product.product_rule_name,
                answer_required=turn.product.answer_required,
            ),
            next_node=(
                "product_answer" if turn.product.answer_required else "fallback"
            ),
        )


class ProductAnswerNode:
    """只调用 ProductAnswerService.answer，不处理 QA/Social/Fallback。"""

    def __init__(self, capabilities: AgentCapabilities) -> None:
        self._capabilities = capabilities

    async def __call__(self, state: AgentState) -> StatePatch:
        turn = state.turn
        if turn is None or turn.product.context is None:
            raise ValueError("ProductAnswerNode 前必须加载商品事实")
        profile = ProductProfile.model_validate(turn.product.profile)
        try:
            result: ProductAnswer = await self._capabilities.product_answers.answer(
                turn.original_query,
                profile,
                conversation_id=state.conversation_id,
            )
        except AppException:
            if state.session and state.session.channel == "pdd":
                return _pdd_product_handoff_patch(
                    state,
                    answer="商品咨询暂无法安全回答，已转人工",
                    reason_code="PRODUCT_ANSWER_UNAVAILABLE",
                )
            raise
        except Exception as exc:
            if state.session and state.session.channel == "pdd":
                return _pdd_product_handoff_patch(
                    state,
                    answer="商品咨询暂无法安全回答，已转人工",
                    reason_code="PRODUCT_ANSWER_UNAVAILABLE",
                )
            raise LLMInvocationError() from exc
        context = turn.product.context
        return StatePatch(
            route="product",
            final_answer=result.answer,
            reply=AgentReplyState(
                source="product",
                route="product",
                answer=result.answer,
                product_id=context.product_id,
                product_name=context.name,
                product_resolution=turn.product.resolution_source,
                rule_name=turn.product.product_rule_name,
                confidence=result.confidence,
                facts_supported=result.facts_supported,
                contains_sensitive_or_after_sales=(
                    result.contains_sensitive_or_after_sales
                ),
                needs_clarification=result.needs_clarification,
            ),
            next_node="response",
        )


product_resolve_node = ProductResolveNode
product_load_node = ProductLoadNode
product_answer_node = ProductAnswerNode

__all__ = [
    "ProductAnswerNode",
    "ProductLoadNode",
    "ProductResolveNode",
    "product_answer_node",
    "product_load_node",
    "product_resolve_node",
]
