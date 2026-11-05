"""Helios's registry backends pass the conformance suite (helios_core.conformance):
in memory, SqlRegistry on a SQLite file, and SqlRegistry on Postgres when
HELIOS_TEST_POSTGRES names a database URL.

    docker run -d -e POSTGRES_PASSWORD=helios -p 127.0.0.1:25432:5432 postgres:17
    HELIOS_TEST_POSTGRES=postgresql+psycopg://postgres:helios@127.0.0.1:25432/postgres uv run pytest ...
"""
import os

import pytest

from helios_core.conformance import RegistryConformance
from helios_core.registry import InMemoryRegistry, ModelRegistry, SqlRegistry
from helios_core.registry.sql import VERSION_TABLE, metadata

POSTGRES = os.environ.get("HELIOS_TEST_POSTGRES")


def drop_registry(url):
    import sqlalchemy as sa

    engine = sa.create_engine(url)
    with engine.begin() as connection:
        metadata.drop_all(connection)
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {VERSION_TABLE}"))
    engine.dispose()


def sql_registry(url):
    registry = SqlRegistry(url)
    registry.upgrade()
    return registry


@pytest.fixture(params=["memory", "sqlite", "postgres"])
def backend(request, tmp_path) -> ModelRegistry:
    if request.param == "memory":
        yield InMemoryRegistry()
    elif request.param == "sqlite":
        yield sql_registry(f"sqlite:///{tmp_path / 'registry.db'}")
    else:
        if not POSTGRES:
            pytest.skip("set HELIOS_TEST_POSTGRES to a Postgres URL to run against Postgres")
        drop_registry(POSTGRES)
        yield sql_registry(POSTGRES)
        drop_registry(POSTGRES)


class TestRegistryBackends(RegistryConformance):
    @pytest.fixture
    def registry(self, backend):
        return backend
