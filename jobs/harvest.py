"""
Job: harvest
Runs `helios --profile workbench harvest`: snapshots the catalog, glossary and query
history of a registered data source into a new run.

Environment:
  HELIOS_SOURCE_ID   the registered data source to harvest (`helios source add`)
  HELIOS_DATABASES   comma-separated databases to harvest (default: tpcds; 5.7 removes the default)
  HELIOS_GLOSSARIES  comma-separated Atlas glossaries to include (default: all)
  HELIOS_QUERY_DIR   optional folder of .sql files to treat as query history
  HELIOS_RUN_ID      optional; default is a new timestamp run id
  HELIOS_MODEL_ID    stable Helios Model ID (legacy fallback: first database)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import env_list, helios  # noqa: E402

from helios_model.discovery import new_run_id  # noqa: E402

source = os.environ.get("HELIOS_SOURCE_ID")
if not source:
    raise SystemExit("set HELIOS_SOURCE_ID to a registered data source (helios source add ID --connection default)")
databases = env_list("HELIOS_DATABASES") or ["tpcds"]
run_id = os.environ.get("HELIOS_RUN_ID") or new_run_id()
model_id = os.environ.get("HELIOS_MODEL_ID") or databases[0]

arguments = ["--source", source, "--model", model_id, "--run-id", run_id]
arguments += [a for d in databases for a in ("--database", d)]
arguments += [a for g in env_list("HELIOS_GLOSSARIES") for a in ("--glossary", g)]
if os.environ.get("HELIOS_QUERY_DIR"):
    arguments += ["--query-dir", os.environ["HELIOS_QUERY_DIR"]]
helios("harvest", arguments, run_id, model_id)
