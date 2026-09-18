"""客服控制台 HTTP 与 SSE 接口。"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Annotated, AsyncIterator, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_console_runtime, get_shop_runtime_manager
from app.schemas.common import ApiResponse
from app.schemas.console import (
    AutomationData,
    AutomationUpdate,
    ConnectorStatusData,
    ConversationListData,
    ConversationMessagesData,
    ConversationView,
    CustomerListData,
    CustomerMessagesData,
    GreetingAutomationData,
    GreetingAutomationUpdate,
    HandoffAutomationData,
    HandoffAutomationUpdate,
    CustomerNoteData,
    CustomerNoteUpdate,
    KnowledgeGapDraftRequest,
    KnowledgeGapListData,
    KnowledgeGapUpdate,
    KnowledgeGapView,
    OutboundJobView,
    ReplyRequest,
    ShopSummaryView,
    ShopListData,
    ShopStatsData,
    ShopUpdate,
    ShopView,
)
from app.services.console_runtime import ConsoleRuntime
from app.services.shop_runtime_manager import ShopRuntimeManager


router = APIRouter(tags=["console"])
Runtime = Annotated[ConsoleRuntime, Depends(get_console_runtime)]
Manager = Annotated[ShopRuntimeManager, Depends(get_shop_runtime_manager)]


@router.get("/shops", response_model=ApiResponse[ShopListData])
async def list_shops(manager: Manager) -> ApiResponse[ShopListData]:
    return ApiResponse(data=ShopListData(items=await manager.list_shops()))


@router.post(
    "/shops/provision", response_model=ApiResponse[ShopView], status_code=201
)
async def provision_shop(manager: Manager) -> ApiResponse[ShopView]:
    return ApiResponse(data=ShopView(**await manager.provision()))


@router.get("/shops/{shop_id}", response_model=ApiResponse[ShopView])
async def get_shop(shop_id: str, manager: Manager) -> ApiResponse[ShopView]:
    return ApiResponse(data=ShopView(**await manager.get_shop(shop_id)))


@router.patch("/shops/{shop_id}", response_model=ApiResponse[ShopView])
async def update_shop(
    shop_id: str, request: ShopUpdate, manager: Manager
) -> ApiResponse[ShopView]:
    if request.enabled is False:
        await manager.disable(shop_id)
    elif request.enabled is True:
        await manager.repository.update_shop_state(
            shop_id, lifecycle_status="active", enabled=True
        )
    if request.reception_mode is not None:
        runtime = await manager.get_runtime(shop_id)
        await runtime.set_reception_mode(request.reception_mode)
    return ApiResponse(data=ShopView(**await manager.get_shop(shop_id)))


@router.post(
    "/shops/{shop_id}/connector/start",
    response_model=ApiResponse[ConnectorStatusData],
)
async def start_shop_connector(
    shop_id: str, manager: Manager
) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await manager.start(shop_id)))


@router.post(
    "/shops/{shop_id}/connector/stop",
    response_model=ApiResponse[ConnectorStatusData],
)
async def stop_shop_connector(
    shop_id: str, manager: Manager
) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await manager.stop(shop_id)))


@router.post(
    "/shops/{shop_id}/connector/focus",
    response_model=ApiResponse[ConnectorStatusData],
)
async def focus_shop_connector(
    shop_id: str, manager: Manager
) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await manager.focus(shop_id)))


@router.get(
    "/shops/{shop_id}/connector/status",
    response_model=ApiResponse[ConnectorStatusData],
)
async def shop_connector_status(
    shop_id: str, manager: Manager
) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await manager.status(shop_id)))


@router.get("/shop", response_model=ApiResponse[ShopSummaryView])
async def shop_summary(runtime: Runtime) -> ApiResponse[ShopSummaryView]:
    return ApiResponse(data=ShopSummaryView(**await runtime.shop_summary()))


@router.get("/customers", response_model=ApiResponse[CustomerListData])
async def list_customers(
    runtime: Runtime,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> ApiResponse[CustomerListData]:
    items = await runtime.list_customers(limit=limit, search=search)
    return ApiResponse(data=CustomerListData(items=items))


@router.get(
    "/customers/{customer_id}/messages",
    response_model=ApiResponse[CustomerMessagesData],
)
async def customer_messages(
    customer_id: str,
    runtime: Runtime,
    day: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ApiResponse[CustomerMessagesData]:
    items = await runtime.customer_messages(
        customer_id, message_date=day, limit=limit
    )
    return ApiResponse(
        data=CustomerMessagesData(customer_id=customer_id, day=day, items=items)
    )


@router.get("/conversations", response_model=ApiResponse[ConversationListData])
async def list_conversations(
    runtime: Runtime,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    state: Literal["pending", "manual", "auto_replied"] | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    before: datetime | None = None,
) -> ApiResponse[ConversationListData]:
    items = await runtime.list_conversations(
        limit=limit, state=state, search=search, before=before
    )
    cursor = items[-1].get("last_message_at") if len(items) == limit else None
    return ApiResponse(data=ConversationListData(items=items, next_cursor=cursor))


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ApiResponse[ConversationMessagesData],
)
async def conversation_messages(
    conversation_id: str,
    runtime: Runtime,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    before: datetime | None = None,
) -> ApiResponse[ConversationMessagesData]:
    detail = await runtime.conversation_detail(
        conversation_id, limit=limit, before=before
    )
    messages = detail["messages"]
    cursor = messages[0].get("occurred_at") if len(messages) == limit else None
    return ApiResponse(data=ConversationMessagesData(**detail, next_cursor=cursor))


@router.post(
    "/conversations/{conversation_id}/read",
    response_model=ApiResponse[ConversationView],
)
async def mark_conversation_read(
    conversation_id: str, runtime: Runtime
) -> ApiResponse[ConversationView]:
    item = await runtime.mark_conversation_read(conversation_id)
    return ApiResponse(data=ConversationView(**item))


@router.delete(
    "/conversations/{conversation_id}/response-timer",
    response_model=ApiResponse[ConversationView],
)
async def clear_conversation_response_timer(
    conversation_id: str, runtime: Runtime
) -> ApiResponse[ConversationView]:
    item = await runtime.clear_conversation_response_timer(conversation_id)
    return ApiResponse(data=ConversationView(**item))


@router.post(
    "/conversations/{conversation_id}/reply",
    response_model=ApiResponse[OutboundJobView],
    status_code=202,
)
async def reply(
    conversation_id: str, request: ReplyRequest, runtime: Runtime
) -> ApiResponse[OutboundJobView]:
    job = await runtime.reply(
        conversation_id,
        content=request.content,
        client_request_id=request.client_request_id,
    )
    return ApiResponse(data=OutboundJobView(**job))


@router.put(
    "/conversations/{conversation_id}/automation",
    response_model=ApiResponse[ConversationView],
)
async def update_conversation_automation(
    conversation_id: str, request: AutomationUpdate, runtime: Runtime
) -> ApiResponse[ConversationView]:
    item = await runtime.set_conversation_automation(conversation_id, request.enabled)
    return ApiResponse(data=ConversationView(**item))


@router.get("/connector/status", response_model=ApiResponse[ConnectorStatusData])
async def connector_status(runtime: Runtime) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await runtime.connector_status()))


@router.get("/message-assets/{asset_id}", response_class=FileResponse)
async def message_asset(asset_id: str, runtime: Runtime) -> FileResponse:
    path, media_type = await runtime.message_asset(asset_id)
    return FileResponse(path, media_type=media_type)


@router.get("/connector/diagnostics", response_model=ApiResponse[dict[str, object]])
async def connector_diagnostics(
    runtime: Runtime,
) -> ApiResponse[dict[str, object]]:
    return ApiResponse(data=await runtime.connector_diagnostics())


@router.post("/connector/start", response_model=ApiResponse[ConnectorStatusData])
async def connector_start(runtime: Runtime) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await runtime.start_connector()))


@router.post("/connector/stop", response_model=ApiResponse[ConnectorStatusData])
async def connector_stop(runtime: Runtime) -> ApiResponse[ConnectorStatusData]:
    return ApiResponse(data=ConnectorStatusData(**await runtime.stop_connector()))


@router.get("/automation", response_model=ApiResponse[AutomationData])
async def get_automation(runtime: Runtime) -> ApiResponse[AutomationData]:
    return ApiResponse(data=AutomationData(**await runtime.automation()))


@router.put("/automation", response_model=ApiResponse[AutomationData])
async def update_automation(
    request: AutomationUpdate, runtime: Runtime
) -> ApiResponse[AutomationData]:
    return ApiResponse(data=AutomationData(**await runtime.set_automation(request.enabled)))


@router.get(
    "/automation/greeting",
    response_model=ApiResponse[GreetingAutomationData],
)
async def get_greeting_automation(
    runtime: Runtime,
) -> ApiResponse[GreetingAutomationData]:
    return ApiResponse(
        data=GreetingAutomationData(**await runtime.greeting_automation())
    )


@router.put(
    "/automation/greeting",
    response_model=ApiResponse[GreetingAutomationData],
)
async def update_greeting_automation(
    request: GreetingAutomationUpdate, runtime: Runtime
) -> ApiResponse[GreetingAutomationData]:
    groups = request.trigger_groups.model_dump()
    templates = request.reply_templates.model_dump()
    result = await runtime.set_greeting_automation(
        enabled=request.enabled,
        trigger_groups=groups,
        reply_templates=templates,
    )
    return ApiResponse(data=GreetingAutomationData(**result))


@router.get(
    "/automation/handoff",
    response_model=ApiResponse[HandoffAutomationData],
)
async def get_handoff_automation(
    runtime: Runtime,
) -> ApiResponse[HandoffAutomationData]:
    return ApiResponse(data=HandoffAutomationData(**await runtime.handoff_automation()))


@router.put(
    "/automation/handoff",
    response_model=ApiResponse[HandoffAutomationData],
)
async def update_handoff_automation(
    request: HandoffAutomationUpdate, runtime: Runtime
) -> ApiResponse[HandoffAutomationData]:
    result = await runtime.set_handoff_automation(request.reply_template)
    return ApiResponse(data=HandoffAutomationData(**result))


@router.get(
    "/shops/{shop_id}/conversations",
    response_model=ApiResponse[ConversationListData],
)
async def scoped_list_conversations(
    shop_id: str,
    manager: Manager,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    state: Literal["pending", "manual", "auto_replied"] | None = None,
    search: Annotated[str | None, Query(max_length=100)] = None,
    before: datetime | None = None,
) -> ApiResponse[ConversationListData]:
    runtime = await manager.get_runtime(shop_id)
    return await list_conversations(runtime, limit, state, search, before)


@router.get(
    "/shops/{shop_id}/conversations/{conversation_id}/messages",
    response_model=ApiResponse[ConversationMessagesData],
)
async def scoped_conversation_messages(
    shop_id: str,
    conversation_id: str,
    manager: Manager,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    before: datetime | None = None,
) -> ApiResponse[ConversationMessagesData]:
    runtime = await manager.get_runtime(shop_id)
    return await conversation_messages(conversation_id, runtime, limit, before)


@router.post(
    "/shops/{shop_id}/conversations/{conversation_id}/read",
    response_model=ApiResponse[ConversationView],
)
async def scoped_mark_conversation_read(
    shop_id: str, conversation_id: str, manager: Manager
) -> ApiResponse[ConversationView]:
    return await mark_conversation_read(
        conversation_id, await manager.get_runtime(shop_id)
    )


@router.delete(
    "/shops/{shop_id}/conversations/{conversation_id}/response-timer",
    response_model=ApiResponse[ConversationView],
)
async def scoped_clear_response_timer(
    shop_id: str, conversation_id: str, manager: Manager
) -> ApiResponse[ConversationView]:
    return await clear_conversation_response_timer(
        conversation_id, await manager.get_runtime(shop_id)
    )


@router.post(
    "/shops/{shop_id}/conversations/{conversation_id}/reply",
    response_model=ApiResponse[OutboundJobView],
    status_code=202,
)
async def scoped_reply(
    shop_id: str,
    conversation_id: str,
    request: ReplyRequest,
    manager: Manager,
) -> ApiResponse[OutboundJobView]:
    return await reply(
        conversation_id, request, await manager.get_runtime(shop_id)
    )


@router.put(
    "/shops/{shop_id}/conversations/{conversation_id}/automation",
    response_model=ApiResponse[ConversationView],
)
async def scoped_conversation_automation(
    shop_id: str,
    conversation_id: str,
    request: AutomationUpdate,
    manager: Manager,
) -> ApiResponse[ConversationView]:
    return await update_conversation_automation(
        conversation_id, request, await manager.get_runtime(shop_id)
    )


@router.get(
    "/shops/{shop_id}/customers", response_model=ApiResponse[CustomerListData]
)
async def scoped_list_customers(
    shop_id: str,
    manager: Manager,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    search: Annotated[str | None, Query(max_length=100)] = None,
) -> ApiResponse[CustomerListData]:
    return await list_customers(await manager.get_runtime(shop_id), limit, search)


@router.get(
    "/shops/{shop_id}/customers/{customer_id}/messages",
    response_model=ApiResponse[CustomerMessagesData],
)
async def scoped_customer_messages(
    shop_id: str,
    customer_id: str,
    manager: Manager,
    day: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> ApiResponse[CustomerMessagesData]:
    return await customer_messages(
        customer_id, await manager.get_runtime(shop_id), day, limit
    )


@router.get(
    "/shops/{shop_id}/customers/{customer_id}/note",
    response_model=ApiResponse[CustomerNoteData | None],
)
async def get_customer_note(
    shop_id: str, customer_id: str, manager: Manager
) -> ApiResponse[CustomerNoteData | None]:
    note = await (await manager.get_runtime(shop_id)).customer_note(customer_id)
    return ApiResponse(data=CustomerNoteData(**note) if note else None)


@router.put(
    "/shops/{shop_id}/customers/{customer_id}/note",
    response_model=ApiResponse[CustomerNoteData],
)
async def update_customer_note(
    shop_id: str,
    customer_id: str,
    request: CustomerNoteUpdate,
    manager: Manager,
) -> ApiResponse[CustomerNoteData]:
    note = await (await manager.get_runtime(shop_id)).set_customer_note(
        customer_id,
        content=request.content,
        tags=request.tags,
        pinned=request.pinned,
    )
    return ApiResponse(data=CustomerNoteData(**note))


@router.get(
    "/shops/{shop_id}/automation", response_model=ApiResponse[AutomationData]
)
async def scoped_get_automation(
    shop_id: str, manager: Manager
) -> ApiResponse[AutomationData]:
    return await get_automation(await manager.get_runtime(shop_id))


@router.put(
    "/shops/{shop_id}/automation", response_model=ApiResponse[AutomationData]
)
async def scoped_update_automation(
    shop_id: str, request: AutomationUpdate, manager: Manager
) -> ApiResponse[AutomationData]:
    return await update_automation(request, await manager.get_runtime(shop_id))


@router.get(
    "/shops/{shop_id}/automation/greeting",
    response_model=ApiResponse[GreetingAutomationData],
)
async def scoped_get_greeting(
    shop_id: str, manager: Manager
) -> ApiResponse[GreetingAutomationData]:
    return await get_greeting_automation(await manager.get_runtime(shop_id))


@router.put(
    "/shops/{shop_id}/automation/greeting",
    response_model=ApiResponse[GreetingAutomationData],
)
async def scoped_update_greeting(
    shop_id: str,
    request: GreetingAutomationUpdate,
    manager: Manager,
) -> ApiResponse[GreetingAutomationData]:
    return await update_greeting_automation(
        request, await manager.get_runtime(shop_id)
    )


@router.get(
    "/shops/{shop_id}/automation/handoff",
    response_model=ApiResponse[HandoffAutomationData],
)
async def scoped_get_handoff(
    shop_id: str, manager: Manager
) -> ApiResponse[HandoffAutomationData]:
    return await get_handoff_automation(await manager.get_runtime(shop_id))


@router.put(
    "/shops/{shop_id}/automation/handoff",
    response_model=ApiResponse[HandoffAutomationData],
)
async def scoped_update_handoff(
    shop_id: str,
    request: HandoffAutomationUpdate,
    manager: Manager,
) -> ApiResponse[HandoffAutomationData]:
    return await update_handoff_automation(
        request, await manager.get_runtime(shop_id)
    )


@router.get(
    "/shops/{shop_id}/message-assets/{asset_id}", response_class=FileResponse
)
async def scoped_message_asset(
    shop_id: str, asset_id: str, manager: Manager
) -> FileResponse:
    return await message_asset(asset_id, await manager.get_runtime(shop_id))


@router.get(
    "/shops/{shop_id}/knowledge-gaps",
    response_model=ApiResponse[KnowledgeGapListData],
)
async def list_knowledge_gaps(
    shop_id: str,
    manager: Manager,
    status: Literal["open", "draft", "resolved", "dismissed"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> ApiResponse[KnowledgeGapListData]:
    items = await (await manager.get_runtime(shop_id)).knowledge_gaps(
        status=status, limit=limit
    )
    return ApiResponse(data=KnowledgeGapListData(items=items))


@router.patch(
    "/shops/{shop_id}/knowledge-gaps/{gap_id}",
    response_model=ApiResponse[KnowledgeGapView],
)
async def update_knowledge_gap(
    shop_id: str,
    gap_id: str,
    request: KnowledgeGapUpdate,
    manager: Manager,
) -> ApiResponse[KnowledgeGapView]:
    item = await (await manager.get_runtime(shop_id)).update_knowledge_gap(
        gap_id,
        status=request.status,
        candidate_answer=request.candidate_answer,
        linked_qa_code=request.linked_qa_code,
    )
    return ApiResponse(data=KnowledgeGapView(**item))


@router.post(
    "/shops/{shop_id}/knowledge-gaps/{gap_id}/qa-draft",
    response_model=ApiResponse[KnowledgeGapView],
    status_code=201,
)
async def create_knowledge_gap_qa_draft(
    shop_id: str,
    gap_id: str,
    request: KnowledgeGapDraftRequest,
    manager: Manager,
) -> ApiResponse[KnowledgeGapView]:
    item = await (await manager.get_runtime(shop_id)).create_knowledge_gap_qa_draft(
        gap_id, standard_answer=request.standard_answer
    )
    return ApiResponse(data=KnowledgeGapView(**item))


@router.get(
    "/shops/{shop_id}/stats", response_model=ApiResponse[ShopStatsData]
)
async def shop_stats(
    shop_id: str,
    manager: Manager,
    days: Annotated[int, Query(ge=1, le=365)] = 7,
) -> ApiResponse[ShopStatsData]:
    return ApiResponse(
        data=ShopStatsData(
            **await (await manager.get_runtime(shop_id)).stats(days=days)
        )
    )


def _sse(event: dict[str, object]) -> str:
    data = json.dumps(
        {"code": 0, "message": "success", "data": event["payload"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f'id: {event["id"]}\nevent: {event["event_type"]}\ndata: {data}\n\n'


@router.get("/events", response_class=StreamingResponse)
async def events(
    runtime: Manager,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    return await _event_stream_response(
        runtime, shop_id=None, last_event_id=last_event_id, cursor=cursor
    )


@router.get("/shops/{shop_id}/events", response_class=StreamingResponse)
async def scoped_events(
    shop_id: str,
    manager: Manager,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    await manager.get_runtime(shop_id)
    return await _event_stream_response(
        manager, shop_id=shop_id, last_event_id=last_event_id, cursor=cursor
    )


async def _event_stream_response(
    runtime: ShopRuntimeManager | ConsoleRuntime,
    *,
    shop_id: str | None,
    last_event_id: str | None,
    cursor: int | None,
) -> StreamingResponse:
    # 必须在响应头发出前验证数据库，否则流内异常无法再转换为统一 JSON 错误。
    initialize = getattr(runtime, "ensure_initialized", None)
    if initialize is None:
        initialize = runtime.initialize
    await initialize()
    requested_cursor = last_event_id if last_event_id is not None else cursor
    try:
        replay_after = int(requested_cursor) if requested_cursor is not None else None
    except ValueError:
        replay_after = None

    async def stream() -> AsyncIterator[str]:
        async with runtime.broker.subscribe() as queue:
            last_sent = (
                replay_after
                if replay_after is not None
                else (
                    await runtime.repository.latest_event_id()
                    if shop_id is None
                    else await runtime.repository.latest_event_id(shop_id)
                )
            )
            while True:
                replay = (
                    await runtime.repository.events_after(last_sent)
                    if shop_id is None
                    else await runtime.repository.events_after(
                        last_sent, shop_id=shop_id
                    )
                )
                for event in replay:
                    last_sent = max(last_sent, int(event["id"]))
                    yield _sse(event)
                if len(replay) < 200:
                    break
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                else:
                    event_id = int(event["id"])
                    payload = event.get("payload") or {}
                    matches_shop = shop_id is None or (
                        isinstance(payload, dict)
                        and payload.get("shop_id") == shop_id
                    )
                    if event_id > last_sent and matches_shop:
                        last_sent = event_id
                        yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
