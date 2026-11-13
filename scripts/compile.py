"""Compile a semantic request from the command line.

  uv run python scripts/compile.py tpcds '{"metrics":["Store Sales Revenue"],"dimensions":["s_state","d_year"],"filters":[["d_year","=",2001]],"limit":20}'
  uv run python scripts/compile.py tpcds request.json --dialect duckdb --run   # --run executes on the default connection
"""
import argparse
import json
import os
from helios_cortex.ossie import SemanticModel
from helios_cortex.compiler import Compiler, SemanticRequest
from helios_cloudera.impala import IMPALA
from helios_cloudera.workbench import WorkbenchSettings as Settings

ap = argparse.ArgumentParser()
ap.add_argument("model")
ap.add_argument("request")
ap.add_argument("--dialect", default="impala", help="impala, or any SQLGlot dialect (duckdb, postgres, snowflake, ...)")
ap.add_argument("--run", action="store_true")
a = ap.parse_args()
req = SemanticRequest.from_dict(json.load(open(a.request)) if os.path.exists(a.request) else json.loads(a.request))
model = SemanticModel.load_published(a.model, Settings().artifacts_dir)
out = Compiler(model).compile(req, dialect=IMPALA if a.dialect in ("impala", "hive") else a.dialect)
print(f"-- fact: {out.fact}; joins: {', '.join(out.joins) or 'none'}\n{out.sql}")
if a.run:
    from helios_cortex.engines import engine_for

    connection = Settings().connection()
    if connection is None:
        raise SystemExit("--run needs a connection: set IMPALA_HOST, WORKLOAD_USER and WORKLOAD_PASSWORD, "
                         "or a [connections.default] in helios.toml")
    res = engine_for(connection).query(out.sql, limit=200)
    print("\n" + " | ".join(res.columns))
    for r in res.rows:
        print(" | ".join(str(x) for x in r))
