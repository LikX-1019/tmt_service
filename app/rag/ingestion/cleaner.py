"""知识文本的保守清理。"""

from __future__ import annotations

import re

from app.qa.models import RetrievalDocument


_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+$", re.MULTILINE)


class KnowledgeCleaner:
    def clean(self, document: RetrievalDocument) -> RetrievalDocument:
        content = document.content.replace("\r\n", "\n").replace("\r", "\n")
        content = _TRAILING_SPACE.sub("", content)
        content = _EXCESS_BLANK_LINES.sub("\n\n", content).strip()
        return document.copy(content=content)
