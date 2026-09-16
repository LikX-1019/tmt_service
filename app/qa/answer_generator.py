"""严格基于检索上下文的大模型回答生成器。"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.factories.llm_factory import LLMFactory
from app.prompts.qa import QA_SYSTEM_PROMPT, QA_USER_PROMPT
from app.qa.models import RetrievalDocument
from app.services.chat_service import _content_to_text


class QAAnswerGenerator:
    def __init__(self, llm: Any | None = None) -> None:
        self._llm = llm

    @staticmethod
    def _context(documents: list[RetrievalDocument]) -> str:
        sections = []
        for index, document in enumerate(documents, start=1):
            sections.append(
                "\n".join(
                    [
                        f"[S{index}]",
                        f"Title: {document.title or ''}",
                        f"Source: {document.source or ''}",
                        f"Content: {document.content}",
                    ]
                )
            )
        return "\n\n".join(sections)

    async def generate(
        self, query: str, documents: list[RetrievalDocument]
    ) -> str:
        llm = self._llm or await asyncio.to_thread(LLMFactory.get_llm, "rag")
        response = await llm.ainvoke(
            [
                SystemMessage(content=QA_SYSTEM_PROMPT),
                HumanMessage(
                    content=QA_USER_PROMPT.format(
                        context=self._context(documents), query=query
                    )
                ),
            ]
        )
        answer = _content_to_text(response.content)
        if not answer:
            raise RuntimeError("LLM returned empty QA answer")
        return answer
