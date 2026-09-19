"""日报生成服务：收集证据、聚类、LLM 总结、写文件与记录日志。"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from app.core.config import Settings
from app.worklog.collector import GitWorkspaceError, collect_repository
from app.worklog.models import WorklogCollection
from app.worklog.summarizer import (
    EvidenceConstrainedSummarizer,
    Summarizer,
    cluster_work_items,
    format_worklog,
    parse_llm_daily_summary,
)


def configure_worklog_logger(log_dir: Path) -> logging.Logger:
    """把程序运行日志固定写入 logs/worklog.log，不与 HTTP 日志混合。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("tmt.daily_worklog")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(log_dir / "worklog.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    )
    logger.addHandler(handler)
    return logger


def _local_range(settings: Settings, day: datetime) -> tuple[datetime, datetime]:
    """返回统一时区下的 00:00 到当前配置截止时间。"""
    timezone = ZoneInfo(settings.worklog_timezone)
    local_day = day.astimezone(timezone).date()
    start = datetime.combine(local_day, time.min, tzinfo=timezone)
    end = datetime.combine(
        local_day,
        time(settings.worklog_schedule_hour, settings.worklog_schedule_minute),
        tzinfo=timezone,
    )
    return start, end


def generate_daily_worklog(
    settings: Settings,
    *,
    day: datetime | None = None,
    summarizer: Summarizer | None = None,
    logger: logging.Logger | None = None,
) -> tuple[Path, WorklogCollection, str]:
    """生成并覆盖当天日报和 latest.md；返回文件、证据集合和正文。"""
    now = day or datetime.now(tz=ZoneInfo(settings.worklog_timezone))
    start, end = _local_range(settings, now)
    logger = logger or configure_worklog_logger(settings.log_dir)
    collection = WorklogCollection(
        date=start.date().isoformat(),
        timezone=settings.worklog_timezone,
        start_at=start,
        end_at=end,
    )
    logger.info(
        "Daily worklog scan started: start=%s end=%s roots=%r",
        start.isoformat(),
        end.isoformat(),
        settings.worklog_workspace_roots,
    )

    for raw_root in settings.workspace_roots:
        try:
            snapshot = collect_repository(raw_root, start, end)
        except GitWorkspaceError as error:
            logger.warning("Skipped workspace %s: %s", raw_root, error)
            continue
        collection.repositories.append(snapshot)
        logger.info(
            "Scanned repository=%s commits=%d uncommitted=%d",
            snapshot.name,
            len(snapshot.commits),
            len(snapshot.changes),
        )

    collection.items = cluster_work_items(collection)
    logger.info(
        "Collected commits=%d uncommitted=%d clustered_items=%d",
        collection.commit_count,
        collection.change_count,
        len(collection.items),
    )

    body_items: list[str] = []
    if collection.items:
        evidence_summarizer = summarizer or EvidenceConstrainedSummarizer()
        try:
            raw_summary = evidence_summarizer.summarize(collection)
        except Exception as error:
            raw_summary = None
            logger.warning(
                "LLM summary failed; using deterministic evidence summary: %s",
                error,
            )
        if raw_summary:
            # 预聚类只是证据输入；允许 LLM 合并到 4-5 条，但至少要有一条。
            expected_min = 1
            body_items = parse_llm_daily_summary(raw_summary, expected_min) or []
            if body_items:
                logger.info("LLM summary accepted with %d items", len(body_items))
            else:
                logger.warning("LLM summary failed format validation")
    if not body_items:
        body_items = [item.description for item in collection.items]

    content = format_worklog(collection, body_items)
    output_dir = settings.worklog_output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    daily_path = output_dir / f"{collection.date}.md"
    latest_path = output_dir / "latest.md"
    _atomic_write(daily_path, content)
    _atomic_write(latest_path, content)
    logger.info("Daily report generated: %s", daily_path)
    return daily_path, collection, content


def _atomic_write(path: Path, content: str) -> None:
    """同一天重复执行时覆盖同一个文件，避免产生 -1/-2 副本。"""
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
