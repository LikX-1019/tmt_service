"""HTTP → service → QA/connector → exception 的日志链路集成测试。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

import app.core.logging as logging_module
from app.core.config import Settings
from app.core.exception_handlers import unhandled_exception_handler
from app.core.logging import configure_logging
from app.middleware.logging import RequestLoggingMiddleware
from app.middleware.request_id import RequestIdMiddleware


MANAGED_LOGGERS = (
    "app.middleware.logging",
    "app.qa",
    "app.integrations.pdd",
    "app.services.console_runtime",
    "app.services.shop_runtime_manager",
    "app.audit",
)


@pytest.fixture
def isolated_logging(tmp_path: Path):
    """隔离 root/分类 logger，避免集成测试污染其他测试输出。"""
    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    saved_root_level = root.level
    root.handlers.clear()
    saved_children = {
        name: (list(logging.getLogger(name).handlers), logging.getLogger(name).level)
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
    for name, (handlers, level) in saved_children.items():
        child = logging.getLogger(name)
        child.handlers.clear()
        child.setLevel(level)
        for handler in handlers:
            child.addHandler(handler)
    logging_module._log_files = None
    logging_module._coordinator = None
    logging_module._managed_file_handlers = []


def read_json_lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def records_with_event(path: Path, event: str) -> list[dict[str, Any]]:
    return [item for item in read_json_lines(path) if item.get("event") == event]


def test_http_service_qa_connector_exception_context(isolated_logging: Path) -> None:
    now = datetime(2026, 9, 19, 12, 5, tzinfo=timezone.utc)
    configure_logging(
        Settings(_env_file=None, log_dir=isolated_logging),
        force=True,
        clock=lambda: now,
    )
    application = FastAPI()

    @application.get("/shops/{shop_id}/conversations/{conversation_id}")
    async def endpoint(
        shop_id: str, conversation_id: str, request: Request
    ) -> Response:
        logging.getLogger("app.services.chat_service").info(
            "e2e_service_step", extra={"event": "e2e_service_step"}
        )
        logging.getLogger("app.qa.service").info(
            "e2e_qa_step", extra={"event": "e2e_qa_step"}
        )
        logging.getLogger("app.integrations.pdd.playwright_connector").info(
            "e2e_connector_step", extra={"event": "e2e_connector_step"}
        )
        try:
            raise RuntimeError("e2e failure")
        except RuntimeError as exc:
            return await unhandled_exception_handler(request, exc)

    application.add_middleware(RequestLoggingMiddleware)
    application.add_middleware(RequestIdMiddleware)

    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(
            "/shops/shop-e2e/conversations/conv-e2e",
            headers={"X-Request-ID": "req-e2e", "X-Trace-ID": "trace-e2e"},
        )

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == "req-e2e"
    assert response.headers["X-Trace-ID"] == "trace-e2e"

    date_dir = isolated_logging / "2026-09-19"
    expected_events = {
        "app.log": (
            "e2e_service_step",
            "e2e_qa_step",
            "e2e_connector_step",
            "unhandled_exception",
        ),
        "access.log": ("http_request_completed",),
        "qa.log": ("e2e_qa_step",),
        "connector.log": ("e2e_connector_step",),
        "error.log": ("unhandled_exception",),
    }
    for filename, events in expected_events.items():
        for event in events:
            records = records_with_event(date_dir / filename, event)
            assert records, (filename, event)
            for record in records:
                assert record["request_id"] == "req-e2e"
                assert record["trace_id"] == "trace-e2e"
                assert record["shop_id"] == "shop-e2e"
                assert record["conversation_id"] == "conv-e2e"

    unhandled = records_with_event(date_dir / "error.log", "unhandled_exception")
    assert unhandled
    assert "RuntimeError: e2e failure" in unhandled[-1]["traceback"]
