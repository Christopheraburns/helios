"""
Job: profile
Runs `helios --profile workbench profile`: column statistics, key candidates and
verified relationships for a run's harvest.

Environment:
  HELIOS_RUN_ID            run to profile (default: latest)
  HELIOS_OVERLAP_THRESHOLD minimum FK->PK match ratio to accept a relationship (default 0.95)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import helios, latest_run_id, run_model_id  # noqa: E402

run_id = os.environ.get("HELIOS_RUN_ID") or latest_run_id()
if not run_id:
    raise SystemExit("no run found; run jobs/harvest.py first")
helios("profile", ["--run", run_id, "--overlap-threshold", os.environ.get("HELIOS_OVERLAP_THRESHOLD", "0.95")],
       run_id, run_model_id(run_id))
