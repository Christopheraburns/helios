import pytest

from helios_cortex.index.store import duckdb_index_store


@pytest.fixture
def index():
    """An empty helios_index in an in-memory DuckDB, with every table created."""
    store = duckdb_index_store()
    store.ensure_tables()
    return store
