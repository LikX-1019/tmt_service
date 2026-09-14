"""Central factory for chat models used by agent nodes."""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable


OutputModel = TypeVar("OutputModel", bound=BaseModel)

SUPPORTED_AGENT_TYPES = frozenset(
    {
        "guard",
        "intent",
        "rag",
        "query_rewrite",
        "response",
        "summarize",
    }
)


def _init_chat_model(**kwargs: Any) -> BaseChatModel:
    """Import LangChain only when the first model is actually requested."""
    from langchain.chat_models import init_chat_model

    return init_chat_model(**kwargs)


class LLMFactory:
    """Create and cache LLMs by scenario and runtime options."""

    _instances: dict[tuple[str, str, str | None, float, bool], BaseChatModel] = {}
    _lock = Lock()

    @staticmethod
    def _get_settings() -> Settings:
        return get_settings()

    @classmethod
    def _validate_agent_type(cls, agent_type: str) -> str:
        normalized = agent_type.strip().lower()
        if normalized not in SUPPORTED_AGENT_TYPES:
            available = ", ".join(sorted(SUPPORTED_AGENT_TYPES))
            raise ValueError(
                f"Unknown agent_type {agent_type!r}. Available types: {available}"
            )
        return normalized

    @classmethod
    def _build_model_kwargs(
        cls,
        settings: Settings,
        model: str,
        temperature: float,
        streaming: bool,
    ) -> dict[str, Any]:
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

        kwargs: dict[str, Any] = {
            "model": model,
            "model_provider": settings.llm_provider,
            "api_key": settings.require_llm_api_key(),
            "temperature": temperature,
            "streaming": streaming,
            "max_retries": settings.llm_max_retries,
            "timeout": settings.llm_timeout_seconds,
        }
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        return kwargs

    @classmethod
    def get_llm(
        cls,
        agent_type: str,
        temperature: float = 0.0,
        streaming: bool = False,
    ) -> BaseChatModel:
        """Return a cached chat model configured for an agent scenario."""
        agent_type = cls._validate_agent_type(agent_type)
        settings = cls._get_settings()
        model = settings.model_for(agent_type)
        cache_key = (
            settings.llm_provider,
            model,
            settings.llm_base_url,
            temperature,
            streaming,
        )

        if cache_key not in cls._instances:
            with cls._lock:
                if cache_key not in cls._instances:
                    kwargs = cls._build_model_kwargs(
                        settings, model, temperature, streaming
                    )
                    cls._instances[cache_key] = _init_chat_model(**kwargs)
        return cls._instances[cache_key]

    @classmethod
    def get_structured_llm(
        cls,
        agent_type: str,
        output_schema: type[OutputModel],
        temperature: float = 0.0,
    ) -> Runnable[Any, OutputModel]:
        """Return an LLM whose output is parsed into ``output_schema``."""
        if not isinstance(output_schema, type) or not issubclass(
            output_schema, BaseModel
        ):
            raise TypeError("output_schema must be a Pydantic BaseModel class")

        settings = cls._get_settings()
        llm = cls.get_llm(agent_type, temperature=temperature)
        return llm.with_structured_output(
            output_schema,
            method=settings.llm_structured_output_method,
        )

    @classmethod
    def clear_cache(cls) -> None:
        """Clear cached model instances, primarily for tests and reloading."""
        with cls._lock:
            cls._instances.clear()


def get_llm(
    agent_type: str,
    temperature: float = 0.0,
    streaming: bool = False,
) -> BaseChatModel:
    return LLMFactory.get_llm(agent_type, temperature, streaming)


def get_structured_llm(
    agent_type: str,
    output_schema: type[OutputModel],
    temperature: float = 0.0,
) -> Runnable[Any, OutputModel]:
    return LLMFactory.get_structured_llm(
        agent_type,
        output_schema,
        temperature,
    )
