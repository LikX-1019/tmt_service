from pathlib import Path
from typing import Any
import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

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
    serialized = json.dumps(rows, ensure_ascii=False, indent=2)
    with path.open("x", encoding="utf-8") as backup:
        backup.write(serialized)
        backup.flush()
        os.fsync(backup.fileno())
    verified = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(verified, list) or len(verified) != len(rows):
        raise RuntimeError("QA backup verification failed: row count mismatch")
    if any(not isinstance(row, dict) for row in verified):
        raise RuntimeError("QA backup verification failed: invalid row format")
    return len(verified)


async def _count_mysql() -> int:
    factory = get_session_factory()
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(QAKnowledge)) or 0)


async def _clear_mysql(expected_before: int) -> tuple[int, int]:
    factory = get_session_factory()
    async with factory() as session:
        before = int(await session.scalar(select(func.count()).select_from(QAKnowledge)) or 0)
        if before != expected_before:
            raise RuntimeError(
                f"cs_qa changed after backup: expected {expected_before}, found {before}"
            )
        await session.execute(delete(QAKnowledge))
        await session.commit()
        after = int(await session.scalar(select(func.count()).select_from(QAKnowledge)) or 0)
    return before, after


def _milvus_count(client: Any, collection: str) -> int:
    rows = client.query(
        collection_name=collection,
        filter="",
        output_fields=["count(*)"],
    )
    if not rows:
        return 0
    return int(rows[0].get("count(*)", 0))


def _inspect_milvus() -> tuple[str, int]:
    from pymilvus import MilvusClient

    settings = get_settings()
    client = MilvusClient(uri=settings.milvus_uri)
    try:
        if not client.has_collection(settings.milvus_collection):
            return "missing", 0
        return "present", _milvus_count(client, settings.milvus_collection)
    finally:
        client.close()


def _clear_milvus() -> tuple[int, int]:
    from pymilvus import MilvusClient

    settings = get_settings()
    client = MilvusClient(uri=settings.milvus_uri)
    try:
        if not client.has_collection(settings.milvus_collection):
            return 0, 0
        before = _milvus_count(client, settings.milvus_collection)
        client.delete(
            collection_name=settings.milvus_collection,
            filter='chunk_id != ""',
        )
        client.flush(collection_name=settings.milvus_collection)
        after = _milvus_count(client, settings.milvus_collection)
        return before, after
    finally:
        client.close()


def _looks_production(*values: object) -> bool:
    return any(
        token in str(value or "").strip().casefold()
        for value in values
        for token in ("production", "prod")
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--yes", action="store_true", help="确认执行不可逆清理")
    parser.add_argument("--backup", type=Path, help="清理前把 cs_qa 行导出为 JSON")
    parser.add_argument("--skip-milvus", action="store_true", help="跳过 Milvus 实体清理（保留旧向量风险）")
    args = parser.parse_args()

    settings = get_settings()
    print("Environment:", settings.app_env)
    print("QA database:", settings.mysql_host, settings.mysql_port, settings.mysql_database)
    print("QA table: cs_qa")
    print(
        "Vector collection:",
        settings.milvus_host,
        settings.milvus_port,
        settings.milvus_collection,
    )
    mysql_count = await _count_mysql()
    milvus_status, milvus_count = _inspect_milvus()
    print(f"MySQL cs_qa count: {mysql_count}")
    print(f"Milvus collection status: {milvus_status}; entity count: {milvus_count}")
    if not args.yes:
        print("Dry run only. Add --yes to delete cs_qa rows and Milvus entities.")
        return 0
    if _looks_production(
        settings.app_env,
        settings.mysql_host,
        settings.mysql_database,
        settings.milvus_host,
        settings.milvus_collection,
    ):
        raise SystemExit("Refusing to clear QA data: production-like target detected")
    if args.backup is None:
        raise SystemExit("--backup is required when --yes is used")
    if args.backup.exists():
        raise SystemExit(f"Backup file already exists: {args.backup}")
    count = await _backup_qa_rows(args.backup)
    if count != mysql_count:
        raise SystemExit(
            f"Backup verification failed: expected {mysql_count} rows, verified {count}"
        )
    print(f"Backed up and verified {count} cs_qa rows -> {args.backup}")

    before, after = await _clear_mysql(count)
    print(f"Deleted cs_qa rows: {before - after}; remaining: {after}")
    if after != 0:
        raise SystemExit(f"MySQL cs_qa cleanup verification failed: {after} rows remain")
    if not args.skip_milvus:
        vector_before, vector_after = _clear_milvus()
        print(f"Milvus entities: before={vector_before}; after={vector_after}")
        if vector_after != 0:
            raise SystemExit(
                f"Milvus cleanup verification failed: {vector_after} entities remain"
            )
    else:
        print("Milvus collection: SKIPPED")
    return 0


async def _run() -> int:
    try:
        return await main()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
