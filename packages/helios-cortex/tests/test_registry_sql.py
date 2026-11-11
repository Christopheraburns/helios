"""SqlRegistry specifics: URLs, the local default, Alembic migrations (burndown 4.2).
The registry contract itself runs on every backend in test_registry.py."""
import os
from datetime import timezone
from pathlib import Path

import pytest
import sqlalchemy as sa

from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.registry import RegistryNotReady, SqlRegistry
from helios_cortex.registry.__main__ import main
from helios_cortex.registry.sql import VERSION_TABLE, metadata
from helios_cortex.settings import Settings

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
    with pytest.raises(RegistryNotReady, match="python -m helios_cortex.registry upgrade"):
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
        assert "pip install 'helios-cortex[postgres]'" in str(exc)
        assert "hunter2" not in str(exc)
    else:
        assert "hunter2" not in repr(registry)


def test_the_command_upgrades_and_reports_the_schema(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HELIOS_REGISTRY_URL", f"sqlite:///{tmp_path / 'cli.db'}")
    assert main(["current"]) == 0
    assert "schema empty (this helios-cortex: 0001_registry)" in capsys.readouterr().out
    assert main(["upgrade"]) == 0
    assert "schema 0001_registry" in capsys.readouterr().out
    assert main([]) == 2
    assert Path(tmp_path / "cli.db").is_file()


def test_the_registry_survives_its_connections_being_dropped(clean_postgres):
    """A managed database's failover or idle timeout closes connections under the pool;
    the registry replaces them instead of failing the next call (burndown 4.7)."""
    if not POSTGRES:
        pytest.skip("set HELIOS_TEST_POSTGRES to a Postgres URL to run against Postgres")
    registry = SqlRegistry(POSTGRES)
    registry.upgrade()
    registry.save_data_source(DataSource("warehouse", "Warehouse", "impala", "default"))

    admin = sa.create_engine(POSTGRES)
    with admin.begin() as connection:
        dropped = connection.execute(sa.text(
            "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity "
            "WHERE datname = current_database() AND pid <> pg_backend_pid() AND backend_type = 'client backend'"
        )).scalar()
    admin.dispose()

    assert dropped >= 1
    assert [d.id for d in registry.data_sources()] == ["warehouse"]
