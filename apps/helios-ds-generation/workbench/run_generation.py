"""
Workbench Job `helios-ds-generate`: run one Helios-DS generation job.

The API starts runs of this Job with HELIOS_DS_JOB_ID in the run's environment;
the worker reads the job from the helios_ds lakehouse, runs it and records
RUNNING / SUCCEEDED / FAILED events there. Re-running a job is safe.

Created and updated by workbench/setup_jobs.py. Settings (Job environment or
project environment variables):
  HELIOS_DS_JOB_ID        set per run by the API
  HELIOS_DS_LAKEHOUSE     impala
  HELIOS_DS_TPCDS         impala:tpcds
  HELIOS_DS_OBJECT_STORE  s3a://applied-ai-buk-d5eff1ab/helios-db/source
  IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD
"""

import os
import sys

PROJECT_DIR = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(PROJECT_DIR, "helios")
sys.path.insert(0, os.path.join(ROOT, "shared"))  # helios_core (Impala settings)
sys.path.insert(0, os.path.join(ROOT, "apps", "helios-ds-generation", "src"))

from helios_ds.jobs import run_from_env  # noqa: E402

job = run_from_env()
print(f"job {job.job_id}: {job.state.value}; dataset {job.dataset_id}; {job.message}")
if job.state.value == "FAILED":
    raise SystemExit(1)
