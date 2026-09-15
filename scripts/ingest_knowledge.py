"""将现有 QA 数据库或 md/txt 知识文件写入 Milvus。"""

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

from app.core.config import get_settings
from app.database.session import dispose_engine
from app.factories.embedding_factory import EmbeddingFactory
from app.factories.vectorstore_factory import VectorStoreFactory
from app.qa.catalog import load_catalog
from app.rag.ingestion import (
    KnowledgeCleaner,
    KnowledgeIndexer,
    KnowledgeLoader,
    KnowledgeSplitter,
)


async def ingest(source: Path | None) -> dict[str, object]:
    settings = get_settings()
    loader_failures = 0
    if source is None:
        catalog = await load_catalog(settings)
        raw_documents = catalog.documents
        chunks = raw_documents
    else:
        raw_documents, failures = KnowledgeLoader().load(source)
        loader_failures = len(failures)
        cleaner = KnowledgeCleaner()
        splitter = KnowledgeSplitter()
        chunks = [
            chunk
            for document in raw_documents
            for chunk in splitter.split(cleaner.clean(document))
        ]

    embedder = await asyncio.to_thread(EmbeddingFactory.create, settings)
    vector_store = VectorStoreFactory.create(settings)
    succeeded, failed = await KnowledgeIndexer(embedder, vector_store).index(chunks)
    return {
        "documents": len(raw_documents),
        "chunks": len(chunks),
        "succeeded": succeeded,
        "failed": failed + loader_failures,
        "collection": settings.milvus_collection,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        help="可选 md/txt 文件或目录；省略时使用 QA_DATA_SOURCE",
    )
    args = parser.parse_args()
    async def run() -> dict[str, object]:
        try:
            return await ingest(args.source)
        finally:
            await dispose_engine()

    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
