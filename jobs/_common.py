"""Shared bootstrap for Workbench Jobs (which run in a Jupyter kernel: no __file__).

Each Job is a thin host: it maps its environment variables to arguments of the
``helios`` command (burndown 5.5) and runs it with the Workbench settings profile,
inside the console's audit record. Configuration (connections, the registry, where
runs and artifacts live) is the command's, read by ``WorkbenchSettings``.
"""
import os
import shlex
import sys
import time
from contextlib import contextmanager

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
# Workbench Jobs run from the checkout, not an install: put the repo (for apps.*)
# and the helios-cortex, helios-cloudera and helios-model sources on the path until the runtime installs wheels.
for path in (
    ROOT,
    os.path.join(ROOT, "packages", "helios-cortex", "src"),
    os.path.join(ROOT, "packages", "helios-cloudera", "src"),
    os.path.join(ROOT, "packages", "helios-model", "src"),
):
    if path not in sys.path:
        sys.path.insert(0, path)


def env_list(name: str) -> list[str]:
    """A comma-separated variable as a list (empty when unset)."""
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


def latest_run_id() -> str | None:
    from helios_cloudera.workbench import WorkbenchSettings
    from helios_cortex.runs import RunStore

    return RunStore.from_settings(WorkbenchSettings()).latest_run_id()


def run_model_id(run_id: str) -> str | None:
    from helios_cloudera.workbench import WorkbenchSettings
    from helios_cortex.runs import RunStore

    return (RunStore.from_settings(WorkbenchSettings()).load(run_id, "harvest") or {}).get("model_id")


def helios(stage: str, arguments: list[str], run_id: str, model_id: str | None = None) -> None:
    """Run ``helios --profile workbench <stage> <arguments>`` as this Job, audited."""
    from helios_cortex.cli import main

    argv = ["--profile", "workbench", stage, *arguments]
    print("helios " + shlex.join(argv), flush=True)
    with audit_job(stage, run_id, model_id):
        status = main(argv)
        if status:
            raise SystemExit(status)


@contextmanager
def audit_job(stage: str, run_id: str, model_id: str | None = None):
    from apps.helios.console import audit
    from apps.helios.console.metadata import SQLiteMetadataRepository
    from helios_cloudera.workbench import WorkbenchSettings as Settings

    repository = SQLiteMetadataRepository.from_settings(Settings())
    findings = repository.integrity_check()
    if findings != ("ok",):
        raise RuntimeError(
            "Helios metadata is unavailable; initialize or repair it through "
            f"the API Application before running Jobs ({findings[0]})"
        )
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
