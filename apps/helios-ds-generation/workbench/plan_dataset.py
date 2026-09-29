"""
Workbench Job: plan a Helios-DS dataset and publish its generation manifest.

Create the Job with:
  Script:   helios/apps/helios-ds-generation/workbench/plan_dataset.py
  Runtime:  helios-ds-runtime (Python 3.12)

Workbench runs this file in a Jupyter kernel (no __file__), so paths come from
the environment. Configure under Project Settings -> Advanced -> Environment Variables:
  HELIOS_DS_TPCDS         impala:tpcds (or duckdb-generate:1 for a smoke test)
  HELIOS_DS_LAKEHOUSE     impala: helios_ds/helios_ground_truth in the tpcds lakehouse
  IMPALA_HOST, WORKLOAD_USER, WORKLOAD_PASSWORD
                          Impala Virtual Warehouse connection (same as the Helios apps)
  HELIOS_DS_OBJECT_STORE  s3a://applied-ai-buk-d5eff1ab/helios-db/source
  HELIOS_DS_S3_CONNECTION optional; Workbench data connection name (default "S3 Object Store")
  HELIOS_DS_CONFIG        optional DatasetConfig JSON path, relative to the repo root
  HELIOS_DS_INIT_TABLES   set to 1 to create missing tables first
"""

import os
import sys

PROJECT_DIR = os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw")
ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(PROJECT_DIR, "helios")
sys.path.insert(0, os.path.join(ROOT, "shared"))  # helios_core (Impala settings)
sys.path.insert(0, os.path.join(ROOT, "apps", "helios-ds-generation", "src"))

from helios_ds.__main__ import main  # noqa: E402

argv = ["plan"]
if os.environ.get("HELIOS_DS_CONFIG"):
    argv += ["--config", os.path.join(ROOT, os.environ["HELIOS_DS_CONFIG"])]
if os.environ.get("HELIOS_DS_INIT_TABLES") == "1":
    argv.append("--init-tables")
exit_code = main(argv)
if exit_code:
    raise SystemExit(exit_code)
