"""
Job: harvest
Snapshots the catalog, glossary and query history into runs/<run_id>/harvest.json.

Environment:
  HELIOS_DATABASES   comma-separated databases to harvest (default: tpcds)
  HELIOS_GLOSSARIES  comma-separated Atlas glossaries to include (default: all)
  HELIOS_QUERY_DIR   optional folder of .sql files to treat as query history
  HELIOS_RUN_ID      optional; default is a new timestamp run id
  HELIOS_MODEL_ID    stable Helios Model ID (legacy fallback: first database)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import atlas, engine, new_run_id, run_path, write_json  # noqa: E402

from helios_core.catalog import Harvester  # noqa: E402
from helios_core.artifacts import model_id_for_run  # noqa: E402

databases = [d.strip() for d in os.environ.get("HELIOS_DATABASES", "tpcds").split(",") if d.strip()]
glossaries = [g.strip() for g in os.environ.get("HELIOS_GLOSSARIES", "").split(",") if g.strip()] or None
run_id = os.environ.get("HELIOS_RUN_ID") or new_run_id()
model_id = model_id_for_run(
    explicit=os.environ.get("HELIOS_MODEL_ID"),
    legacy_default=databases[0] if databases else "helios",
)

print(f"harvest run {run_id}: model={model_id} databases={databases} glossaries={glossaries or 'all'}")
snapshot = Harvester(engine(), atlas()).run(databases, glossaries, os.environ.get("HELIOS_QUERY_DIR"))
snapshot["model_id"] = model_id
print(f"tables={len(snapshot['tables'])} glossary_terms={len(snapshot['glossary_terms'])} "
      f"queries={len(snapshot['queries']['statements'])} kept, {snapshot['queries']['stats']} "
      f"({'; '.join(snapshot['queries']['sources'])})")
write_json(run_path(run_id, "harvest.json"), snapshot)
