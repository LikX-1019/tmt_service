from pathlib import Path
from typing import Any
import argparse
import asyncio
import json
import sys
from datetime import date, datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, select  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.database.session import dispose_engine, get_session_factory  # noqa: E402
from app.models.knowledge import QAKnowledge  # noqa: E402


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def _backup_qa_rows(path: Path) -> int:
    factory = get_session_factory()
    columns = QAKnowledge.__table__.columns
    rows: list[dict[str, Any]] = []
    async with factory() as session:
        for item in await session.scalars(select(QAKnowledge).order_by(QAKnowledge.id)):
            rows.append({column.name: _jsonable(getattr(item, column.name)) for column in columns})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


async def _clear_mysql() -> tuple[int, int]:
    factory = get_session_factory()
    async with factory() as session:
        before = len((await session.execute(select(QAKnowledge.id))).all())
        await session.execute(delete(QAKnowledge))
        await session.commit()
        after = len((await session.execute(select(QAKnowledge.id))).all())
    return before, after


def _clear_milvus() -> str:
    from pymilvus import MilvusClient

    settings = get_settings()
    client = MilvusClient(uri=settings.milvus_uri)
    try:
        if not client.has_collection(settings.milvus_collection):
            return "missing"
        client.delete(
            collection_name=settings.milvus_collection,
            filter='chunk_id != ""',
        )
        return "cleared"
    finally:
        client.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="确认执行不可逆清理")
    parser.add_argument("--backup", type=Path, help="清理前把 cs_qa 行导出为 JSON")
    parser.add_argument("--skip-milvus", action="store_true", help="跳过 Milvus 实体清理（保留旧向量风险）")
    args = parser.parse_args()

    settings = get_settings()
    print("QA database:", settings.mysql_host, settings.mysql_database)
    print("QA table: cs_qa")
    print("Vector collection:", settings.milvus_collection)
    if not args.yes:
        print("Dry run only. Add --yes to delete cs_qa rows and Milvus entities.")
        return 0
    if args.backup is not None:
        if args.backup.exists():
            raise SystemExit(f"Backup file already exists: {args.backup}")
        count = await _backup_qa_rows(args.backup)
        print(f"Backed up {count} cs_qa rows -> {args.backup}")

    before, after = await _clear_mysql()
    print(f"Deleted cs_qa rows: {before - after}; remaining: {after}")
    if not args.skip_milvus:
        print("Milvus collection:", _clear_milvus())
    else:
        print("Milvus collection: SKIPPED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    finally:
        asyncio.run(dispose_engine())
