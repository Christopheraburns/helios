"""Shared bootstrap for Workbench Jobs (which run in a Jupyter kernel: no __file__)."""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RUNS_DIR = os.environ.get("HELIOS_RUNS_DIR") or os.path.join(ROOT, "runs")


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def latest_run_id() -> str | None:
    if not os.path.isdir(RUNS_DIR):
        return None
    runs = sorted(d for d in os.listdir(RUNS_DIR) if os.path.isdir(os.path.join(RUNS_DIR, d)))
    return runs[-1] if runs else None


def run_path(run_id: str, name: str) -> str:
    os.makedirs(os.path.join(RUNS_DIR, run_id), exist_ok=True)
    return os.path.join(RUNS_DIR, run_id, name)


def write_json(path: str, data) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"wrote {path} ({os.path.getsize(path):,} bytes)")


def read_json(path: str):
    with open(path) as f:
        return json.load(f)


def engine():
    from helios_core.config import impala_config
    from helios_core.engines import ImpalaEngine
    cfg = impala_config()
    if cfg is None:
        raise SystemExit("IMPALA_HOST / credentials are not set in the project environment")
    return ImpalaEngine(cfg)


def atlas():
    from helios_core.atlas import AtlasClient
    from helios_core.config import atlas_config
    cfg = atlas_config()
    return AtlasClient(cfg) if cfg else None
