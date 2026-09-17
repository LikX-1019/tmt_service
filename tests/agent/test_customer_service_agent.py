from types import SimpleNamespace

import pytest

from app.agent.greeting import (
    match_greeting_rule,
    normalize_greeting_phrase,
    normalize_reply_templates,
    select_reply_template,
)
from app.agent.service import (
    AgentReply,
    CustomerContext,
    CustomerServiceAgent,
    IntentDecision,
)
from app.factories.llm_factory import LLMFactory


class FakeIntentLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.content)


class FailingIntentLLM:
    async def ainvoke(self, messages):
        raise TimeoutError("provider timeout")


def config(*, enabled: bool = True, **trigger_groups: list[str]):
    groups = {
        "salutation": ["你好", "您好", "哈喽", "嗨"],
        "availability": ["在吗", "有人吗", "客服在吗"],
        "thanks": ["谢谢", "辛苦了", "感谢"],
        "goodbye": ["再见", "拜拜", "先这样"],
    }
    groups.update(trigger_groups)
    return {
        "enabled": enabled,
        "trigger_groups": groups,
        "reply_templates": {
            "salutation": ["店铺问候话术"],
            "availability": ["店铺在线话术"],
            "thanks": ["店铺致谢话术"],
            "goodbye": ["店铺告别话术"],
        },
    }


def test_greeting_rule_normalization_and_full_phrase_match() -> None:
    assert normalize_greeting_phrase("  ＨＥＬＬＯ！！ ") == "hello"
    assert match_greeting_rule("ｈｅｌｌｏ！", {"salutation": ["hello"]}) == "salutation"
    assert match_greeting_rule("在吗？店家？", {"availability": ["在吗"]}) == "availability"
    assert match_greeting_rule("你好，我想退款", {"salutation": ["你好"]}) is None


@pytest.mark.asyncio
async def test_rule_greeting_uses_shop_template_without_llm() -> None:
    llm = FakeIntentLLM("invalid")
    agent = CustomerServiceAgent(intent_llm=llm)

    result = await agent.run(
        "在吗？！",
        CustomerContext(display_name="林女士"),
        greeting_config=config(),
    )

    assert result == AgentReply(
        intent="daily_greeting",
        greeting_type="availability",
        confidence=1.0,
        recognition_source="rule",
        answer="店铺在线话术",
    )
    assert llm.calls == 0


def test_reply_template_is_stable_per_customer_and_distributed() -> None:
    variants = [f"问候版本-{index}" for index in range(5)]
    templates = {"salutation": variants}

    first = select_reply_template(
        templates, "salutation", selection_key="buyer-1"
    )
    second = select_reply_template(
        templates, "salutation", selection_key="buyer-1"
    )
    selected = {
        select_reply_template(
            templates, "salutation", selection_key=f"buyer-{index}"
        )
        for index in range(50)
    }

    assert first == second
    assert selected == set(variants)
    assert normalize_reply_templates({"salutation": "历史单条话术"})[
        "salutation"
    ] == ["历史单条话术"]


@pytest.mark.asyncio
async def test_model_fallback_uses_template_and_never_generates_reply() -> None:
    llm = FakeIntentLLM(
        '{"intent":"daily_greeting","greeting_type":"thanks","confidence":0.96}'
    )
    agent = CustomerServiceAgent(intent_llm=llm)

    result = await agent.run(
        "您好，方便帮忙吗",
        CustomerContext(),
        greeting_config=config(),
    )

    assert result.answer == "店铺致谢话术"
    assert result.recognition_source == "llm"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_low_confidence_model_result_falls_back_to_other() -> None:
    llm = FakeIntentLLM(
        '{"intent":"daily_greeting","greeting_type":"goodbye","confidence":0.89}'
    )
    result = await CustomerServiceAgent(intent_llm=llm).run(
        "这句话有点像告别", CustomerContext(), greeting_config=config()
    )
    assert result.intent == "other"
    assert result.answer is None


@pytest.mark.asyncio
async def test_invalid_model_json_safely_falls_back_to_other() -> None:
    llm = FakeIntentLLM("不是 JSON")
    result = await CustomerServiceAgent(intent_llm=llm).run(
        "随便聊聊", CustomerContext(), greeting_config=config()
    )
    assert result.intent == "other"
    assert result.confidence == 0.0
    assert result.answer is None


@pytest.mark.asyncio
async def test_intent_uses_plain_llm_not_structured_tool_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = FakeIntentLLM(
        '{"intent":"daily_greeting","greeting_type":"salutation","confidence":0.99}'
    )

    def fake_get_llm(agent_type, temperature, streaming=False):
        assert agent_type == "intent"
        assert temperature == 0.0
        assert streaming is False
        return llm

    def reject_structured(*args, **kwargs):
        raise AssertionError("意图识别不得使用 tool_choice 结构化输出")

    monkeypatch.setattr(LLMFactory, "get_llm", fake_get_llm)
    monkeypatch.setattr(LLMFactory, "get_structured_llm", reject_structured)
    result = await CustomerServiceAgent().run(
        "您好，方便帮忙吗", CustomerContext(), greeting_config=config()
    )
    assert result.greeting_type == "salutation"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_model_timeout_safely_falls_back_to_other() -> None:
    result = await CustomerServiceAgent(intent_llm=FailingIntentLLM()).run(
        "有点像问候", CustomerContext(), greeting_config=config()
    )
    assert result.intent == "other"
    assert result.confidence == 0.0
    assert result.answer is None


@pytest.mark.asyncio
async def test_disabled_greeting_config_skips_model() -> None:
    llm = FakeIntentLLM("invalid")
    result = await CustomerServiceAgent(intent_llm=llm).run(
        "你好", CustomerContext(), greeting_config=config(enabled=False)
    )
    assert result.intent == "other"
    assert llm.calls == 0


def test_intent_decision_requires_greeting_type() -> None:
    with pytest.raises(ValueError):
        IntentDecision(intent="daily_greeting", confidence=0.99)
