"""
Job: profile
Reads runs/<run_id>/harvest.json, computes column statistics, key candidates and
verified relationships, and writes runs/<run_id>/profile.json.

Environment:
  HELIOS_RUN_ID            run to profile (default: latest)
  HELIOS_OVERLAP_THRESHOLD minimum FK->PK match ratio to accept a relationship (default 0.95)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import engine, latest_run_id, read_json, run_path, write_json  # noqa: E402

from helios_core.profiler import Profiler  # noqa: E402

run_id = os.environ.get("HELIOS_RUN_ID") or latest_run_id()
if not run_id:
    raise SystemExit("no harvest run found; run jobs/harvest.py first")

harvest = read_json(run_path(run_id, "harvest.json"))
print(f"profile run {run_id}: {len(harvest['tables'])} tables")
result = Profiler(engine(), float(os.environ.get("HELIOS_OVERLAP_THRESHOLD", "0.95"))).run(harvest)
print(f"relationships accepted={len(result['relationships'])} suggested={len(result['suggested_relationships'])} "
      f"rejected={len(result['rejected_candidates'])}")
for r in result["relationships"]:
    print(f"  {r['from']}.{r['from_column']} -> {r['to']}.{r['to_column']}  match={r['match_ratio']}  name={r['name_score']}")
write_json(run_path(run_id, "profile.json"), result)
