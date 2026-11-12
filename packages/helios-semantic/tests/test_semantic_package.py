"""helios-semantic is the package a developer installs (burndown 6.6): the libraries, the
`helios` command through helios-cortex, and extras that name real extras."""
import json
import re
import subprocess
import sys
import tomllib
from importlib.metadata import distribution
from pathlib import Path

import helios_semantic

PACKAGES = Path(__file__).resolve().parents[2]
PROJECT = tomllib.loads((PACKAGES / "helios-semantic" / "pyproject.toml").read_text())["project"]


def test_the_package_reports_its_version_and_installs_the_libraries():
    assert helios_semantic.__version__ == PROJECT["version"]
    assert PROJECT["dependencies"] == ["helios-cortex", "helios-model", "helios-serve"]


def test_each_extra_names_an_extra_its_package_declares():
    extras = PROJECT["optional-dependencies"]
    for name, requirements in extras.items():
        for requirement in requirements:
            package, _, wanted = re.fullmatch(r"([a-z-]+)(\[([a-z0-9,]+)\])?", requirement).groups()
            if package == "helios-semantic":
                assert name == "all" and set(wanted.split(",")) == set(extras) - {"all"}
                continue
            assert (PACKAGES / package).is_dir(), requirement
            if wanted:
                declared = tomllib.loads((PACKAGES / package / "pyproject.toml").read_text())["project"]
                assert set(wanted.split(",")) <= set(declared["optional-dependencies"]), requirement
    # The extras from 3.2, with the object stores Phase 4 added; [unstructured] waits for helios-crawl.
    assert set(extras) == {"cloudera", "llm", "postgres", "trino", "s3", "azure", "gcs", "ozone", "ontology", "all"}


def test_the_helios_command_comes_with_it():
    scripts = {e.name: e.value for e in distribution("helios-cortex").entry_points if e.group == "console_scripts"}
    assert scripts["helios"] == "helios_cortex.cli:main"
    listed = subprocess.run([sys.executable, "-m", "helios_cortex.cli", "--help"], capture_output=True, text=True)
    assert listed.returncode == 0
    assert all(command in listed.stdout for command in ("harvest", "publish", "eval"))


def test_importing_it_loads_nothing_heavy():
    probe = ("import json, sys, helios_semantic; "
             "print(json.dumps(sorted({m.partition('.')[0] for m in sys.modules})))")
    loaded = set(json.loads(subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                                           check=True).stdout))
    assert not loaded & {"apps", "mcp", "helios_model", "helios_serve", "helios_cloudera", "duckdb", "sqlglot"}
