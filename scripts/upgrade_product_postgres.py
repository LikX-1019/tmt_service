"""Apply and verify the shared PostgreSQL product profile view definition."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import psycopg
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_SQL_PATH = (
    PROJECT_ROOT / "docker" / "product-postgres" / "sql" / "product_api_profiles.sql"
)
PROFILE_VIEW_NAME = "product_api_profiles"
REQUIRED_PROFILE_FIELDS = frozenset(
    {
        "id",
        "name",
        "summary",
        "selling_points",
        "specifications",
        "usage",
        "suitable_for",
        "warnings",
        "after_sales_limits",
        "updated_at",
        "platform",
        "internal_code",
        "brand",
        "category_name",
        "model",
        "compliance_notes",
    }
)


class ProductPostgresSettings(BaseSettings):
    """Connection settings for the product PostgreSQL database."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    host: str = Field(default="127.0.0.1", validation_alias="PRODUCT_POSTGRES_HOST")
    port: int = Field(default=5432, ge=1, le=65535, validation_alias="PRODUCT_POSTGRES_PORT")
    database: str = Field(default="product_catalog", validation_alias="PRODUCT_POSTGRES_DB")
    user: str = Field(default="product_catalog", validation_alias="PRODUCT_POSTGRES_USER")
    password: SecretStr = Field(validation_alias="PRODUCT_POSTGRES_PASSWORD")
    app_env: str = Field(default="development", validation_alias="APP_ENV")

    def safe_target(self) -> dict[str, str | int]:
        return {
            "environment": self.app_env,
            "host": self.host,
            "port": self.port,
            "database": self.database,
        }


class ProductProfileSchemaError(RuntimeError):
    """Raised when the profile view is missing or incomplete."""


def load_profile_sql(path: Path = PROFILE_SQL_PATH) -> str:
    sql = path.read_text(encoding="utf-8").strip()
    marker = f"CREATE OR REPLACE VIEW {PROFILE_VIEW_NAME}"
    if marker not in sql:
        raise ProductProfileSchemaError(f"shared SQL does not define {PROFILE_VIEW_NAME}")
    return sql


def missing_required_fields(actual_fields: set[str]) -> set[str]:
    return set(REQUIRED_PROFILE_FIELDS) - actual_fields


def fetch_profile_fields(connection: Any) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relkind
            FROM pg_catalog.pg_class AS c
            JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = current_schema()
              AND c.relname = %s
            """,
            (PROFILE_VIEW_NAME,),
        )
        relation = cursor.fetchone()
        if relation is None or relation[0] not in {"v", "m"}:
            raise ProductProfileSchemaError(f"view {PROFILE_VIEW_NAME} does not exist")

        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
            ORDER BY ordinal_position
            """,
            (PROFILE_VIEW_NAME,),
        )
        return {row[0] for row in cursor.fetchall()}


def verify_profile_view(connection: Any) -> set[str]:
    actual_fields = fetch_profile_fields(connection)
    missing = missing_required_fields(actual_fields)
    if missing:
        names = ", ".join(sorted(missing))
        raise ProductProfileSchemaError(
            f"view {PROFILE_VIEW_NAME} is missing required fields: {names}"
        )
    return actual_fields


def connect_product_postgres(settings: ProductPostgresSettings) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=settings.host,
        port=settings.port,
        dbname=settings.database,
        user=settings.user,
        password=settings.password.get_secret_value(),
        connect_timeout=10,
        application_name="product_profile_schema_upgrade",
    )


def run_upgrade(settings: ProductPostgresSettings, *, check: bool) -> set[str]:
    target = settings.safe_target()
    print(
        "Product PostgreSQL target: "
        f"environment={target['environment']} host={target['host']} "
        f"port={target['port']} database={target['database']}"
    )
    print(f"Mode: {'check' if check else 'upgrade'}")

    sql = None if check else load_profile_sql()
    with connect_product_postgres(settings) as connection:
        if sql is not None:
            connection.execute(sql)
            connection.execute(
                "COMMENT ON VIEW product_api_profiles IS "
                "'供商品客服 API 返回的已发布商品事实字段；由正式升级脚本维护'"
            )
        actual_fields = verify_profile_view(connection)

    print(
        f"Verified {PROFILE_VIEW_NAME}: "
        f"required={len(REQUIRED_PROFILE_FIELDS)} actual={len(actual_fields)}"
    )
    return actual_fields


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upgrade or verify the PostgreSQL product profile view."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify connectivity and required fields without modifying the database",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = ProductPostgresSettings()
        run_upgrade(settings, check=args.check)
    except (OSError, ProductProfileSchemaError, ValidationError, psycopg.Error) as exc:
        print(f"Product PostgreSQL schema check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
