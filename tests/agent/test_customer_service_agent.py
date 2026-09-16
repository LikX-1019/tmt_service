from types import SimpleNamespace

import pytest

from app.agent.service import (
    AgentReply,
    CustomerContext,
    CustomerServiceAgent,
    IntentDecision,
)


class FakeIntentLLM:
    def __init__(self, intent: str, confidence: float = 0.99) -> None:
        self.result = IntentDecision(intent=intent, confidence=confidence)
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return self.result


class FakeResponseLLM:
    def __init__(self, answer: str = "您好呀，请问有什么可以帮您？") -> None:
        self.answer = answer
        self.messages = None
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        self.messages = messages
        return SimpleNamespace(content=self.answer)


@pytest.mark.asyncio
async def test_greeting_uses_customer_context_and_service_identity() -> None:
    intent_llm = FakeIntentLLM("daily_greeting")
    response_llm = FakeResponseLLM()
    agent = CustomerServiceAgent(
        intent_llm=intent_llm,
        response_llm=response_llm,
    )

    result = await agent.run(
        "你好",
        CustomerContext(
            customer_id="internal-customer-id",
            platform_customer_id="platform-customer-id",
            display_name="林女士",
            shop_name="JAFFICK旗舰店",
            goods_name="轻薄羽绒服",
        ),
    )

    assert result == AgentReply(
        intent="daily_greeting",
        confidence=0.99,
        answer="您好呀，请问有什么可以帮您？",
    )
    system_prompt = response_llm.messages[0].content
    assert "小满" in system_prompt
    assert "林女士" in system_prompt
    assert "JAFFICK旗舰店" in system_prompt
    assert "轻薄羽绒服" in system_prompt
    assert "internal-customer-id" not in system_prompt
    assert "platform-customer-id" not in system_prompt


@pytest.mark.asyncio
async def test_non_greeting_skips_response_model() -> None:
    response_llm = FakeResponseLLM()
    agent = CustomerServiceAgent(
        intent_llm=FakeIntentLLM("other", confidence=0.96),
        response_llm=response_llm,
    )

    result = await agent.run("你好，我的快递到哪了", CustomerContext())

    assert result == AgentReply(intent="other", confidence=0.96, answer=None)
    assert response_llm.calls == 0
