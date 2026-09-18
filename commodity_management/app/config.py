from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from psycopg.conninfo import make_conninfo


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    database_host: str = "product-postgres"
    database_port: int = Field(default=5432, ge=1, le=65535)
    database_name: str = "product_catalog"
    database_user: str = "product_catalog"
    database_password: str = Field(min_length=1)

    commodity_admin_username: str = Field(default="admin", min_length=1, max_length=100)
    commodity_admin_password: str = Field(min_length=12)
    commodity_session_secret: str = Field(min_length=32)
    product_api_bearer_token: str = Field(min_length=24)

    def database_dsn(self) -> str:
        return make_conninfo(
            host=self.database_host,
            port=self.database_port,
            dbname=self.database_name,
            user=self.database_user,
            password=self.database_password,
            connect_timeout=5,
            application_name="commodity_management",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
