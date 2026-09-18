from pathlib import Path

import pytest

from scripts import upgrade_product_postgres


def test_shared_profile_sql_is_the_single_current_view_definition() -> None:
    sql = upgrade_product_postgres.load_profile_sql()
    init_sql = (
        upgrade_product_postgres.PROJECT_ROOT
        / "docker"
        / "product-postgres"
        / "init"
        / "002_product_api_profile_fields.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE OR REPLACE VIEW product_api_profiles" in sql
    assert "\\ir /opt/product-postgres/product_api_profiles.sql" in init_sql
    assert "CREATE OR REPLACE VIEW product_api_profiles" not in init_sql


def test_load_profile_sql_rejects_unrelated_file(tmp_path: Path) -> None:
    path = tmp_path / "wrong.sql"
    path.write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(upgrade_product_postgres.ProductProfileSchemaError):
        upgrade_product_postgres.load_profile_sql(path)


def test_required_profile_fields_accept_superset() -> None:
    actual = set(upgrade_product_postgres.REQUIRED_PROFILE_FIELDS) | {"future_field"}

    assert upgrade_product_postgres.missing_required_fields(actual) == set()


def test_required_profile_fields_report_missing_names() -> None:
    actual = set(upgrade_product_postgres.REQUIRED_PROFILE_FIELDS) - {"brand", "model"}

    assert upgrade_product_postgres.missing_required_fields(actual) == {"brand", "model"}


def test_verification_fails_with_missing_field_names(monkeypatch: pytest.MonkeyPatch) -> None:
    actual = set(upgrade_product_postgres.REQUIRED_PROFILE_FIELDS) - {"brand", "model"}
    monkeypatch.setattr(
        upgrade_product_postgres,
        "fetch_profile_fields",
        lambda _connection: actual,
    )

    with pytest.raises(
        upgrade_product_postgres.ProductProfileSchemaError,
        match=r"brand, model",
    ):
        upgrade_product_postgres.verify_profile_view(object())


def test_check_mode_does_not_load_or_execute_upgrade_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeConnection:
        def __enter__(self) -> "FakeConnection":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def execute(self, _sql: str) -> None:
            raise AssertionError("check mode must not execute upgrade SQL")

    settings = upgrade_product_postgres.ProductPostgresSettings(
        _env_file=None,
        password="test-only-password",
        app_env="test",
    )
    monkeypatch.setattr(
        upgrade_product_postgres,
        "connect_product_postgres",
        lambda _settings: FakeConnection(),
    )
    monkeypatch.setattr(
        upgrade_product_postgres,
        "verify_profile_view",
        lambda _connection: set(upgrade_product_postgres.REQUIRED_PROFILE_FIELDS),
    )
    monkeypatch.setattr(
        upgrade_product_postgres,
        "load_profile_sql",
        lambda: (_ for _ in ()).throw(AssertionError("SQL must not be loaded")),
    )

    actual = upgrade_product_postgres.run_upgrade(settings, check=True)

    assert actual == set(upgrade_product_postgres.REQUIRED_PROFILE_FIELDS)
