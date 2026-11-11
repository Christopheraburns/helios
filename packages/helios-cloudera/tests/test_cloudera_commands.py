"""helios-cloudera's part of the helios command (burndown 5.5): the workbench settings
profile and `helios impala`."""
import io

import impala.dbapi as impala_dbapi
import pytest

from helios_cortex import plugins
from helios_cortex.cli import main
from helios_cortex.settings import load_settings

from helios_cloudera.workbench import WorkbenchSettings

WORKBENCH = {"IMPALA_HOST": "vw.example.com", "WORKLOAD_USER": "svc-helios", "WORKLOAD_PASSWORD": "pw"}


def helios(*argv):
    out, err = io.StringIO(), io.StringIO()
    return main(list(argv), out=out, stderr=err), out.getvalue(), err.getvalue()


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ("HELIOS_PROFILE", "HELIOS_CONFIG", "IMPALA_HOST", "WORKLOAD_USER", "WORKLOAD_PASSWORD",
                 "HELIOS_CONNECTIONS__DEFAULT__ENGINE"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


class Cursor:
    def __init__(self, user):
        self.user, self.description = user, None

    def execute(self, sql):
        self.description = [("value",)]
        self.sql = sql

    def fetchone(self):
        return (self.user,)

    def fetchmany(self, _limit):
        return [(1,)]


@pytest.fixture
def impala(environment):
    connects = []

    def connect(**kwargs):
        connects.append(kwargs)
        user = kwargs["http_path"].partition("doAs=")[2] or "svc-helios"
        return type("Connection", (), {"cursor": lambda self: Cursor(user), "close": lambda self: None})()

    environment.setattr(impala_dbapi, "connect", connect)
    for name, value in WORKBENCH.items():
        environment.setenv(name, value)
    return connects


def test_the_workbench_profile_and_impala_command_are_registered(environment):
    assert "workbench" in plugins.names(plugins.PROFILES)
    assert "impala" in plugins.names(plugins.COMMANDS)
    for name, value in WORKBENCH.items():
        environment.setenv(name, value)
    settings = load_settings("workbench")
    assert isinstance(settings, WorkbenchSettings)
    assert settings.connection("default").engine == "impala"
    assert load_settings().connection("default") is None       # the profile is never assumed


def test_diagnose_checks_the_workload_user_then_the_delegated_one(impala):
    status, out, _ = helios("--profile", "workbench", "impala", "diagnose", "--delegated-user", "alice")
    assert status == 0
    assert out.splitlines() == ["service-account authentication: ok", "proxy delegation: ok for alice"]
    assert [c["http_path"] for c in impala] == ["cliservice", "cliservice?doAs=alice"]
    assert all("pw" not in line for line in out.splitlines())


def test_impala_commands_need_an_impala_connection(environment):
    status, _, err = helios("impala", "init-index")
    assert status == 1 and "Impala is not configured" in err and "--profile workbench" in err
    status, _, err = helios("impala", "diagnose")
    assert (status, err) == (1, "helios: error: configuration: failed (set IMPALA_HOST, WORKLOAD_USER, "
                                "and WORKLOAD_PASSWORD)\n")
