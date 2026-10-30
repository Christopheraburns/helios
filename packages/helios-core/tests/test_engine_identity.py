"""Engine identity modes: whose identity a query runs as (burndown 2.2)."""
import impala.dbapi as impala_dbapi
import pytest
from pydantic import ValidationError

from helios_core import Caller, IdentityError, IdentityMode
from helios_core.engines import ImpalaAuthenticationError, ImpalaEngine, ImpalaProxyDelegationError
from helios_core.engines.duckdb_engine import DuckDBEngine
from helios_core.engines.impala import ImpalaConfig, impala_config_for
from helios_core.settings import Settings


class Cursor:
    def __init__(self, effective_user, fail_with=None):
        self.effective_user = effective_user
        self.fail_with = fail_with
        self.executed = []
        self.description = None

    def execute(self, sql):
        if self.fail_with:
            raise self.fail_with
        self.executed.append(sql)
        self.description = [("value",)]

    def fetchone(self):
        return (self.effective_user,)

    def fetchmany(self, _limit):
        return [(7,)]


class Connection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.closed = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.closed = True


@pytest.fixture
def warehouse(monkeypatch):
    """Record each impyla connect() and answer EFFECTIVE_USER() as ``effective_user``."""
    state = {"connects": [], "effective_user": "alice", "fail_with": None}

    def connect(**kwargs):
        state["connects"].append(kwargs)
        state["cursor"] = Cursor(state["effective_user"], state["fail_with"])
        return Connection(state["cursor"])

    monkeypatch.setattr(impala_dbapi, "connect", connect)
    return state


def impala(identity, user="svc-helios", password="pw"):
    return ImpalaEngine(ImpalaConfig("vw.example.com", 443, user, password, identity=identity))


def test_duckdb_supports_service_only():
    assert DuckDBEngine.identity_modes == {IdentityMode.SERVICE}
    with pytest.raises(ValueError, match="does not support identity mode 'impersonate'"):
        DuckDBEngine(identity_mode="impersonate")
    engine = DuckDBEngine()
    assert engine.query("SELECT 7", Caller("alice", token="t")).rows == [(7,)]
    assert engine.query("SELECT 7", limit=1).rows == [(7,)]


def test_impala_supports_every_mode():
    assert ImpalaEngine.identity_modes == set(IdentityMode)


def test_service_mode_runs_every_query_as_the_workload_user(warehouse):
    assert impala(IdentityMode.SERVICE).query("SELECT 7", Caller("alice")).rows == [(7,)]
    [connect] = warehouse["connects"]
    assert (connect["auth_mechanism"], connect["user"], connect["http_path"]) == ("LDAP", "svc-helios", "cliservice")
    assert warehouse["cursor"].executed == ["SELECT 7"]


def test_impersonate_mode_adds_doas_and_checks_the_effective_user(warehouse):
    assert impala(IdentityMode.IMPERSONATE).query("SELECT 7", Caller("alice")).rows == [(7,)]
    [connect] = warehouse["connects"]
    assert (connect["auth_mechanism"], connect["user"], connect["http_path"]) == ("LDAP", "svc-helios", "cliservice?doAs=alice")
    assert warehouse["cursor"].executed == ["SELECT EFFECTIVE_USER()", "SELECT 7"]


def test_impersonate_mode_stops_when_impala_runs_as_someone_else(warehouse):
    warehouse["effective_user"] = "svc-helios"
    with pytest.raises(ImpalaProxyDelegationError):
        impala(IdentityMode.IMPERSONATE).query("SELECT secret", Caller("alice"))
    assert warehouse["cursor"].executed == ["SELECT EFFECTIVE_USER()"]


def test_impersonate_mode_needs_a_username(warehouse):
    with pytest.raises(IdentityError, match="username"):
        impala(IdentityMode.IMPERSONATE).query("SELECT 7", Caller(token="t"))
    assert warehouse["connects"] == []


def test_no_caller_is_helios_own_work_as_the_service(warehouse):
    impala(IdentityMode.IMPERSONATE).query("SHOW DATABASES")
    [connect] = warehouse["connects"]
    assert connect["http_path"] == "cliservice"
    assert warehouse["cursor"].executed == ["SHOW DATABASES"]


def test_token_mode_authenticates_the_callers_jwt(warehouse):
    engine = impala(IdentityMode.TOKEN, user=None, password=None)
    assert engine.query("SELECT 7", Caller("alice", token="caller-jwt")).rows == [(7,)]
    [connect] = warehouse["connects"]
    assert connect["auth_mechanism"] == "JWT"
    assert connect["jwt"] == "caller-jwt"
    assert "user" not in connect and "password" not in connect
    assert warehouse["cursor"].executed == ["SELECT 7"]


def test_token_mode_needs_a_token(warehouse):
    with pytest.raises(IdentityError, match="token"):
        impala(IdentityMode.TOKEN).query("SELECT 7", Caller("alice"))
    assert warehouse["connects"] == []


def test_token_mode_without_a_workload_user_has_no_service_identity(warehouse):
    with pytest.raises(IdentityError, match="no workload user"):
        impala(IdentityMode.TOKEN, user=None, password=None).query("SHOW DATABASES")


def test_a_rejected_token_says_so_without_repeating_it(warehouse):
    warehouse["fail_with"] = type("HttpError", (Exception,), {"code": 401})("unauthorized")
    with pytest.raises(ImpalaAuthenticationError, match="the caller's token") as raised:
        impala(IdentityMode.TOKEN).query("SELECT 7", Caller(token="caller-jwt"))
    assert "caller-jwt" not in str(raised.value)


def test_the_connection_setting_picks_the_mode():
    def config(**options):
        return impala_config_for(Settings(connections={"w": {"engine": "impala", "host": "vw", **options}}).connections["w"])

    assert config(user="u", password="p").identity is IdentityMode.SERVICE
    assert config(user="u", password="p", identity="impersonate").identity is IdentityMode.IMPERSONATE
    assert config(identity="token").user is None
    with pytest.raises(ValidationError, match="needs user and password"):
        config(identity="impersonate")
    with pytest.raises(ValidationError):
        Settings(connections={"w": {"engine": "impala", "host": "vw", "identity": "kerberos"}})
    assert "identity" not in Settings(connections={"d": {"engine": "duckdb"}}).connections["d"].options


def test_the_workload_password_is_not_in_the_config_repr():
    assert "pw" not in repr(ImpalaConfig("vw", 443, "svc", "pw"))
