"""
Job: harvest
Snapshots the catalog, glossary and query history into runs/<run_id>/harvest.json.

Environment:
  HELIOS_DATABASES   comma-separated databases to harvest (default: tpcds)
  HELIOS_GLOSSARIES  comma-separated Atlas glossaries to include (default: all)
  HELIOS_QUERY_DIR   optional folder of .sql files to treat as query history
  HELIOS_RUN_ID      optional; default is a new timestamp run id
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import atlas, engine, new_run_id, run_path, write_json  # noqa: E402

from helios_core.catalog import Harvester  # noqa: E402

databases = [d.strip() for d in os.environ.get("HELIOS_DATABASES", "tpcds").split(",") if d.strip()]
glossaries = [g.strip() for g in os.environ.get("HELIOS_GLOSSARIES", "").split(",") if g.strip()] or None
run_id = os.environ.get("HELIOS_RUN_ID") or new_run_id()

print(f"harvest run {run_id}: databases={databases} glossaries={glossaries or 'all'}")
snapshot = Harvester(engine(), atlas()).run(databases, glossaries, os.environ.get("HELIOS_QUERY_DIR"))
print(f"tables={len(snapshot['tables'])} glossary_terms={len(snapshot['glossary_terms'])} "
      f"queries={len(snapshot['queries']['statements'])} ({'; '.join(snapshot['queries']['sources'])})")
write_json(run_path(run_id, "harvest.json"), snapshot)
