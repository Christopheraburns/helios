"""helios-model is a package of its own that depends on helios-cortex alone (burndown 5.1)."""
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import helios_model

PACKAGE = Path(__file__).resolve().parents[1]


def test_the_package_reports_its_version():
    project = tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]
    assert helios_model.__version__ == project["version"]
    assert project["dependencies"] == ["helios-cortex"]


def test_importing_it_loads_no_app_code_and_no_other_helios_package():
    probe = ("import json, sys, helios_model; "
             "print(json.dumps(sorted({m.partition('.')[0] for m in sys.modules})))")
    loaded = set(json.loads(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                                           check=True).stdout))
    assert not loaded & {"apps", "helios_cloudera", "helios_serve", "helios_mcp", "helios_crawl"}
