"""The helios command: the dispatcher, profiles, the caller, and helios-cortex's own
subcommands (burndown 5.5). helios-model's and helios-cloudera's commands are tested
in their packages."""
import io
from importlib.metadata import entry_points

import pytest

from helios_cortex import Caller, plugins
from helios_cortex.cli import CommandContext, CommandError, main
from helios_cortex.settings import Settings, load_settings


def run(*argv, monkeypatch=None):
    out, err = io.StringIO(), io.StringIO()
    status = main(list(argv), out=out, stderr=err)
    return status, out.getvalue(), err.getvalue()


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A directory with a starter helios.toml whose data stays in it."""
    monkeypatch.chdir(tmp_path)
    for name in ("HELIOS_CONFIG", "HELIOS_PROFILE", "HELIOS_CALLER_TOKEN", "HELIOS_REGISTRY_URL", "HELIOS_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    assert run("init")[0] == 0
    config = tmp_path / "helios.toml"
    config.write_text(config.read_text().replace('# data_dir = "helios-data"', 'data_dir = "helios-data"'))
    return tmp_path


def test_the_command_is_installed_with_cortexs_subcommands():
    [script] = [e for e in entry_points(group="console_scripts") if e.name == "helios"]
    assert script.value == "helios_cortex.cli:main"
    assert {"init", "registry", "index", "source", "model"} <= set(plugins.names(plugins.COMMANDS))
    status, out, _ = run("--help")
    assert status == 0 and "init" in out and "registry" in out


def test_usage_errors_and_missing_packages(project):
    assert run()[0] == 2                                           # no command
    assert run("frobnicate")[0] == 2
    if "harvest" not in plugins.names(plugins.COMMANDS):          # a cortex-only install
        status, _, err = run("harvest", "--source", "x")
        assert (status, err.strip()) == (2, "helios: error: 'harvest' is a command from helios-model; install it")


class Fails:
    help = "always fails"

    def configure(self, parser):
        parser.add_argument("--why", default="no reason")

    def run(self, args, context):
        raise CommandError(args.why)


def fake_commands(monkeypatch, commands):
    real_names, real_load = plugins.names, plugins.load
    monkeypatch.setattr(plugins, "names", lambda g: tuple(commands) if g == plugins.COMMANDS else real_names(g))

    def load(group, name):
        if group != plugins.COMMANDS:
            return real_load(group, name)
        if isinstance(commands[name], Exception):
            raise commands[name]
        return commands[name]

    monkeypatch.setattr(plugins, "load", load)


def test_errors_are_one_line_and_a_broken_plugin_leaves_the_rest(monkeypatch):
    fake_commands(monkeypatch, {"fails": Fails, "broken": plugins.PluginLoadError("broken failed to load: boom")})
    status, out, err = run("fails", "--why", "the warehouse is down")
    assert (status, out) == (1, "")
    assert err.splitlines() == ["helios: warning: broken failed to load: boom",
                                "helios: error: the warehouse is down"]


def test_init_writes_a_config_that_loads_and_never_overwrites(project):
    settings = Settings.from_file(project / "helios.toml")
    assert settings.connection("default").engine == "duckdb"
    assert settings.data_dir.name == "helios-data"
    status, _, err = run("init")
    assert status == 1 and "already exists; pass --force" in err
    assert run("init", "--force", "elsewhere/helios.toml")[0] == 0
    assert (project / "elsewhere" / "helios.toml").read_text().startswith("# Helios configuration")


def test_sources_and_models_are_registered(project):
    assert run("registry", "current")[1].endswith("schema empty (this helios-cortex: 0001_registry)\n")
    status, out, _ = run("source", "add", "warehouse", "--connection", "default", "--name", "Warehouse")
    assert (status, out) == (0, "data source warehouse: connection default (duckdb)\n")
    status, _, err = run("source", "add", "lake", "--connection", "lake")
    assert status == 1 and "no connection 'lake' in the settings (configured: default)" in err
    assert run("source", "list")[1] == "warehouse\tWarehouse\tduckdb\tdefault\n"

    assert run("model", "add", "sales", "--name", "Sales", "--source", "warehouse",
               "--table", "shop.orders", "--as", "alice")[0] == 0
    status, _, err = run("model", "add", "hr", "--name", "HR", "--source", "people")
    assert status == 1 and "no data source people" in err
    assert run("model", "list")[1] == "sales\tSales\tdraft\n"

    from helios_cortex.registry import SqlRegistry

    stored = SqlRegistry.from_settings(Settings.from_file(project / "helios.toml")).registered_model("sales")
    assert (stored.created_by, stored.model.data_sources[0].selected_assets) == ("alice", ("shop.orders",))


def test_the_index_ddl_is_printed(project):
    from helios_cortex.index.__main__ import impala_ddl

    assert run("index", "ddl")[1] == impala_ddl()


def test_the_caller_is_opt_in_and_its_token_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("HELIOS_CALLER_TOKEN", raising=False)
    context = CommandContext()
    assert context.caller(None) is None                            # the service identity
    assert context.caller("alice") == Caller("alice")
    monkeypatch.setenv("HELIOS_CALLER_TOKEN", "jwt")
    caller = context.caller("alice")
    assert (caller.username, caller.token) == ("alice", "jwt")
    assert context.caller(None).token == "jwt"


class Lake(Settings):
    """A profile: a platform's own defaults."""


def test_a_profile_is_named_or_not_used(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HELIOS_PROFILE", raising=False)
    real_load = plugins.load
    monkeypatch.setattr(plugins, "load", lambda g, n: {"lake": Lake, "bad": dict}[n] if g == plugins.PROFILES
                        else real_load(g, n))
    assert type(load_settings()) is Settings
    assert type(load_settings("lake")) is Lake
    monkeypatch.setenv("HELIOS_PROFILE", "lake")
    assert type(load_settings()) is Lake
    with pytest.raises(plugins.PluginError, match="not a Settings subclass"):
        load_settings("bad")
    config = tmp_path / "other.toml"
    config.write_text('data_dir = "x"\n')
    assert load_settings(config=config).data_dir.name == "x"
