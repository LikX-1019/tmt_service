"""为低风险售前 FAQ 批量开通自动回复资格（可重复执行）。

安全范围：已审核(usable) + 启用检索 + 非人工必需 + 低风险 + 售前阶段，
且排除物流/售后与伤病/恢复类别。敏感词、高风险、商品上下文不匹配等
运行时门控仍由 AutoReplyPolicy 继续拦截，本脚本只授予 FAQ 精确命中的
自动发送资格。
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import text

from app.database.session import dispose_engine, get_session_factory

SAFE_SCOPE_WHERE = """
    review_status = 'usable'
    AND retrieval_enabled = 1
    AND human_required = 0
    AND risk_level = 'low'
    AND service_stage = 'pre_sale'
    AND question_type NOT IN ('物流/售后', '伤病/恢复')
"""


async def run(dry_run: bool) -> dict[str, object]:
    factory = get_session_factory()
    async with factory() as session:
        before = (
            await session.execute(
                text("SELECT COUNT(*) FROM cs_qa WHERE auto_reply_eligible = 1")
            )
        ).scalar_one()
        rows = (
            await session.execute(
                text(
                    "SELECT question_type, COUNT(*) AS n FROM cs_qa "
                    f"WHERE {SAFE_SCOPE_WHERE} "
                    "GROUP BY question_type ORDER BY n DESC"
                )
            )
        ).all()
        by_type = {row[0]: int(row[1]) for row in rows}
        total = sum(by_type.values())
        disabled = 0
        if not dry_run:
            # 先整体重置再按安全范围授予，保证脚本幂等且能回收不再合格的行。
            disabled = (
                await session.execute(
                    text("UPDATE cs_qa SET auto_reply_eligible = 0")
                )
            ).rowcount
            await session.execute(
                text(
                    "UPDATE cs_qa SET auto_reply_eligible = 1 "
                    f"WHERE {SAFE_SCOPE_WHERE}"
                )
            )
            await session.commit()
    return {
        "dry_run": dry_run,
        "previously_enabled": int(before),
        "reset_count": int(disabled),
        "newly_enabled": total,
        "by_question_type": by_type,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅统计将要开通的数量，不修改数据库",
    )
    args = parser.parse_args()

    async def runner() -> dict[str, object]:
        try:
            return await run(args.dry_run)
        finally:
            await dispose_engine()

    print(json.dumps(asyncio.run(runner()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
