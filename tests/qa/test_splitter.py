from app.qa.models import RetrievalDocument
from app.rag.ingestion.splitter import KnowledgeSplitter


def test_splitter_prefers_paragraph_boundaries_and_versions_chunks() -> None:
    document = RetrievalDocument(
        chunk_id="manual",
        content="# 标题\n\n第一段完整事实。\n\n第二段完整事实。",
        metadata={"document_version": "2026-09"},
    )

    chunks = KnowledgeSplitter(chunk_size=22, chunk_overlap=4).split(document)

    assert len(chunks) >= 2
    assert chunks[0].content.endswith("。")
    assert chunks[0].metadata["document_id"] == "manual"
    assert chunks[0].metadata["document_version"] == "2026-09"
    assert len(str(chunks[0].metadata["content_hash"])) == 64
    assert chunks[0].metadata["active"] is False
