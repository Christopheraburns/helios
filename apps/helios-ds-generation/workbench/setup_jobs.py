"""
Create or update the Helios-DS Workbench Jobs in this project (task J-05).

Run from a session (or as a one-off Job) on any runtime:
  python helios/apps/helios-ds-generation/workbench/setup_jobs.py

Settings (optional):
  HELIOS_DS_RUNTIME          runtime image (default: the registered helios-ds-runtime)
  HELIOS_DS_WORKER_CPU       vCPUs per generation run (default 2)
  HELIOS_DS_WORKER_MEMORY    GiB per generation run (default 4)
  HELIOS_DS_OBJECT_STORE     default s3a://applied-ai-buk-d5eff1ab/helios-db/source
"""

import json
import os
import sys

PROJECT_DIR = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(PROJECT_DIR, "helios")
sys.path.insert(0, os.path.join(ROOT, "shared"))
sys.path.insert(0, os.path.join(ROOT, "apps", "helios-ds-generation", "src"))

import cmlapi  # noqa: E402

from helios_ds.jobs import GENERATE_JOB_NAME  # noqa: E402
from helios_ds.jobs.dispatch import workbench_client  # noqa: E402

RUNTIME = os.environ.get("HELIOS_DS_RUNTIME", "docker.io/christopheraburns/helios-ds-runtime:0.1.0")
SCRIPT = "helios/apps/helios-ds-generation/workbench/run_generation.py"  # relative to project root

definition = {
    "name": GENERATE_JOB_NAME,
    "script": SCRIPT,
    "cpu": float(os.environ.get("HELIOS_DS_WORKER_CPU", "2")),
    "memory": float(os.environ.get("HELIOS_DS_WORKER_MEMORY", "4")),
    "runtime_identifier": RUNTIME,
    "timeout": 3600,
    "kill_on_timeout": True,
    "environment": {
        "HELIOS_DS_LAKEHOUSE": "impala",
        "HELIOS_DS_TPCDS": "impala:tpcds",
        "HELIOS_DS_OBJECT_STORE": os.environ.get(
            "HELIOS_DS_OBJECT_STORE", "s3a://applied-ai-buk-d5eff1ab/helios-db/source"
        ),
    },
}

client = workbench_client()
project_id = os.environ["CDSW_PROJECT_ID"]
existing = [
    j for j in client.list_jobs(project_id, page_size=100).jobs if j.name == GENERATE_JOB_NAME
]
if existing:
    # The update model takes the environment as a JSON string; create takes a dict.
    update = {**definition, "environment": json.dumps(definition["environment"])}
    job = client.update_job(cmlapi.Job(**update), project_id, existing[0].id)
    print(f"updated Workbench Job {job.name} ({job.id}) on {job.runtime_identifier}")
else:
    job = client.create_job(
        cmlapi.CreateJobRequest(project_id=project_id, **definition), project_id
    )
    print(f"created Workbench Job {job.name} ({job.id}) on {job.runtime_identifier}")
