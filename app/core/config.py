"""应用配置模块，统一从环境变量和项目根目录的 .env 文件加载配置。"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote_plus

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """集中定义应用共享的强类型配置，并负责基础格式校验。"""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Customer Service Agent"
    app_env: str = "local"
    app_debug: bool = Field(
        default=False,
        validation_alias=AliasChoices("APP_DEBUG", "DEBUG"),
    )
    app_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("APP_HOST", "HOST"),
    )
    app_port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("APP_PORT", "PORT"),
    )
    sqlalchemy_database_url: str | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
        exclude=True,
    )
    log_level: str = "INFO"
    log_dir: Path = PROJECT_ROOT / "logs"
    default_tenant_id: str = "tenant_default"
    default_shop_name: str = "拼多多店铺"

    pdd_chat_url: str = "https://mms.pinduoduo.com/chat-merchant/index.html"
    pdd_chrome_executable: Path | None = None
    pdd_chrome_profile_dir: Path = PROJECT_ROOT / "data" / "runtime" / "pdd-chrome"
    pdd_poll_interval_seconds: float = Field(default=1.0, ge=0.5, le=10)
    pdd_response_timeout_seconds: int = Field(default=160, ge=1)
    message_asset_dir: Path = PROJECT_ROOT / "data" / "message-assets"
    message_asset_max_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)

    auto_reply_debounce_seconds: float = Field(default=2.0, ge=0.5, le=30)
    auto_reply_policy_version: str = "v1"
    auto_reply_calibration_path: Path = (
        PROJECT_ROOT / "data" / "runtime" / "auto_reply_calibration.json"
    )
    auto_reply_min_precision: float = Field(default=0.98, ge=0.5, le=1)
    auto_reply_min_samples: int = Field(default=100, ge=1)
    auto_reply_rag_mode: Literal["suggest_only", "calibrated", "immediate"] = (
        "calibrated"
    )
    auto_reply_rag_min_margin: float = Field(default=0.10, ge=0)
    message_retention_days: int = Field(default=30, ge=1)
    audit_retention_days: int = Field(default=90, ge=1)

    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "customer_service"
    mysql_user: str = "customer_service"
    mysql_password: SecretStr = SecretStr("")

    milvus_host: str = "127.0.0.1"
    milvus_port: int = Field(default=19530, ge=1, le=65535)
    milvus_collection: str = "customer_service_knowledge_v1"
    milvus_schema_version: int = Field(default=1, ge=1)
    embedding_version: str = "v1"
    embedding_max_concurrency: int = Field(default=2, ge=1, le=16)
    reranker_max_concurrency: int = Field(default=1, ge=1, le=16)

    qa_data_source: str = "mysql"
    qa_excel_path: Path = PROJECT_ROOT / "data" / "qa" / "整理结果" / "智能客服标准QA整理.xlsx"
    qa_excel_sheet: str = "标准QA"

    embedding_provider: str = "sentence_transformers"
    embedding_model: str | None = None
    vector_store: str = "milvus"

    rag_dense_top_k: int = Field(default=20, ge=1, le=100)
    rag_bm25_top_k: int = Field(default=20, ge=1, le=100)
    rag_fusion_top_k: int = Field(default=20, ge=1, le=100)
    rag_rerank_top_k: int = Field(default=5, ge=1, le=50)
    rrf_k: int = Field(default=60, ge=1)
    rag_score_threshold: float = 0.35

    reranker_provider: str = "sentence_transformers"
    reranker_model: str | None = None

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
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    llm_warmup_on_startup: bool = True
    llm_structured_output_method: str = "function_calling"

    embedding_model_path: Path = PROJECT_ROOT / "model" / "bge-m3"
    reranker_model_path: Path = PROJECT_ROOT / "model" / "bge-reranker"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        """将日志级别统一为大写，并拒绝 logging 不支持的级别。"""
        level = value.strip().upper()
        allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        """只接受明确的运行环境名称，避免拼写错误静默改变安全默认值。"""
        environment = value.strip().lower()
        aliases = {"local": "development", "dev": "development", "prod": "production"}
        environment = aliases.get(environment, environment)
        if environment not in {"development", "test", "production"}:
            raise ValueError("app_env must be development, test, or production")
        return environment

    @field_validator(
        "log_dir",
        "qa_excel_path",
        "embedding_model_path",
        "reranker_model_path",
        "pdd_chrome_profile_dir",
        "auto_reply_calibration_path",
        "message_asset_dir",
    )
    @classmethod
    def resolve_log_dir(cls, value: Path) -> Path:
        """将相对日志目录固定到项目根目录，避免受启动目录影响。"""
        value = value.expanduser()
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator("pdd_chrome_executable")
    @classmethod
    def resolve_optional_path(cls, value: Path | None) -> Path | None:
        """解析可选浏览器路径；留空时由 Playwright 使用系统 Chrome。"""
        if value is None:
            return None
        value = value.expanduser()
        return value if value.is_absolute() else PROJECT_ROOT / value

    @field_validator(
        "llm_provider",
        "llm_model",
        "qa_data_source",
        "embedding_provider",
        "vector_store",
        "reranker_provider",
    )
    @classmethod
    def reject_blank_llm_values(cls, value: str) -> str:
        """清理模型配置两端空白，并禁止空的提供商或模型名称。"""
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("llm_base_url")
    @classmethod
    def normalize_base_url(cls, value: str | None) -> str | None:
        """规范模型服务地址；空字符串表示使用 SDK 默认地址。"""
        if value is None:
            return None
        value = value.strip().rstrip("/")
        return value or None

    @field_validator("llm_model_routes")
    @classmethod
    def reject_blank_routes(cls, value: dict[str, str]) -> dict[str, str]:
        """规范场景模型路由，避免空键或空模型名进入运行阶段。"""
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
        """生成已安全转义账号密码的 SQLAlchemy 异步连接地址。"""
        if self.sqlalchemy_database_url:
            return self.sqlalchemy_database_url
        user = quote_plus(self.mysql_user)
        password = quote_plus(self.mysql_password.get_secret_value())
        database = quote_plus(self.mysql_database)
        return (
            f"mysql+aiomysql://{user}:{password}"
            f"@{self.mysql_host}:{self.mysql_port}/{database}?charset=utf8mb4"
        )

    @property
    def milvus_uri(self) -> str:
        """生成 Milvus HTTP 连接地址。"""
        return f"http://{self.milvus_host}:{self.milvus_port}"

    def require_llm_api_key(self) -> str:
        """首次创建模型时返回 API Key；未配置时立即给出明确错误。"""
        api_key = self.llm_api_key.get_secret_value() if self.llm_api_key else ""
        if not api_key.strip():
            raise ValueError(
                "LLM_API_KEY is required to create an LLM. "
                "Set it in the process environment or the project .env file."
            )
        return api_key

    def model_for(self, agent_type: str) -> str:
        """返回指定业务场景的模型，未单独配置时回退到默认模型。"""
        return self.llm_model_routes.get(agent_type.lower(), self.llm_model)

    def safe_summary(self) -> dict[str, Any]:
        """返回可用于启动诊断的非敏感配置，确保不会泄露密钥。"""
        return {
            "app_env": self.app_env,
            "app_debug": self.app_debug,
            "log_level": self.log_level,
            "log_dir": str(self.log_dir),
            "mysql_host": self.mysql_host,
            "milvus_uri": self.milvus_uri,
            "milvus_collection": self.milvus_collection,
            "milvus_schema_version": self.milvus_schema_version,
            "qa_data_source": self.qa_data_source,
            "qa_excel_path": str(self.qa_excel_path),
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model or str(self.embedding_model_path),
            "embedding_version": self.embedding_version,
            "embedding_max_concurrency": self.embedding_max_concurrency,
            "vector_store": self.vector_store,
            "reranker_provider": self.reranker_provider,
            "reranker_model": self.reranker_model or str(self.reranker_model_path),
            "reranker_max_concurrency": self.reranker_max_concurrency,
            "llm_provider": self.llm_provider,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "llm_temperature": self.llm_temperature,
            "llm_warmup_on_startup": self.llm_warmup_on_startup,
            "pdd_chat_url": self.pdd_chat_url,
            "pdd_chrome_profile_dir": str(self.pdd_chrome_profile_dir),
            "pdd_poll_interval_seconds": self.pdd_poll_interval_seconds,
            "message_asset_dir": str(self.message_asset_dir),
            "message_asset_max_bytes": self.message_asset_max_bytes,
            "auto_reply_policy_version": self.auto_reply_policy_version,
            "message_retention_days": self.message_retention_days,
            "audit_retention_days": self.audit_retention_days,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级缓存配置，避免在每次请求中重复读取环境变量。"""
    return Settings()
