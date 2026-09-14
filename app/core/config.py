"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed configuration shared by the application."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "customer-service-agent"
    app_env: str = "local"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"
    default_tenant_id: str = "tenant_default"

    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "customer_service"
    mysql_user: str = "customer_service"
    mysql_password: SecretStr = SecretStr("")

    redis_host: str = "127.0.0.1"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr = SecretStr("")
    redis_db: int = Field(default=0, ge=0)

    milvus_host: str = "127.0.0.1"
    milvus_port: int = Field(default=19530, ge=1, le=65535)
    milvus_collection: str = "customer_service_knowledge"

    minio_endpoint: str = "127.0.0.1:9000"
    minio_root_user: str = "customer_service_minio"
    minio_root_password: SecretStr = SecretStr("")
    minio_secure: bool = False

    llm_provider: str = "openai"
    llm_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"
        ),
    )
    llm_base_url: str | None = Field(
        default="https://api.deepseek.com/v1",
        validation_alias=AliasChoices("LLM_BASE_URL", "DEEPSEEK_BASE_URL"),
    )
    llm_model: str = Field(
        default="deepseek-chat",
        validation_alias=AliasChoices(
            "LLM_MODEL", "DEEPSEEK_MODEL_CHAT", "DEEPSEEK_MODEL"
        ),
    )
    llm_model_routes: dict[str, str] = Field(default_factory=dict)
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    llm_structured_output_method: str = "function_calling"

    embedding_model_path: Path = PROJECT_ROOT / "model" / "bge-m3"
    reranker_model_path: Path = PROJECT_ROOT / "model" / "bge-reranker"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @field_validator("llm_provider", "llm_model")
    @classmethod
    def reject_blank_llm_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        return value or None

    @field_validator("llm_model_routes")
    @classmethod
    def reject_blank_routes(cls, value: dict[str, str]) -> dict[str, str]:
        routes: dict[str, str] = {}
        for agent_type, model in value.items():
            agent_type = agent_type.strip().lower()
            model = model.strip()
            if not agent_type or not model:
                raise ValueError("llm_model_routes keys and values must not be blank")
            routes[agent_type] = model
        return routes

    @property
    def database_url(self) -> str:
        """SQLAlchemy async URL with credentials safely escaped."""
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password.get_secret_value())
        database = quote_plus(self.mysql_database)
        return (
            f"mysql+aiomysql://{user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{database}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        password = self.redis_password.get_secret_value()
        auth = f":{quote_plus(password)}@" if password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def milvus_uri(self) -> str:
        return f"http://{self.milvus_host}:{self.milvus_port}"

    def require_llm_api_key(self) -> str:
        """Return the API key or fail when an LLM is first requested."""
        api_key = self.llm_api_key.get_secret_value() if self.llm_api_key else ""
        if not api_key.strip():
            raise ValueError(
                "LLM_API_KEY is required to create an LLM. "
                "Set it in the process environment or the project .env file."
            )
        return api_key

    def model_for(self, agent_type: str) -> str:
        return self.llm_model_routes.get(agent_type.lower(), self.llm_model)

    def safe_summary(self) -> dict[str, Any]:
        """Return non-secret fields suitable for startup diagnostics."""
        return {
            "app_env": self.app_env,
            "app_debug": self.app_debug,
            "mysql_host": self.mysql_host,
            "redis_host": self.redis_host,
            "milvus_uri": self.milvus_uri,
            "llm_provider": self.llm_provider,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
