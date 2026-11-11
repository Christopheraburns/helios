"""Engine identity modes: whose identity a query runs as (burndown 2.2).

The Impala engine, which supports every mode, is tested in helios-cloudera.
"""
import pytest

from helios_core import Caller, IdentityMode
from helios_core.engines.duckdb_engine import DuckDBEngine


def test_duckdb_supports_service_only():
    assert DuckDBEngine.identity_modes == {IdentityMode.SERVICE}
    with pytest.raises(ValueError, match="does not support identity mode 'impersonate'"):
        DuckDBEngine(identity_mode="impersonate")
    engine = DuckDBEngine()
    assert engine.query("SELECT 7", Caller("alice", token="t")).rows == [(7,)]
    assert engine.query("SELECT 7", limit=1).rows == [(7,)]
