from types import SimpleNamespace

import pytest

from app.qa.answer_generator import QAAnswerGenerator
from app.qa.models import RetrievalDocument


class StubLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self._content)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("  根据资料回答。  ", "根据资料回答。"),
        (
            [
                {"type": "text", "text": "根据"},
                {"type": "image_url", "image_url": {"url": "ignored"}},
                {"type": "text", "text": "资料回答。"},
            ],
            "根据资料回答。",
        ),
    ],
)
async def test_answer_generator_normalizes_llm_content(content, expected) -> None:
    generator = QAAnswerGenerator(llm=StubLLM(content))

    answer = await generator.generate(
        "可以水洗吗",
        [RetrievalDocument(chunk_id="c1", content="清洗说明")],
    )

    assert answer == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    ["", "   ", [], [{"type": "image_url", "image_url": {"url": "ignored"}}]],
)
async def test_answer_generator_rejects_empty_llm_content(content) -> None:
    generator = QAAnswerGenerator(llm=StubLLM(content))

    with pytest.raises(RuntimeError, match="empty QA answer"):
        await generator.generate(
            "可以水洗吗",
            [RetrievalDocument(chunk_id="c1", content="清洗说明")],
        )
