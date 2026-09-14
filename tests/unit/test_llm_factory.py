from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from app.core.config import Settings
from app.core.exceptions import UnsupportedLLMProviderError
from app.factories import llm_factory
from app.factories.llm_factory import LLMFactory


class FakeChatModel:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.structured_args: tuple[Any, ...] | None = None

    def with_structured_output(self, *args: Any, **kwargs: Any) -> Any:
        self.structured_args = (*args, kwargs)
        return self


class Result(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def clear_factory_cache() -> None:
    LLMFactory.clear_cache()
    yield
    LLMFactory.clear_cache()


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        llm_api_key=SecretStr("test-key"),
        llm_model="default-model",
        llm_model_routes={"intent": "intent-model"},
    )


def test_get_llm_routes_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[FakeChatModel] = []

    def fake_init(**kwargs: Any) -> FakeChatModel:
        model = FakeChatModel(kwargs)
        created.append(model)
        return model

    monkeypatch.setattr(LLMFactory, "_get_settings", staticmethod(make_settings))
    monkeypatch.setattr(llm_factory, "_init_chat_model", fake_init)

    first = LLMFactory.get_llm("intent", temperature=0.2)
    second = LLMFactory.get_llm("intent", temperature=0.2)

    assert first is second
    assert len(created) == 1
    assert created[0].kwargs["model"] == "intent-model"
    assert created[0].kwargs["api_key"] == "test-key"


def test_cache_separates_streaming_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LLMFactory, "_get_settings", staticmethod(make_settings))
    monkeypatch.setattr(
        llm_factory,
        "_init_chat_model",
        lambda **kwargs: FakeChatModel(kwargs),
    )

    regular = LLMFactory.get_llm("response")
    streaming = LLMFactory.get_llm("response", streaming=True)

    assert regular is not streaming


def test_structured_llm_binds_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(LLMFactory, "_get_settings", staticmethod(make_settings))
    monkeypatch.setattr(
        llm_factory,
        "_init_chat_model",
        lambda **kwargs: FakeChatModel(kwargs),
    )

    model = LLMFactory.get_structured_llm("intent", Result)

    assert model.structured_args == (Result, {"method": "function_calling"})


def test_unknown_agent_type_fails_before_model_creation() -> None:
    with pytest.raises(ValueError, match="Unknown agent_type"):
        LLMFactory.get_llm("unknown")


def test_missing_api_key_has_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, llm_api_key=None)
    monkeypatch.setattr(LLMFactory, "_get_settings", staticmethod(lambda: settings))

    with pytest.raises(ValueError, match="LLM_API_KEY"):
        LLMFactory.get_llm("response")


def test_create_accepts_model_and_temperature_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(LLMFactory, "_get_settings", staticmethod(make_settings))
    monkeypatch.setattr(
        llm_factory,
        "_init_chat_model",
        lambda **kwargs: FakeChatModel(kwargs),
    )

    model = LLMFactory.create(model="override-model", temperature=0.6)

    assert model.kwargs["model"] == "override-model"
    assert model.kwargs["temperature"] == 0.6


def test_unsupported_provider_uses_domain_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings().model_copy(update={"llm_provider": "unknown"})
    monkeypatch.setattr(LLMFactory, "_get_settings", staticmethod(lambda: settings))

    with pytest.raises(UnsupportedLLMProviderError):
        LLMFactory.create()
