"""helios-mcp is a package of its own (burndown 7.1): helios-serve, jsonschema and the MCP SDK, a
`helios mcp` subcommand, and a server an MCP client can initialize, in process and over
real stdio."""
import json
import subprocess
import sys
import tomllib
from importlib.metadata import requires
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.stdio import StdioServerParameters

import helios_mcp
from helios_mcp import SERVER_NAME, build_server

PACKAGE = Path(__file__).resolve().parents[1]


def test_the_package_reports_its_version_and_depends_on_serve_and_the_sdk():
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]
    assert helios_mcp.__version__ == project["version"]
    installed = {r.split(";")[0].split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
                 for r in requires("helios-mcp") or []}
    assert installed == {"helios-serve", "jsonschema", "mcp", "pyyaml"}


def test_importing_it_loads_no_sdk_no_web_server_and_no_other_helios_package():
    probe = ("import json, sys, helios_mcp; "
             "print(json.dumps(sorted({m.partition('.')[0] for m in sys.modules})))")
    loaded = set(json.loads(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                                           check=True).stdout))
    assert not loaded & {"mcp", "apps", "uvicorn", "fastapi", "helios_model", "helios_cloudera", "helios_semantic"}


def test_helios_mcp_is_a_subcommand():
    listed = subprocess.run([sys.executable, "-m", "helios_cortex.cli", "--help"], capture_output=True, text=True)
    assert listed.returncode == 0 and "mcp" in listed.stdout
    described = subprocess.run([sys.executable, "-m", "helios_cortex.cli", "mcp", "--help"], capture_output=True,
                               text=True)
    assert described.returncode == 0
    assert "single-user" in described.stdout and "Daedalus" in described.stdout


async def _initialize(server):
    async with Client(server) as client:
        return client.server_info, client.instructions


def test_an_mcp_client_initializes_the_server_in_process(stores):
    info, instructions = anyio.run(_initialize, build_server(stores))
    assert (info.name, info.version, info.title) == (SERVER_NAME, helios_mcp.__version__, "Helios")
    assert "search_semantics" in instructions and "single-user" in instructions


def test_an_agent_launches_helios_mcp_over_stdio(tmp_path):
    """What Claude Code or goose does: run the command and speak MCP on its stdin/stdout."""
    (tmp_path / "helios.toml").write_text(f'data_dir = "{tmp_path / "data"}"\n')
    launch = StdioServerParameters(command=sys.executable, args=["-m", "helios_cortex.cli", "-q", "mcp"],
                                   cwd=str(tmp_path))
    info, _ = anyio.run(_initialize, launch)
    assert (info.name, info.version) == ("helios", helios_mcp.__version__)
