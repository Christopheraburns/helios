"""The Trino engine plugin: identity modes against a stubbed client (burndown 3.8).

test_trino_live.py runs the same engine against a real Trino server."""
import pytest
from pydantic import ValidationError

from helios_core import Caller, IdentityError, IdentityMode
from helios_core.engines import QueryResult, engine_for
from helios_core.settings import Settings

trino_engine = pytest.importorskip("helios_core.engines.trino_engine", reason="needs helios-core[trino]")
trino = pytest.importorskip("trino")
TrinoEngine, TrinoIdentityError = trino_engine.TrinoEngine, trino_engine.TrinoIdentityError


class Cursor:
    def __init__(self, server):
        self.server, self.description, self._rows = server, None, []

    def execute(self, sql):
        self.server["executed"].append(sql)
        if sql == "SELECT current_user":
            self.description, self._rows = [("_col0",)], [(self.server["current_user"],)]
        else:
            answer = self.server["answers"].get(sql.split(" ")[0] + " " + sql.split(" ")[1], [("value",), [(7,)]])
            if isinstance(answer, Exception):
                raise answer
            self.description, self._rows = answer

    def fetchone(self):
        return self._rows[0]

    def fetchmany(self, limit):
        return self._rows[:limit]

    def fetchall(self):
        return self._rows


class Connection:
    def __init__(self, server):
        self.server = server

    def cursor(self):
        return Cursor(self.server)

    def close(self):
        self.server["closed"] += 1


@pytest.fixture
def server(monkeypatch):
    """Record each trino.dbapi.connect() and answer as a coordinator would."""
    state = {"connects": [], "executed": [], "closed": 0, "current_user": None, "answers": {}}

    def connect(**kwargs):
        state["connects"].append(kwargs)
        state["current_user"] = state["current_user"] or kwargs["user"]
        return Connection(state)

    monkeypatch.setattr(trino.dbapi, "connect", connect)
    return state


def engine(identity="service", **options):
    entry = {"engine": "trino", "host": "trino.example.com", "catalog": "hive", "user": "helios",
             "password": "pw", "identity": identity, **options}
    return engine_for(Settings(connections={"lake": {k: v for k, v in entry.items() if v is not None}}).connection("lake"))


def test_trino_is_an_engine_plugin_for_every_identity_mode():
    assert isinstance(engine(), TrinoEngine)
    assert TrinoEngine.identity_modes == set(IdentityMode)
    assert engine().dialect.name == "trino"
    assert engine().dialect.approx_distinct


def test_service_mode_runs_as_the_service_user_with_basic_auth(server):
    assert engine().query("SELECT 7", Caller("alice")).rows == [(7,)]
    [connect] = server["connects"]
    assert (connect["host"], connect["port"], connect["catalog"], connect["user"], connect["http_scheme"]) == (
        "trino.example.com", 443, "hive", "helios", "https")
    assert isinstance(connect["auth"], trino.auth.BasicAuthentication)
    assert server["executed"] == ["SELECT 7"] and server["closed"] == 1


def test_impersonate_mode_runs_the_session_as_the_caller_and_checks(server):
    assert engine("impersonate").query("SELECT 7", Caller("alice")).rows == [(7,)]
    [connect] = server["connects"]
    assert connect["user"] == "alice"
    assert isinstance(connect["auth"], trino.auth.BasicAuthentication)   # still authenticated as the service user
    assert server["executed"] == ["SELECT current_user", "SELECT 7"]


def test_impersonate_mode_stops_when_trino_runs_as_someone_else(server):
    server["current_user"] = "helios"
    with pytest.raises(TrinoIdentityError):
        engine("impersonate").query("SELECT secret", Caller("alice"))
    assert server["executed"] == ["SELECT current_user"]


def test_helios_own_work_in_impersonate_mode_runs_as_the_service_user(server):
    engine("impersonate").query("SELECT 7")
    assert server["connects"][0]["user"] == "helios"
    assert server["executed"] == ["SELECT 7"]


def test_token_mode_authenticates_with_the_callers_jwt(server):
    lake = engine("token", user=None, password=None)
    assert lake.query("SELECT 7", Caller("alice", token="caller-jwt")).rows == [(7,)]
    [connect] = server["connects"]
    assert isinstance(connect["auth"], trino.auth.JWTAuthentication)
    assert connect["auth"].token == "caller-jwt"
    assert connect["user"] == "alice"
    with pytest.raises(IdentityError, match="token"):
        lake.query("SELECT 7", Caller("alice"))
    with pytest.raises(IdentityError, match="no service user"):
        lake.query("SHOW SCHEMAS")


def test_options_are_validated_for_the_identity_mode():
    with pytest.raises(ValidationError, match="needs user"):
        engine("impersonate", user=None, password=None)
    with pytest.raises(ValidationError, match="needs https"):
        engine(http_scheme="http")
    with pytest.raises(ValidationError, match="catalog"):
        engine(catalog=None)
    open_cluster = engine(password=None, http_scheme="http", schema="sales")
    assert (open_cluster.options.port, open_cluster.options.schema_) == (None, "sales")


def test_secrets_stay_out_of_reprs():
    lake = engine(password="hunter2")
    assert "hunter2" not in repr(lake.options)
    assert "hunter2" not in repr(Settings(connections={"lake": {"engine": "trino", "host": "h", "catalog": "c", "user": "u", "password": "hunter2"}}))


def test_row_count_reads_the_statistics_row_and_falls_back_to_counting(server):
    server["answers"]["SHOW STATS"] = ([("column_name",), ("row_count",)], [("region", None), (None, 1234.0)])
    assert engine().row_count("sales", "orders") == 1234
    assert server["executed"][-1] == 'SHOW STATS FOR "sales"."orders"'
    server["answers"]["SHOW STATS"] = ([("column_name",), ("row_count",)], [(None, None)])
    server["answers"]["SELECT COUNT(*)"] = ([("_col0",)], [(56,)])
    assert engine().row_count("sales", "orders") == 56


def test_recent_queries_come_from_the_coordinator(server):
    server["answers"]["SELECT query"] = ([("query",)], [("SELECT SUM(x) FROM t",), (None,)])
    assert engine().recent_queries(10) == ("system.runtime.queries", ["SELECT SUM(x) FROM t"])
    assert server["executed"][-1] == (
        "SELECT query FROM system.runtime.queries WHERE state = 'FINISHED' ORDER BY created DESC LIMIT 10")


def test_the_engines_query_result_shape(server):
    assert isinstance(engine().query("SELECT 7"), QueryResult)
