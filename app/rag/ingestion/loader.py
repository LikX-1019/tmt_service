"""知识文件加载器，V1 支持 Markdown 与纯文本。"""

from __future__ import annotations

from pathlib import Path

from app.qa.models import RetrievalDocument


class KnowledgeLoader:
    supported_suffixes = {".md", ".txt"}

    def load(self, source: Path) -> tuple[list[RetrievalDocument], list[Path]]:
        files = [source] if source.is_file() else list(source.rglob("*"))
        documents: list[RetrievalDocument] = []
        failures: list[Path] = []
        for path in files:
            if not path.is_file() or path.suffix.lower() not in self.supported_suffixes:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                failures.append(path)
                continue
            documents.append(
                RetrievalDocument(
                    chunk_id=path.stem,
                    content=content,
                    title=path.stem,
                    source=str(path),
                    metadata={
                        "title": path.stem,
                        "source": str(path),
                        "category": path.parent.name,
                        "doc_type": path.suffix.lower().lstrip("."),
                    },
                )
            )
        return documents, failures
