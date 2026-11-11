"""Golden SQL: the sample requests compiled for each engine's dialect (burndown 3.6).

The engine supplies the dialect; nothing defaults to Hive. Each file must also parse
back in its own dialect. Regenerate after an intended change with
HELIOS_UPDATE_GOLDEN=1 uv run pytest packages/helios-core/tests/test_compile_golden.py
and review the diff. Impala's golden files live in helios-cloudera.
"""
import json
import os
from pathlib import Path

import pytest
import sqlglot

from helios_core.compiler import Compiler, SemanticRequest
from helios_core.ossie import SemanticModel

HERE = Path(__file__).parent
ROOT = HERE.parents[2]
GOLDEN = HERE / "golden" / "compile"
REQUESTS = json.loads((HERE / "fixtures" / "sample_requests.json").read_text())
DIALECTS = ["duckdb", "postgres", "snowflake", "bigquery", "trino"]
UPDATE = os.environ.get("HELIOS_UPDATE_GOLDEN") == "1"


@pytest.fixture(scope="module")
def model():
    return SemanticModel.load(str(ROOT / "models" / "published" / "tpcds.ossie.yaml"))


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("case", sorted(REQUESTS))
def test_compiled_sql_matches_the_golden_file(model, dialect, case):
    sql = Compiler(model).compile(SemanticRequest.from_dict(REQUESTS[case]), dialect=dialect).sql + "\n"
    sqlglot.parse_one(sql, read=dialect)
    path = GOLDEN / dialect / f"{case}.sql"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sql)
    assert path.read_text() == sql, f"{path.relative_to(HERE)} is stale; see this module's docstring"


def test_every_golden_file_has_a_request():
    files = {(p.parent.name, p.stem) for p in GOLDEN.glob("*/*.sql")}
    assert files == {(d, c) for d in DIALECTS for c in REQUESTS}
