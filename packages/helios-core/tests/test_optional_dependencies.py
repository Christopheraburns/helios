"""The base install is the compiler, semantic model, DuckDB and SQLite (burndown 3.2).

Every optional package is blocked in a fresh interpreter; ``import helios_core``,
every base module and the quickstart path (a DuckDB connection found through the
plugin registry, a published model, a compiled metric query) must still work, and
each module that needs an extra must say which one to install.
"""
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[1]

BASE = {"alembic", "duckdb", "jsonschema", "platformdirs", "pydantic", "pydantic-settings", "pyyaml", "sqlalchemy", "sqlglot"}

# The module each optional requirement installs, so the subprocess can block it.
OPTIONAL_MODULES = {
    "httpx": "httpx",
    "psycopg": "psycopg",
    "s3fs": "s3fs",
    "adlfs": "adlfs",
    "gcsfs": "gcsfs",
    "fsspec": "fsspec",
    "requests": "requests",
    "trino": "trino",
    "linkml-runtime": "linkml_runtime",
    "lancedb": "lancedb",
    "sentence-transformers": "sentence_transformers",
}

# Also blocked: helios-cloudera and what it installs, which core must never import.
CLOUDERA_MODULES = ["helios_cloudera", "impala", "requests"]

# Modules that are an optional feature: importing them without the extra names it.
FEATURES = {
    "helios_core.engines.trino_engine": "trino",
    "helios_core.llm": "llm",
    "helios_core.llm.client": "llm",
    "helios_core.ontology.parser": "ontology",
}


def _name(requirement: str) -> str:
    return re.match(r"[A-Za-z0-9._-]+", requirement).group(0).lower()


def _project() -> dict:
    return tomllib.loads((PACKAGE / "pyproject.toml").read_text())["project"]


def test_the_base_dependencies_are_only_the_base():
    assert {_name(r) for r in _project()["dependencies"]} == BASE


def test_every_extra_is_known_to_the_isolation_check():
    extras = _project()["optional-dependencies"]
    assert {"azure", "gcs", "llm", "ontology", "ozone", "postgres", "s3", "trino"} <= set(extras)
    assert "cloudera" not in extras  # helios-cloudera is its own package (3.3)
    assert {_name(r) for reqs in extras.values() for r in reqs} == set(OPTIONAL_MODULES)


ISOLATED = r'''
import importlib, importlib.abc, json, pkgutil, sys, tempfile, os

BLOCKED = set(sys.argv[1].split(","))
FEATURES = json.loads(sys.argv[2])

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.partition(".")[0] in BLOCKED:
            raise ModuleNotFoundError(f"No module named {name!r} (blocked)", name=name)

sys.meta_path.insert(0, Block())
report = {"broken": [], "features": {}}

import helios_core
# walk_packages cannot descend into a package that fails to import, so the
# feature modules are named as well.
names = {m.name for m in pkgutil.walk_packages(helios_core.__path__, "helios_core.")} | set(FEATURES)
for name in sorted(names):
    if "._pending" in name or name.endswith("__main__"):
        continue
    try:
        importlib.import_module(name)
    except ImportError as exc:
        if name in FEATURES:
            report["features"][name] = str(exc)
        else:
            report["broken"].append(f"{name}: {exc}")
    else:
        if name in FEATURES:
            report["features"][name] = None

from helios_core import plugins
from helios_core.compiler import Compiler, SemanticRequest
from helios_core.ossie import SemanticModel
from helios_core.settings import Settings

settings = Settings(connections={"default": {"engine": "duckdb"}})
plugins.require_configured(settings)
engine = plugins.load(plugins.ENGINES, settings.connection().engine)()
engine.query("CREATE SCHEMA tpcds")
engine.query("CREATE TABLE tpcds.date_dim AS SELECT 1 AS d_date_sk, 2001 AS d_year")
engine.query("CREATE TABLE tpcds.store_sales AS SELECT 1 AS ss_sold_date_sk, 9.5 AS ss_net_paid")
model = SemanticModel.load(os.path.join(sys.argv[3], "models", "published", "tpcds.ossie.yaml"))
request = SemanticRequest(metrics=["Store Sales Revenue"], dimensions=["d_year"])
report["quickstart"] = [list(map(float, row)) for row in engine.query(Compiler(model).compile(request, dialect=engine.sqlglot_dialect).sql).rows]

report["loaded"] = sorted(BLOCKED & {name.partition(".")[0] for name in sys.modules})
print(json.dumps(report))
'''


def test_helios_core_and_the_quickstart_work_without_any_extra(tmp_path):
    result = subprocess.run(
        [sys.executable, "-c", ISOLATED, ",".join([*OPTIONAL_MODULES.values(), *CLOUDERA_MODULES]), json.dumps(FEATURES), str(ROOT)],
        capture_output=True, text=True, cwd=tmp_path, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.splitlines()[-1])

    assert report["broken"] == []
    assert report["loaded"] == []
    for module, extra in FEATURES.items():
        assert report["features"][module] is not None, f"{module} imported without its extra"
        assert f"pip install 'helios-core[{extra}]'" in report["features"][module]
    assert report["quickstart"] == [[2001.0, 9.5]]
