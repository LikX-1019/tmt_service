"""统一日志模块，提供按日分文件、脱敏和请求链路关联能力。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sys
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import Context, ContextVar, Token
from datetime import datetime, timedelta
from pathlib import Path
from traceback import format_exception
from typing import Any
from uuid import uuid4

from app.core.config import Settings, get_settings


_CONTEXT_FIELDS = ("request_id", "trace_id", "conversation_id", "shop_id")
_CONTEXT_VARS: dict[str, ContextVar[str]] = {
    field: ContextVar(field, default="-") for field in _CONTEXT_FIELDS
}
# 兼容旧内部名称，避免已有调用方在升级时立即失效。
_request_id = _CONTEXT_VARS["request_id"]

_LOG_FILENAMES = ("app.log", "error.log", "access.log", "qa.log", "connector.log", "audit.log")
_DATE_DIRECTORY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAFE_CONTEXT_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_SENSITIVE_KEY = re.compile(
    r"(?:password|passwd|pwd|token|api[_-]?key|secret|authorization|cookie|session)",
    re.IGNORECASE,
)
_SENSITIVE_ASSIGNMENT = re.compile(
    r"\b(password|passwd|pwd|token|api[_-]?key|secret|authorization|cookie|session)\b"
    r"(\s*[:=]\s*)([^\r\n,;]+)",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_REDACTED = "[REDACTED]"

_RESERVED_RECORD_KEYS = {
    "name",
    "msg",
    "args",
    "levelname",
    "levelno",
    "pathname",
    "filename",
    "module",
    "exc_info",
    "exc_text",
    "stack_info",
    "stack_header",
    "lineno",
    "funcName",
    "created",
    "msecs",
    "relativeCreated",
    "thread",
    "threadName",
    "processName",
    "process",
    "taskName",
    "message",
    "asctime",
    *_CONTEXT_FIELDS,
}
_TOP_LEVEL_FIELDS = ("event", "method", "path", "status_code", "duration_ms", "model")

_log_files: tuple[Path, Path] | None = None
_coordinator: DailyLogCoordinator | None = None
_managed_file_handlers: list[tuple[logging.Logger, DailyDatedFileHandler]] = []


def _now() -> datetime:
    return datetime.now().astimezone()


def _clean_context_value(value: object) -> str:
    """规范上下文字符串，保证 JSON Lines 可检索且不引入换行。"""
    text = str(value).strip()
    return text[:256] if text else "-"


def bind_request_id(request_id: str) -> Token[str]:
    """将请求 ID 绑定到当前异步上下文，兼容既有调用。"""
    return _CONTEXT_VARS["request_id"].set(_clean_context_value(request_id))


def reset_request_id(token: Token[str]) -> None:
    """恢复请求 ID 上下文，防止请求之间互相污染。"""
    _CONTEXT_VARS["request_id"].reset(token)


def get_request_id() -> str:
    """获取当前异步上下文中的请求 ID。"""
    return _CONTEXT_VARS["request_id"].get()


def bind_log_context(**values: object) -> dict[str, Token[str]]:
    """绑定统一日志上下文；未知字段拒绝，避免静默丢失业务字段。"""
    unknown = set(values) - set(_CONTEXT_FIELDS)
    if unknown:
        raise TypeError(f"unknown log context fields: {sorted(unknown)}")
    tokens: dict[str, Token[str]] = {}
    for field in _CONTEXT_FIELDS:
        if field not in values:
            continue
        tokens[field] = _CONTEXT_VARS[field].set(
            _clean_context_value(values[field]) or "-"
        )
    return tokens


def reset_log_context(tokens: dict[str, Token[str]]) -> None:
    """按绑定时的逆序恢复日志上下文。"""
    for field in reversed(_CONTEXT_FIELDS):
        if field in tokens:
            _CONTEXT_VARS[field].reset(tokens[field])


@contextmanager
def log_context(**values: object) -> Iterator[None]:
    """在同步或异步调用片段内临时绑定日志上下文。"""
    tokens = bind_log_context(**values)
    try:
        yield
    finally:
        reset_log_context(tokens)


def _new_background_context(**values: object) -> Context:
    """创建干净的后台任务上下文，避免沿用启动请求的 request_id。"""
    fields = dict(values)
    request_id = _clean_context_value(fields.pop("request_id", f"bg-{uuid4().hex}"))
    trace_id = _clean_context_value(fields.pop("trace_id", request_id))
    fields.update(request_id=request_id, trace_id=trace_id)
    context = Context()
    # Context.run 在空 Context 中执行绑定，不污染当前请求上下文。
    context.run(bind_log_context, **fields)
    return context


def create_log_task(
    coroutine: Any,
    *,
    name: str | None = None,
    **context_values: object,
) -> asyncio.Task[Any]:
    """创建带独立链路 ID 的后台任务，适合长生命周期 worker/connector。"""
    context = _new_background_context(**context_values)
    return asyncio.create_task(coroutine, name=name, context=context)


def sanitize_text(value: str) -> str:
    """对已格式化文本中的常见敏感赋值做兜底脱敏。"""
    redacted = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{_REDACTED}",
        value,
    )
    return _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)


def _sanitize_value(value: object, *, _seen: set[int] | None = None, depth: int = 0) -> object:
    """递归脱敏 extra 字段，兼容嵌套 mapping/list/tuple/set。"""
    if depth > 8:
        return _REDACTED
    seen = _seen or set()
    marker = id(value)
    if marker in seen:
        return _REDACTED
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            sanitized[key] = (
                _REDACTED
                if _SENSITIVE_KEY.search(key)
                else _sanitize_value(raw_value, _seen=seen | {marker}, depth=depth + 1)
            )
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [
            _sanitize_value(item, _seen=seen | {marker}, depth=depth + 1)
            for item in value
        ]
        return items if isinstance(value, list) else type(value)(items)
    if isinstance(value, str):
        return sanitize_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(str(value))


class RequestContextFilter(logging.Filter):
    """为每条日志补齐统一上下文字段，包括框架产生的日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        """在日志进入处理器前补齐字段，避免格式化失败。"""
        for field in _CONTEXT_FIELDS:
            value = getattr(record, field, None)
            if value is None or str(value) == "":
                value = _CONTEXT_VARS[field].get()
            setattr(record, field, _clean_context_value(value))
        return True


class JsonFormatter(logging.Formatter):
    """输出 JSON Lines，保留源码位置、上下文、业务字段和 traceback。"""

    def format(self, record: logging.LogRecord) -> str:
        """把标准日志记录转换成 UTF-8 友好且已脱敏的 JSON 字符串。"""
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).astimezone().isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "module": record.name,
            "message": sanitize_text(record.getMessage()),
            "request_id": record.request_id,
            "trace_id": record.trace_id,
            "conversation_id": record.conversation_id,
            "shop_id": record.shop_id,
            "source_file": record.pathname,
            "source_line": record.lineno,
            "source_function": record.funcName,
        }
        fields = {
            key: (
                _REDACTED
                if _SENSITIVE_KEY.search(key)
                else _sanitize_value(value)
            )
            for key, value in record.__dict__.items()
            if key not in _RESERVED_RECORD_KEYS and not key.startswith("_")
        }
        for key in _TOP_LEVEL_FIELDS:
            if hasattr(record, key):
                value = getattr(record, key)
                payload[key] = (
                    _REDACTED
                    if _SENSITIVE_KEY.search(key)
                    else _sanitize_value(value)
                )
        payload["fields"] = fields
        if record.exc_info:
            payload["traceback"] = sanitize_text(
                "".join(format_exception(*record.exc_info))
            )
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """提供本地可读格式，同时输出关键链路字段。"""

    def __init__(self) -> None:
        """初始化控制台格式。"""
        super().__init__(
            fmt=(
                "%(asctime)s.%(msecs)03d | %(levelname)s | %(name)s | "
                "%(pathname)s:%(lineno)d | %(funcName)s | "
                "request_id=%(request_id)s trace_id=%(trace_id)s | "
                "conversation_id=%(conversation_id)s shop_id=%(shop_id)s | %(message)s"
            ),
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        """格式化并对消息及 traceback 做兜底脱敏。"""
        return sanitize_text(super().format(record))


class DailyDatedFileHandler(logging.Handler):
    """由共享协调器驱动的日期目录文件 handler。"""

    def __init__(
        self,
        coordinator: DailyLogCoordinator,
        filename: str,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level=level)
        self.coordinator = coordinator
        self.filename = filename
        self.terminator = "\n"
        self.path: Path | None = None
        self.stream: Any = None

    def _open_locked(self, directory: Path) -> None:
        if self.stream is not None:
            return
        self.path = directory / self.filename
        self.stream = self.path.open("a", encoding="utf-8", buffering=1)

    def _close_stream_locked(self) -> None:
        if self.stream is not None:
            try:
                self.stream.flush()
                self.stream.close()
            finally:
                self.stream = None
                self.path = None

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.stream is None:
                self.coordinator.prepare()
            with self.coordinator.lock:
                self.coordinator.ensure_current_locked()
                stream = self.stream
                if stream is None:
                    raise RuntimeError(f"log stream is not open: {self.filename}")
                stream.write(self.format(record) + self.terminator)
                stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self.coordinator.lock:
            self._close_stream_locked()
        super().close()


class DailyLogCoordinator:
    """协调全部日志文件同步创建、切换和过期目录清理。"""

    def __init__(
        self,
        *,
        log_dir: Path,
        filenames: Sequence[str],
        retention_days: int,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.log_dir = log_dir
        self.filenames = tuple(filenames)
        self.retention_days = retention_days
        self.clock = clock
        self.lock = threading.RLock()
        self.current_date = self._current_date()
        self.handlers: dict[str, DailyDatedFileHandler] = {}
        self._last_prepared_date: str | None = None

    def _current_date(self) -> str:
        return self.clock().strftime("%Y-%m-%d")

    def register(self, handler: DailyDatedFileHandler) -> None:
        with self.lock:
            if handler.filename in self.handlers:
                raise ValueError(f"duplicate log handler: {handler.filename}")
            self.handlers[handler.filename] = handler

    def ensure_current_locked(self) -> None:
        current_date = self._current_date()
        if current_date == self.current_date and all(
            handler.stream is not None for handler in self.handlers.values()
        ):
            return

        if current_date != self.current_date:
            for handler in self.handlers.values():
                handler._close_stream_locked()
            self.current_date = current_date

        directory = self.log_dir / self.current_date
        directory.mkdir(parents=True, exist_ok=True)
        for handler in self.handlers.values():
            handler._open_locked(directory)

        if current_date != self._last_prepared_date:
            cleanup_expired_log_directories(
                self.log_dir,
                self.retention_days,
                now=self.clock(),
            )
            self._last_prepared_date = current_date

    def prepare(self) -> None:
        with self.lock:
            self.ensure_current_locked()

    def close(self) -> None:
        with self.lock:
            for handler in self.handlers.values():
                handler._close_stream_locked()


def cleanup_expired_log_directories(
    log_dir: Path,
    retention_days: int,
    *,
    now: datetime | None = None,
) -> list[str]:
    """删除超过保留期且符合 YYYY-MM-DD 命名的日志目录。"""
    current_date = (now or _now()).date()
    cutoff = current_date - timedelta(days=retention_days)
    removed: list[str] = []
    if not log_dir.exists():
        return removed
    for child in log_dir.iterdir():
        if child.is_symlink() or not child.is_dir():
            continue
        if not _DATE_DIRECTORY_PATTERN.fullmatch(child.name):
            continue
        try:
            created_date = datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if created_date < cutoff:
            try:
                shutil.rmtree(child)
            except OSError:
                continue
            removed.append(child.name)
    return removed


def _reset_managed_handlers() -> None:
    global _managed_file_handlers
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for logger, handler in _managed_file_handlers:
        logger.removeHandler(handler)
        handler.close()
    _managed_file_handlers = []


def _add_category_handler(
    logger_name: str,
    handler: DailyDatedFileHandler,
) -> None:
    logger = logging.getLogger(logger_name)
    logger.addHandler(handler)
    _managed_file_handlers.append((logger, handler))


def configure_logging(
    settings: Settings | None = None,
    *,
    force: bool = False,
    clock: Callable[[], datetime] = _now,
) -> tuple[Path, Path]:
    """配置控制台、全量、错误和分类日志输出。"""
    global _log_files, _coordinator

    if _log_files is not None and not force:
        return _log_files

    settings = settings or get_settings()
    log_dir = settings.log_dir.expanduser().resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    _reset_managed_handlers()
    coordinator = DailyLogCoordinator(
        log_dir=log_dir,
        filenames=_LOG_FILENAMES,
        retention_days=settings.log_retention_days,
        clock=clock,
    )
    context_filter = RequestContextFilter()
    json_formatter = JsonFormatter()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(settings.log_level)
    console_handler.setFormatter(ConsoleFormatter())
    console_handler.addFilter(context_filter)

    handlers = {
        filename: DailyDatedFileHandler(
            coordinator,
            filename,
            level=(
                logging.ERROR
                if filename == "error.log"
                else logging.getLevelName(settings.log_level)
            ),
        )
        for filename in _LOG_FILENAMES
    }
    for handler in handlers.values():
        coordinator.register(handler)
        handler.addFilter(context_filter)
        handler.setFormatter(json_formatter)

    root = logging.getLogger()
    root.setLevel(settings.log_level)
    root.addHandler(console_handler)
    root.addHandler(handlers["app.log"])
    root.addHandler(handlers["error.log"])

    for logger_name in (
        "app.middleware.logging",
        "app.qa",
        "app.integrations.pdd",
        "app.services.console_runtime",
        "app.services.shop_runtime_manager",
        "app.audit",
    ):
        filename = "access.log"
        if logger_name == "app.qa":
            filename = "qa.log"
        elif logger_name in {
            "app.integrations.pdd",
            "app.services.console_runtime",
            "app.services.shop_runtime_manager",
        }:
            filename = "connector.log"
        elif logger_name == "app.audit":
            filename = "audit.log"
        _add_category_handler(logger_name, handlers[filename])

    coordinator.prepare()

    date_dir = log_dir / coordinator.current_date
    _coordinator = coordinator
    _log_files = (date_dir / "app.log", date_dir / "error.log")
    logging.getLogger(__name__).info(
        "logging_configured",
        extra={
            "event": "logging_configured",
            "log_directory": str(date_dir),
            "retention_days": settings.log_retention_days,
        },
    )
    return _log_files


def get_log_files() -> tuple[Path, Path] | None:
    """返回当前进程正在使用的完整日志和错误日志文件路径。"""
    paths = get_log_file_paths()
    if paths is None:
        return None
    return paths["app.log"], paths["error.log"]


def get_log_file_paths() -> dict[str, Path] | None:
    """返回协调器当前日期目录下全部受管日志文件路径。"""
    if _coordinator is None:
        return None
    date_dir = _coordinator.log_dir / _coordinator.current_date
    return {filename: date_dir / filename for filename in _LOG_FILENAMES}
