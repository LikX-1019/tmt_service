"""客服控制台 HTTP 与 SSE 接口。"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from typing import Annotated, AsyncIterator, Literal

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_console_runtime
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
    OutboundJobView,
    ReplyRequest,
)
from app.services.console_runtime import ConsoleRuntime


router = APIRouter(tags=["console"])
Runtime = Annotated[ConsoleRuntime, Depends(get_console_runtime)]


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


def _sse(event: dict[str, object]) -> str:
    data = json.dumps(
        {"code": 0, "message": "success", "data": event["payload"]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f'id: {event["id"]}\nevent: {event["event_type"]}\ndata: {data}\n\n'


@router.get("/events", response_class=StreamingResponse)
async def events(
    runtime: Runtime,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    cursor: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    # 必须在响应头发出前验证数据库，否则流内异常无法再转换为统一 JSON 错误。
    await runtime.ensure_initialized()
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
                else await runtime.repository.latest_event_id()
            )
            while True:
                replay = await runtime.repository.events_after(last_sent)
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
                    if event_id > last_sent:
                        last_sent = event_id
                        yield _sse(event)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
