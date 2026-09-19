"""手动运行或安装每日工作总结任务。"""

 # ruff: noqa: E402

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import get_settings
from app.worklog.launcher import LAUNCHD_LABEL, install_launchd
from app.worklog.service import configure_worklog_logger, generate_daily_worklog


def main() -> int:
    """提供日报手动执行入口和 macOS launchd 安装入口。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="要分析的 ISO 日期；默认为当前 Asia/Shanghai 日期",
    )
    parser.add_argument(
        "--install-launchd",
        action="store_true",
        help="安装/更新 macOS 每天 17:40 的用户定时任务",
    )
    args = parser.parse_args()
    settings = get_settings()

    if args.install_launchd:
        path = install_launchd(settings)
        print(f"Installed {LAUNCHD_LABEL}: {path}")
        return 0

    timezone = ZoneInfo(settings.worklog_timezone)
    day = (
        datetime.combine(args.date, datetime.min.time(), tzinfo=timezone)
        if args.date
        else None
    )
    logger = configure_worklog_logger(settings.log_dir)
    output, collection, content = generate_daily_worklog(
        settings,
        day=day,
        logger=logger,
    )
    print(output)
    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
