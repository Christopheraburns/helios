"""SqlRegistry specifics: URLs, the local default, Alembic migrations (burndown 4.2).
The registry contract itself runs on every backend in test_registry.py."""
import os
from datetime import timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from helios_core.domain import DataSource, DataSourceReference, Model
from helios_core.registry import RegistryNotReady, SqlRegistry
from helios_core.registry.__main__ import main
from helios_core.registry.sql import VERSION_TABLE, metadata
from helios_core.settings import Settings

POSTGRES = os.environ.get("HELIOS_TEST_POSTGRES")


def urls(tmp_path):
    found = [f"sqlite:///{tmp_path / 'registry.db'}"]
    return found + [POSTGRES] if POSTGRES else found


@pytest.fixture
def clean_postgres():
    from test_registry import drop_registry

    if POSTGRES:
        drop_registry(POSTGRES)
    yield
    if POSTGRES:
        drop_registry(POSTGRES)


def test_a_database_must_be_upgraded_before_use(tmp_path):
    registry = SqlRegistry(f"sqlite:///{tmp_path / 'registry.db'}")
    assert registry.current_revision() is None
    with pytest.raises(RegistryNotReady, match="python -m helios_core.registry upgrade"):
        registry.models()
    registry.upgrade()
    assert registry.current_revision() == registry.head_revision() == "0001_registry"
    assert registry.models() == []
    registry.upgrade()   # idempotent


def test_the_local_default_is_a_sqlite_file_in_the_data_directory(tmp_path):
    settings = Settings(data_dir=tmp_path / "helios")
    assert settings.registry_url.get_secret_value() == f"sqlite:///{tmp_path / 'helios' / 'state' / 'registry.db'}"
    registry = SqlRegistry.from_settings(settings)          # creates the directory and the schema
    registry.save_data_source(DataSource("w", "Warehouse", "duckdb", "default"))
    registry.save_model(Model("sales", "Sales", (DataSourceReference("w"),)), created_by="alice")

    reopened = SqlRegistry.from_settings(settings)
    stored = reopened.registered_model("sales")
    assert (stored.model.name, stored.created_by) == ("Sales", "alice")
    assert stored.created_at.tzinfo is timezone.utc
    assert (tmp_path / "helios" / "state" / "registry.db").is_file()


def test_a_shared_database_is_not_upgraded_without_being_asked(tmp_path):
    settings = Settings(registry_url=f"sqlite:///{tmp_path / 'shared.db'}")
    assert SqlRegistry.from_settings(settings, upgrade=False).current_revision() is None
    if POSTGRES:
        assert SqlRegistry.from_settings(Settings(registry_url=POSTGRES)).current_revision() is None


def test_the_migrations_build_exactly_the_tables_the_registry_uses(tmp_path, clean_postgres):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    for url in urls(tmp_path):
        SqlRegistry(url).upgrade()
        engine = sa.create_engine(url)
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"version_table": VERSION_TABLE})
            assert compare_metadata(context, metadata) == [], url
        engine.dispose()


def test_urls_and_settings_never_show_the_password():
    url = "postgresql+psycopg://helios:hunter2@db.example.com/helios"
    assert "hunter2" not in repr(Settings(registry_url=url))
    try:
        registry = SqlRegistry(url)
    except ModuleNotFoundError as exc:  # a base install: no Postgres driver
        assert "pip install 'helios-core[postgres]'" in str(exc)
        assert "hunter2" not in str(exc)
    else:
        assert "hunter2" not in repr(registry)


def test_the_command_upgrades_and_reports_the_schema(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HELIOS_REGISTRY_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    assert main(["current"]) == 0
    assert "schema empty (this helios-core: 0001_registry)" in capsys.readouterr().out
    assert main(["upgrade"]) == 0
    assert "schema 0001_registry" in capsys.readouterr().out
    assert main([]) == 2
    assert Path(tmp_path / "cli.db").is_file()
