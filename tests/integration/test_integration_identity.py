"""Whose identity a query runs as, end to end (burndown 6.7; the Phase 2 gate): helios-serve's
run_query, the default DataPolicy, the connection's identity mode, and helios-cloudera's
real ImpalaEngine, built from Settings through the engine plugin, down to the impyla
connect call. Moved from apps/helios/tests/test_talk_to_data.py, where it went through
the old MCP server."""
import json
from pathlib import Path

import impala.dbapi as impala_dbapi
import pytest

from helios_cortex import Caller
from helios_cortex.artifacts import ArtifactStore
from helios_cortex.domain import DataSource, DataSourceReference, Model
from helios_cortex.ossie import dump_yaml
from helios_cortex.registry import InMemoryRegistry
from helios_cortex.settings import Settings
from helios_serve import ModelStore, Stores, tools

REPO = Path(__file__).resolve().parents[2]
CUSTOMER360 = json.loads((REPO / "packages/helios-serve/tests/fixtures/customer360.ossie.json").read_text())

CONNECTIONS = {
    "impersonate": {"engine": "impala", "identity": "impersonate", "host": "warehouse.example", "port": 443,
                    "user": "helios-service", "password": "secret"},
    "token": {"engine": "impala", "identity": "token", "host": "warehouse.example", "port": 443},
}


class Cursor:
    def __init__(self, effective_user):
        self.effective_user = effective_user
        self.description = None
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)
        self.description = [("effective_user",)] if sql == "SELECT EFFECTIVE_USER()" else [("customer_count",)]

    def fetchone(self):
        return (self.effective_user,)

    def fetchmany(self, _limit):
        return [(7,)]


class Connection:
    def __init__(self, effective_user="owner"):
        self.cursor_value = Cursor(effective_user)

    def cursor(self):
        return self.cursor_value

    def close(self):
        pass


class HttpUnauthorized(Exception):
    code = 401


class RefusedConnection:
    """impyla's connection when the warehouse refuses the credential: opening a cursor fails."""

    def cursor(self):
        raise HttpUnauthorized("HTTP code 401: Unauthorized")

    def close(self):
        raise AttributeError("'NoneType' object has no attribute 'close'")


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    """``warehouse(mode, connection)``: Stores whose model runs on an Impala connection in
    ``mode``, with impyla's connect answering ``connection``; returns them and the connect calls."""
    artifacts = ArtifactStore(tmp_path / "artifacts")
    artifacts.write_published_ossie("customer360", CUSTOMER360, dump_yaml(CUSTOMER360), {"run_id": "r1"})
    registry = InMemoryRegistry()
    registry.save_data_source(DataSource("warehouse", "Warehouse", "impala", "default"))
    registry.save_model(Model("customer360", "Customer 360", (DataSourceReference("warehouse"),)))

    def configure(mode, connection):
        calls = []

        def connect(**kwargs):
            calls.append(kwargs)
            return connection

        monkeypatch.setattr(impala_dbapi, "connect", connect)
        settings = Settings(data_dir=tmp_path / "data", connections={"default": CONNECTIONS[mode]})
        return Stores(settings, registry, ModelStore(artifacts)), calls      # the default DataPolicy

    return configure


def run(stores, caller):
    return tools.run_query(caller, stores, metrics=["Customer count"], model="customer360")


def test_impersonate_mode_runs_the_query_as_the_caller(warehouse):
    connection = Connection("owner")
    stores, calls = warehouse("impersonate", connection)
    result = run(stores, Caller("owner"))
    assert result["rows"] == [[7]]
    [call] = calls
    assert (call["auth_mechanism"], call["user"]) == ("LDAP", "helios-service")
    assert "doAs=owner" in call["http_path"]
    assert connection.cursor_value.executed == ["SELECT EFFECTIVE_USER()", result["sql"]]


def test_impersonate_mode_stops_when_impala_runs_as_someone_else(warehouse):
    stores, _ = warehouse("impersonate", Connection("helios-service"))
    result = run(stores, Caller("owner"))
    assert result["error"] == "proxy_delegation_denied" and not result["retryable"]


def test_token_mode_sends_the_callers_token(warehouse):
    connection = Connection("owner")
    stores, calls = warehouse("token", connection)
    result = run(stores, Caller("owner", "caller-jwt"))
    assert result["rows"] == [[7]]
    [call] = calls
    assert (call["auth_mechanism"], call["jwt"]) == ("JWT", "caller-jwt")
    assert "user" not in call and "password" not in call
    assert connection.cursor_value.executed == [result["sql"]]


def test_token_mode_refuses_a_caller_without_a_token_before_connecting(warehouse):
    stores, calls = warehouse("token", Connection())
    assert run(stores, Caller("owner"))["error"] == "data_authorization_denied"
    assert calls == []


def test_a_rejected_token_is_reported_without_a_service_retry(warehouse):
    stores, calls = warehouse("token", RefusedConnection())
    result = run(stores, Caller("owner", "caller-jwt"))
    assert result["error"] == "caller_token_rejected" and not result["retryable"]
    assert result["message"] == "The warehouse rejected the caller's token"
    assert len(calls) == 1 and "caller-jwt" not in repr(result)


def test_no_caller_never_reaches_the_warehouse(warehouse):
    stores, calls = warehouse("impersonate", Connection())
    assert run(stores, None)["error"] == "data_authorization_denied"
    assert calls == []
