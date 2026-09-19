"""统一日志配置、跨天切换、链路上下文和脱敏测试。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.logging as logging_module
from app.api.dependencies import get_chat_service
from app.api.v1.chat import router as chat_router
from app.core.config import Settings
from app.core.logging import (
    configure_logging,
    create_log_task,
    get_log_file_paths,
    get_request_id,
    log_context,
)
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware
from app.schemas.chat import ChatResponse


MANAGED_LOGGERS = (
    "app.middleware.logging",
    "app.qa",
    "app.integrations.pdd",
    "app.services.console_runtime",
    "app.services.shop_runtime_manager",
    "app.audit",
)
EXPECTED_FILES = {
    "app.log",
    "error.log",
    "access.log",
    "qa.log",
    "connector.log",
    "audit.log",
}


@pytest.fixture
def isolated_logging(tmp_path: Path):
    """在临时目录中配置日志，并恢复 pytest/应用原有 handler。"""
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    saved_root_level = root.level
    root.handlers.clear()
    saved_children = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).level,
            logging.getLogger(name).propagate,
        )
        for name in MANAGED_LOGGERS
    }
    for name in MANAGED_LOGGERS:
        logging.getLogger(name).handlers.clear()

    yield tmp_path

    logging_module._reset_managed_handlers()
    root.handlers.clear()
    root.setLevel(saved_root_level)
    for handler in saved_root_handlers:
        root.addHandler(handler)
    for name, (handlers, level, propagate) in saved_children.items():
        child = logging.getLogger(name)
        child.handlers.clear()
        child.setLevel(level)
        child.propagate = propagate
        for handler in handlers:
            child.addHandler(handler)
    logging_module._log_files = None
    logging_module._coordinator = None
    logging_module._managed_file_handlers = []


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def records_with_event(path: Path, event: str) -> list[dict[str, Any]]:
    return [item for item in read_json_lines(path) if item.get("event") == event]


def test_logging_creates_daily_directory_and_all_files(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)

    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )

    paths = get_log_file_paths()
    assert paths is not None
    assert {path.name for path in paths.values()} == EXPECTED_FILES
    assert all(path.parent == isolated_logging / "2026-09-19" for path in paths.values())
    assert all(path.is_file() for path in paths.values())


def test_logging_rolls_all_files_at_midnight_without_restart(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 23, 59, 59, tzinfo=timezone.utc)
    state = {"now": now}

    def clock() -> datetime:
        return state["now"]

    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=clock,
    )
    logging.getLogger("app.test").info("before_midnight")

    coordinator = logging_module._coordinator
    assert coordinator is not None
    old_streams = {
        name: handler.stream
        for name, handler in coordinator.handlers.items()
    }
    old_contents = {
        path: path.read_text(encoding="utf-8")
        for path in (isolated_logging / "2026-09-19").glob("*.log")
    }

    state["now"] = now + timedelta(seconds=1)
    logging.getLogger("app.qa.service").info("after_midnight")

    new_dir = isolated_logging / "2026-09-20"
    assert {path.name for path in new_dir.iterdir()} == EXPECTED_FILES
    assert get_log_file_paths() is not None
    assert set(get_log_file_paths()) == EXPECTED_FILES
    assert all(path.parent == new_dir for path in get_log_file_paths().values())
    assert all(stream.closed for stream in old_streams.values())
    assert all(
        handler.stream is not None and handler.path is not None
        for handler in coordinator.handlers.values()
    )
    assert all(
        handler.path.parent == new_dir for handler in coordinator.handlers.values()
    )
    for old_path, old_content in old_contents.items():
        assert old_path.read_text(encoding="utf-8") == old_content
    before = read_json_lines(isolated_logging / "2026-09-19" / "app.log")
    after = read_json_lines(new_dir / "app.log")
    assert any(item["message"] == "before_midnight" for item in before)
    assert any(item["message"] == "after_midnight" for item in after)
    assert not any(item["message"] == "after_midnight" for item in before)


def test_error_routing_traceback_and_sensitive_values(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 11, 0, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )
    access_logger = logging.getLogger("app.middleware.logging")
    access_logger.info("access_completed", extra={"event": "access_completed"})
    qa_logger = logging.getLogger("app.qa.service")
    raw_password = f"pw-{uuid4().hex}"
    raw_bearer = f"bearer-{uuid4().hex}"
    raw_cookie = f"cookie-{uuid4().hex}"

    try:
        raise ValueError(f"failed password={raw_password}")
    except ValueError:
        qa_logger.exception(
            f"qa_failed password={raw_password}",
            extra={
                "event": "qa_failed",
                "password": raw_password,
                "headers": {
                    "Authorization": f"Bearer {raw_bearer}",
                    "Cookie": raw_cookie,
                },
            },
        )

    date_dir = isolated_logging / "2026-09-19"
    assert records_with_event(date_dir / "access.log", "access_completed")
    assert records_with_event(date_dir / "qa.log", "qa_failed")
    assert not records_with_event(date_dir / "error.log", "access_completed")

    app_errors = records_with_event(date_dir / "app.log", "qa_failed")
    error_errors = records_with_event(date_dir / "error.log", "qa_failed")
    assert app_errors and error_errors
    for record in (*app_errors, *error_errors):
        assert "event" not in record["fields"]
        assert "Traceback (most recent call last):" in record["traceback"]
        assert "ValueError" in record["traceback"]
        assert "test_error_routing_traceback_and_sensitive_values" in record["traceback"]

    for path in date_dir.glob("*.log"):
        content = path.read_text(encoding="utf-8")
        assert raw_password not in content
        assert raw_bearer not in content
        assert raw_cookie not in content
    for name in ("app.log", "error.log", "qa.log"):
        assert "[REDACTED]" in (date_dir / name).read_text(encoding="utf-8")


def test_logger_routes_without_duplicate_records_in_one_file(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 11, 15, tzinfo=timezone.utc)
    logging.getLogger("app.qa").propagate = False
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )
    cases = (
        ("app.general", "general_event", "app.log"),
        ("app.middleware.logging", "access_event", "access.log"),
        ("app.qa.service", "qa_event", "qa.log"),
        ("app.integrations.pdd.playwright_connector", "connector_event", "connector.log"),
        ("app.services.console_runtime", "console_connector_event", "connector.log"),
        ("app.services.shop_runtime_manager", "manager_connector_event", "connector.log"),
        ("app.audit.business", "audit_event", "audit.log"),
    )
    for logger_name, event, _ in cases:
        assert logging.getLogger(logger_name).propagate is True
        logging.getLogger(logger_name).info(event, extra={"event": event})

    date_dir = isolated_logging / "2026-09-19"
    for logger_name, event, dedicated_file in cases:
        # 分类文件写入一次；传播到 root 后 app.log 也写入一次，这是预期分流，
        # 不是同一文件重复写入。
        assert len(records_with_event(date_dir / dedicated_file, event)) == 1
        assert len(records_with_event(date_dir / "app.log", event)) == 1
        assert len(records_with_event(date_dir / "error.log", event)) == 0

    qa_handlers = [
        handler
        for handler in logging.getLogger("app.qa").handlers
        if isinstance(handler, logging_module.DailyDatedFileHandler)
    ]
    assert len(qa_handlers) == 1


def test_sensitive_values_are_redacted(isolated_logging: Path) -> None:
    now = datetime(2026, 9, 19, 11, 30, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )
    raw_password = f"pw-{uuid4().hex}"
    raw_api_key = f"key-{uuid4().hex}"
    raw_session = f"session-{uuid4().hex}"
    logging.getLogger("app.test").warning(
        f"login rejected password={raw_password}",
        extra={
            "password": raw_password,
            "api_key": raw_api_key,
            "nested": {"session": raw_session},
        },
    )
    content = (isolated_logging / "2026-09-19" / "app.log").read_text(
        encoding="utf-8"
    )
    assert raw_password not in content
    assert raw_api_key not in content
    assert raw_session not in content
    assert content.count("[REDACTED]") >= 4


def test_request_and_trace_context_flow_through_http_logs(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )
    application = FastAPI()

    @application.get("/shops/{shop_id}/conversations/{conversation_id}")
    async def endpoint(shop_id: str, conversation_id: str) -> dict[str, str]:
        logging.getLogger("app.business.test").info(
            "business_step",
            extra={"event": "business_step"},
        )
        return {"ok": "yes"}

    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(RequestIdMiddleware)

    with TestClient(application) as client:
        response = client.get(
            "/shops/shop-1/conversations/conv-1",
            headers={"X-Request-ID": "req-1", "X-Trace-ID": "trace-1"},
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-1"
    assert response.headers["X-Trace-ID"] == "trace-1"
    date_dir = isolated_logging / "2026-09-19"
    business = records_with_event(date_dir / "app.log", "business_step")
    access = records_with_event(date_dir / "access.log", "http_request_completed")
    assert business and access
    for record in (*business, *access):
        assert record["request_id"] == "req-1"
        assert record["trace_id"] == "trace-1"
        assert record["shop_id"] == "shop-1"
        assert record["conversation_id"] == "conv-1"


def test_top_level_json_fields_are_not_duplicated_in_fields(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 12, 10, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )

    logging.getLogger("app.middleware.logging").info(
        "top_level_event",
        extra={
            "event": "top_level_event",
            "method": "POST",
            "path": "/qa",
            "status_code": 200,
            "duration_ms": 3.2,
            "model": "test-model",
            "business_key": "kept",
        },
    )

    record = records_with_event(
        isolated_logging / "2026-09-19" / "access.log", "top_level_event"
    )[-1]
    for key in ("event", "method", "path", "status_code", "duration_ms", "model"):
        assert key in record
        assert key not in record["fields"]
    assert record["fields"]["business_key"] == "kept"


@pytest.mark.asyncio
async def test_background_task_gets_fresh_trace_context(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 12, 15, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )

    async def log_job(event: str) -> None:
        logging.getLogger("app.background.test").info(event, extra={"event": event})

    with log_context(request_id="http-request"):
        inherited_task = asyncio.create_task(log_job("inherited_background_job"))
        await inherited_task
        background_task = create_log_task(log_job("isolated_background_job"), shop_id="shop-bg")
        await background_task
        assert get_request_id() == "http-request"

    inherited = records_with_event(
        isolated_logging / "2026-09-19" / "app.log", "inherited_background_job"
    )
    isolated = records_with_event(
        isolated_logging / "2026-09-19" / "app.log", "isolated_background_job"
    )
    assert inherited and isolated
    assert inherited[-1]["request_id"] == "http-request"
    assert isolated[-1]["request_id"].startswith("bg-")
    assert isolated[-1]["trace_id"] == isolated[-1]["request_id"]
    assert isolated[-1]["shop_id"] == "shop-bg"


@pytest.mark.asyncio
async def test_chat_conversation_id_reaches_access_log(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 12, 30, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )

    class FakeChatService:
        async def chat(self, request: Any) -> ChatResponse:
            return ChatResponse(
                conversation_id=request.conversation_id,
                answer="ok",
                source="rule",
            )

    application = FastAPI()
    application.include_router(chat_router)
    application.dependency_overrides[get_chat_service] = lambda: FakeChatService()
    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(RequestIdMiddleware)

    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        response = await client.post(
            "/chat",
            json={"message": "你好", "conversation_id": "conv-chat-1"},
            headers={
                "X-Request-ID": "chat-req-1",
                "X-Conversation-ID": "ignored-header",
            },
        )

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "chat-req-1"
    body = response.json()["data"]
    assert body["conversation_id"] == "conv-chat-1"

    access = records_with_event(
        isolated_logging / "2026-09-19" / "access.log",
        "http_request_completed",
    )
    assert access
    assert access[-1]["request_id"] == "chat-req-1"
    assert access[-1]["trace_id"] == "chat-req-1"
    assert access[-1]["conversation_id"] == "conv-chat-1"


def test_retention_removes_only_expired_date_directories(
    isolated_logging: Path,
) -> None:
    now = datetime(2026, 9, 19, 13, 0, tzinfo=timezone.utc)
    expired = isolated_logging / "2026-08-19"
    kept = isolated_logging / "2026-08-20"
    expired.mkdir(parents=True)
    kept.mkdir(parents=True)
    (expired / "app.log").write_text("old", encoding="utf-8")
    (kept / "app.log").write_text("kept", encoding="utf-8")
    (isolated_logging / "legacy.log").write_text("legacy", encoding="utf-8")

    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging, log_retention_days=30),
        force=True,
        clock=lambda: now,
    )

    assert not expired.exists()
    assert kept.exists()
    assert (isolated_logging / "legacy.log").exists()
