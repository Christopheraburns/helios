"""
Job: publish
Runs `helios --profile workbench publish`: applies the run's review decisions, builds
and validates the Ossie model, publishes it to the artifact store and records the
revision in the registry. The model must be registered (`helios model add`).

Environment:
  HELIOS_RUN_ID          run to publish from (default: latest)
  HELIOS_ACCEPT_PENDING  "1" to accept un-reviewed elements before publishing (default: they are left out)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios"), "jobs"))
from _common import helios, latest_run_id, run_model_id  # noqa: E402

run_id = os.environ.get("HELIOS_RUN_ID") or latest_run_id()
if not run_id:
    raise SystemExit("no run found")
helios("publish", ["--run", run_id, *(["--accept-pending"] if os.environ.get("HELIOS_ACCEPT_PENDING") == "1" else [])],
       run_id, run_model_id(run_id))
