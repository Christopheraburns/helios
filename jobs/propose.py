"""
Job: propose
Runs `helios --profile workbench propose`: the configured LLM drafts a model from a
run's harvest and profile (Mistral, Anthropic, or Cloudera AI Inference through
INFERENCE_BASE_URL / INFERENCE_MODEL).

Environment:
  HELIOS_RUN_ID   run to propose from (default: latest)
  HELIOS_TABLES   optional comma-separated database.table subset
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import env_list, helios, latest_run_id, run_model_id  # noqa: E402

run_id = os.environ.get("HELIOS_RUN_ID") or latest_run_id()
if not run_id:
    raise SystemExit("no run found; run jobs/harvest.py and jobs/profile.py first")
helios("propose", ["--run", run_id, *[a for t in env_list("HELIOS_TABLES") for a in ("--table", t)]],
       run_id, run_model_id(run_id))
