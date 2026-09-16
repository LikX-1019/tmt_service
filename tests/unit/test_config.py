from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import PROJECT_ROOT, Settings


def test_settings_read_phase_one_environment(
    monkeypatch,
) -> None:
    monkeypatch.setenv("APP_NAME", "Test Customer Service")
    monkeypatch.setenv("DEBUG", "true")
    monkeypatch.setenv("PORT", "9100")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.4")

    settings = Settings(_env_file=None)

    assert settings.app_name == "Test Customer Service"
    assert settings.app_debug is True
    assert settings.app_port == 9100
    assert settings.llm_temperature == 0.4


def test_connection_urls_escape_passwords() -> None:
    settings = Settings(
        _env_file=None,
        mysql_user="customer service",
        mysql_password=SecretStr("p@ss/word"),
    )

    assert "customer+service:p%40ss%2Fword" in settings.database_url


def test_database_url_can_be_overridden_for_isolated_migrations() -> None:
    settings = Settings(_env_file=None, sqlalchemy_database_url="sqlite+aiosqlite:///test.db")

    assert settings.database_url == "sqlite+aiosqlite:///test.db"


@pytest.mark.parametrize(
    ("value", "expected"),
    [("local", "development"), ("test", "test"), ("prod", "production")],
)
def test_settings_normalize_supported_environments(value: str, expected: str) -> None:
    assert Settings(_env_file=None, app_env=value).app_env == expected


def test_settings_reject_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="stagin")


def test_relative_log_dir_is_anchored_to_project() -> None:
    settings = Settings(_env_file=None, log_dir="runtime-logs")

    assert settings.log_dir == PROJECT_ROOT / "runtime-logs"


def test_model_route_falls_back_to_default() -> None:
    settings = Settings(
        _env_file=None,
        llm_model="default-model",
        llm_model_routes={"intent": "fast-model"},
    )

    assert settings.model_for("intent") == "fast-model"
    assert settings.model_for("response") == "default-model"


def test_safe_summary_does_not_expose_secrets() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key=SecretStr("top-secret"),
        mysql_password=SecretStr("db-secret"),
    )

    summary = repr(settings.safe_summary())
    assert "top-secret" not in summary
    assert "db-secret" not in summary
