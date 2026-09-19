import pytest

from app.services.social_router import SocialRouter


@pytest.mark.asyncio
async def test_social_router_uses_deterministic_rule_first() -> None:
    result = await SocialRouter(llm=object()).classify("下次见")

    assert result.intent == "small_talk"
    assert result.source == "rule"
    assert result.response == "好的，下次再见，祝您生活愉快～"


@pytest.mark.asyncio
async def test_social_router_skips_business_questions() -> None:
    class UnexpectedLLM:
        async def ainvoke(self, *_args, **_kwargs):
            raise AssertionError("business question should not invoke LLM")

    result = await SocialRouter(llm=UnexpectedLLM()).classify("这个多少钱")

    assert result.intent == "other"
    assert result.source is None
