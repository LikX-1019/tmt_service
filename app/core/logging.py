"""统一日志模块，提供分文件记录、源码定位和请求链路关联能力。"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar, Token
from datetime import datetime
from pathlib import Path
from traceback import format_exception
from typing import Any

from app.core.config import Settings, get_settings


_request_id: ContextVar[str] = ContextVar("request_id", default="-")
_log_files: tuple[Path, Path] | None = None


def bind_request_id(request_id: str) -> Token[str]:
    """将请求 ID 绑定到当前异步上下文，使链路日志可关联查询。"""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    """请求结束后恢复上下文，防止请求 ID 泄漏到其他请求。"""
    _request_id.reset(token)


def get_request_id() -> str:
    """获取当前异步上下文中的请求 ID。"""
    return _request_id.get()


class RequestContextFilter(logging.Filter):
    """为每条日志补充请求 ID，包括不经过业务代码的框架日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """在日志进入处理器前补齐 request_id 字段，避免格式化失败。"""
        record.request_id = getattr(record, "request_id", get_request_id())
        return True


class JsonFormatter(logging.Formatter):
    """将日志格式化为便于检索的 JSON Lines，并记录精确源码位置。"""

    def format(self, record: logging.LogRecord) -> str:
        """把标准日志记录转换成 UTF-8 友好的 JSON 字符串。"""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "source_file": record.pathname,
            "source_line": record.lineno,
            "source_function": record.funcName,
        }
        for key in (
            "event",
            "method",
            "path",
            "status_code",
            "duration_ms",
            "model",
        ):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["traceback"] = "".join(format_exception(*record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """提供便于本地阅读的控制台格式，同时保留关键诊断字段。"""

    def __init__(self) -> None:
        """初始化包含时间、源码位置和请求 ID 的控制台格式。"""
        super().__init__(
            fmt=(
                "%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | "
                "%(pathname)s:%(lineno)d | %(funcName)s | "
                "request_id=%(request_id)s | %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )


def configure_logging(
    settings: Settings | None = None,
    *,
    force: bool = False,
) -> tuple[Path, Path]:
    """配置控制台、完整日志文件和仅错误日志文件三个输出目标。"""
    global _log_files

    if _log_files is not None and not force:
        return _log_files

    settings = settings or get_settings()
    log_dir = settings.log_dir.expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now().astimezone():%Y%m%d-%H%M%S}-{os.getpid()}"
    app_log = log_dir / f"app-{run_id}.log"
    error_log = log_dir / f"error-{run_id}.log"

    context_filter = RequestContextFilter()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(settings.log_level)
    console_handler.setFormatter(ConsoleFormatter())
    console_handler.addFilter(context_filter)

    app_handler = logging.FileHandler(app_log, encoding="utf-8")
    app_handler.setLevel(settings.log_level)
    app_handler.setFormatter(JsonFormatter())
    app_handler.addFilter(context_filter)

    error_handler = logging.FileHandler(error_log, encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(JsonFormatter())
    error_handler.addFilter(context_filter)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(settings.log_level)
    root.addHandler(console_handler)
    root.addHandler(app_handler)
    root.addHandler(error_handler)

    _log_files = (app_log, error_log)
    logging.getLogger(__name__).info(
        "logging_configured",
        extra={"event": "logging_configured"},
    )
    return _log_files


def get_log_files() -> tuple[Path, Path] | None:
    """返回当前进程正在使用的完整日志和错误日志文件路径。"""
    return _log_files
