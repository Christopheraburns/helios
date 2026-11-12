"""helios-serve is a package of its own that depends on helios-cortex alone and has no
MCP dependency (burndown 6.1)."""
import json
import subprocess
import sys
import tomllib
from importlib.metadata import requires
from pathlib import Path

import helios_serve

PACKAGE = Path(__file__).resolve().parents[1]


def test_the_package_reports_its_version():
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]
    assert helios_serve.__version__ == project["version"]
    assert project["dependencies"] == ["helios-cortex"]
    assert "optional-dependencies" not in project


def test_it_installs_without_mcp_or_a_web_framework():
    """Its installed requirements, extras included: Daedalus and helios-mcp bring MCP."""
    installed = {r.split(";")[0].split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower()
                 for r in requires("helios-serve") or []}
    assert installed == {"helios-cortex"}


def test_importing_it_loads_no_app_code_no_mcp_and_no_other_helios_package():
    probe = ("import json, sys, helios_serve; "
             "print(json.dumps(sorted({m.partition('.')[0] for m in sys.modules})))")
    loaded = set(json.loads(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                                           check=True).stdout))
    assert not loaded & {"apps", "mcp", "fastapi", "starlette", "helios_model", "helios_cloudera",
                         "helios_mcp", "helios_crawl"}
