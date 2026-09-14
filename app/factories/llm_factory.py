"""大模型工厂，集中创建并缓存 OpenAI-compatible 对话模型。"""

from __future__ import annotations

from threading import Lock
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.exceptions import UnsupportedLLMProviderError

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
    """延迟导入并创建 ChatOpenAI，避免应用导入阶段提前初始化模型。"""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(**kwargs)


class LLMFactory:
    """根据业务场景和运行参数创建、缓存并复用大模型实例。"""

    _instances: dict[tuple[str, str, str | None, float, bool], BaseChatModel] = {}
    _lock = Lock()

    @staticmethod
    def _get_settings() -> Settings:
        """获取统一配置；单独封装便于测试替换。"""
        return get_settings()

    @classmethod
    def _validate_agent_type(cls, agent_type: str) -> str:
        """规范并校验业务场景名称，防止拼写错误静默回退。"""
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
        """从安全配置中组装 ChatOpenAI 初始化参数。"""
        if settings.llm_provider.strip().lower() != "openai":
            raise UnsupportedLLMProviderError(
                details={"provider": settings.llm_provider}
            )
        if not 0 <= temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")

        kwargs: dict[str, Any] = {
            "model": model,
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
        model: str | None = None,
    ) -> BaseChatModel:
        """返回指定业务场景的缓存模型，相同配置不会重复创建客户端。"""
        agent_type = cls._validate_agent_type(agent_type)
        settings = cls._get_settings()
        model = (model or settings.model_for(agent_type)).strip()
        if not model:
            raise ValueError("model must not be blank")
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
    def create(
        cls,
        model: str | None = None,
        temperature: float | None = None,
        *,
        streaming: bool = False,
    ) -> BaseChatModel:
        """创建第一阶段回复模型，并允许临时覆盖模型名和温度。"""
        settings = cls._get_settings()
        return cls.get_llm(
            "response",
            temperature=(
                settings.llm_temperature if temperature is None else temperature
            ),
            streaming=streaming,
            model=model,
        )

    @classmethod
    def get_structured_llm(
        cls,
        agent_type: str,
        output_schema: type[OutputModel],
        temperature: float = 0.0,
    ) -> Runnable[Any, OutputModel]:
        """返回可将输出解析为指定 Pydantic 模型的结构化模型。"""
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
        """清空模型实例缓存，主要供自动化测试和配置重载使用。"""
        with cls._lock:
            cls._instances.clear()


def get_llm(
    agent_type: str,
    temperature: float = 0.0,
    streaming: bool = False,
) -> BaseChatModel:
    """提供函数式入口，获取指定业务场景的大模型。"""
    return LLMFactory.get_llm(agent_type, temperature, streaming)


def get_structured_llm(
    agent_type: str,
    output_schema: type[OutputModel],
    temperature: float = 0.0,
) -> Runnable[Any, OutputModel]:
    """提供函数式入口，获取带结构化输出约束的大模型。"""
    return LLMFactory.get_structured_llm(
        agent_type,
        output_schema,
        temperature,
    )
