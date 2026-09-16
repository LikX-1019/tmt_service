from types import SimpleNamespace

import pytest

from app.core.exceptions import LLMInvocationError
from app.services.chat_service import ChatService


class FakeLLM:
    def __init__(self, content: object = "您好，请问有什么可以帮您？") -> None:
        self.content = content
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_chat_service_invokes_async_llm() -> None:
    llm = FakeLLM()
    response = await ChatService(llm=llm).chat("你好")

    assert response.answer == "您好，请问有什么可以帮您？"
    assert len(llm.messages) == 2


@pytest.mark.asyncio
async def test_chat_service_converts_provider_failure() -> None:
    class BrokenLLM:
        async def ainvoke(self, messages):
            raise ConnectionError("provider unavailable")

    with pytest.raises(LLMInvocationError):
        await ChatService(llm=BrokenLLM()).chat("你好")
