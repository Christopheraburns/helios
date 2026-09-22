"""Compile a semantic request from the command line.

  python scripts/compile.py tpcds '{"metrics":["Store Sales Revenue"],"dimensions":["s_state","d_year"],"filters":[["d_year","=",2001]],"limit":20}'
  python scripts/compile.py tpcds request.json --dialect duckdb --run   # --run executes against the configured Impala engine
"""
import argparse, json, os, sys
sys.path.insert(0, os.environ.get("HELIOS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from helios_core.ossie import SemanticModel
from helios_core.compiler import Compiler, SemanticRequest

ap = argparse.ArgumentParser(); ap.add_argument("model"); ap.add_argument("request"); ap.add_argument("--dialect", default="hive"); ap.add_argument("--run", action="store_true")
a = ap.parse_args()
req = SemanticRequest.from_dict(json.load(open(a.request)) if os.path.exists(a.request) else json.loads(a.request))
model = SemanticModel.load_published(a.model)
out = Compiler(model).compile(req, dialect=a.dialect)
print(f"-- fact: {out.fact}; joins: {', '.join(out.joins) or 'none'}\n{out.sql}")
if a.run:
    from jobs._common import engine
    res = engine().query(out.sql, 200)
    print("\n" + " | ".join(res.columns))
    for r in res.rows: print(" | ".join(str(x) for x in r))
