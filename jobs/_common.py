"""Shared bootstrap for Workbench Jobs (which run in a Jupyter kernel: no __file__)."""
import json
import os
import sys
import time
from contextlib import contextmanager
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


@contextmanager
def audit_job(stage: str, run_id: str, model_id: str | None = None):
    from helios_core import audit
    from helios_core.metadata import SQLiteMetadataRepository

    repository = SQLiteMetadataRepository()
    repository.migrate()
    model = repository.model(model_id) if model_id else None
    subject = os.environ.get("CDSW_USER") or os.environ.get(
        "HELIOS_JOB_PRINCIPAL"
    )
    principal_id = (
        f"cloudera-workbench:{subject}" if subject else "service:helios-job"
    )
    session_id = audit.normalize_correlation_id(
        os.environ.get("CDSW_JOB_ID")
    ) or f"job-{run_id}"
    token = audit.set_context(
        audit.AuditContext(
            request_id=audit.new_request_id(),
            session_id=session_id,
            principal_id=principal_id,
            organization_id=model.organization_id if model else None,
            model_id=model_id,
        )
    )
    started = time.perf_counter()
    audit.emit(
        repository,
        component="job",
        event_type="job.lifecycle",
        action=stage,
        outcome="started",
        summary=f"{stage} job started",
        resource_type="run",
        resource_id=run_id,
        details={"stage": stage},
    )
    try:
        yield
    except BaseException as exc:
        audit.emit(
            repository,
            component="job",
            event_type="job.lifecycle",
            action=stage,
            outcome="error",
            severity="error",
            summary=f"{stage} job failed",
            resource_type="run",
            resource_id=run_id,
            duration_ms=(time.perf_counter() - started) * 1000,
            details={"stage": stage, "error_type": type(exc).__name__},
        )
        raise
    else:
        audit.emit(
            repository,
            component="job",
            event_type="job.lifecycle",
            action=stage,
            outcome="success",
            summary=f"{stage} job completed",
            resource_type="run",
            resource_id=run_id,
            duration_ms=(time.perf_counter() - started) * 1000,
            details={"stage": stage},
        )
    finally:
        audit.reset_context(token)


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
