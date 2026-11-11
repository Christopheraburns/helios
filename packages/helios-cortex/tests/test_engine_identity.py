"""Engine identity modes: whose identity a query runs as (burndown 2.2).

The Impala engine, which supports every mode, is tested in helios-cloudera.
"""
import pytest

from helios_cortex import Caller, IdentityMode
from helios_cortex.engines.duckdb_engine import DuckDBEngine


def test_duckdb_supports_service_only():
    assert DuckDBEngine.identity_modes == {IdentityMode.SERVICE}
    with pytest.raises(ValueError, match="does not support identity mode 'impersonate'"):
        DuckDBEngine(identity_mode="impersonate")
    engine = DuckDBEngine()
    assert engine.query("SELECT 7", Caller("alice", token="t")).rows == [(7,)]
    assert engine.query("SELECT 7", limit=1).rows == [(7,)]


class Recording(DuckDBEngine):
    """DuckDB that records whose identity each statement was asked to run as."""

    def __init__(self):
        super().__init__()
        self.callers = []

    def query(self, sql, caller=None, *, limit=1000):
        self.callers.append(caller)
        return super().query(sql, caller, limit=limit)


def test_catalog_reads_forward_the_caller_to_query():
    """Harvesting as a caller (burndown 5.4): the information_schema defaults and the
    COUNT(*) row count pass the caller on, so the engine's identity mode applies to them."""
    engine = Recording()
    engine.query("CREATE SCHEMA shop")
    engine.query("CREATE TABLE shop.orders (o_id INTEGER)")
    engine.callers.clear()
    alice = Caller("alice")
    assert engine.databases(caller=alice) == ["main", "shop"]
    assert engine.tables("shop", caller=alice) == ["orders"]
    assert engine.columns("shop", "orders", caller=alice) == [{"name": "o_id", "type": "INTEGER", "comment": ""}]
    assert engine.row_count("shop", "orders", caller=alice) == 0
    assert engine.recent_queries(10, caller=alice) is None
    assert engine.callers == [alice] * 4
    engine.callers.clear()
    engine.tables("shop")
    assert engine.callers == [None]                          # no caller: the service identity


def test_sqlalchemy_introspection_accepts_a_caller(tmp_path):
    pytest.importorskip("sqlalchemy", reason="needs helios-cortex[sqlalchemy]")
    from helios_cortex.engines.sqlalchemy_engine import SqlAlchemyEngine

    engine = SqlAlchemyEngine(f"sqlite:///{tmp_path / 'shop.db'}")
    engine.query("CREATE TABLE orders (o_id INTEGER)")
    alice = Caller("alice")
    assert engine.tables("main", caller=alice) == ["orders"]
    assert [c["name"] for c in engine.columns("main", "orders", caller=alice)] == ["o_id"]
    assert "main" in engine.databases(caller=alice)
