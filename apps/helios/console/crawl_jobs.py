"""Starting crawls as runs of a Workbench Job.

The API never crawls in its own process: it starts a run of the ``helios-crawl``
Job through the Cloudera AI API, passing what to crawl in the run's environment.
Workbench gives the crawl its own container, keeps its log and can stop it; the
crawl records itself in ``helios_index.crawl_runs`` as before.

The Job is created on first use, with the API Application's runtime. A crawl
connects as the Job's WORKLOAD_USER: to run crawls as the crawler machine user,
set WORKLOAD_USER and WORKLOAD_PASSWORD in the Job's environment in Workbench.
"""

from __future__ import annotations

import json
import os
from typing import Any

JOB_SCRIPT = "helios/jobs/crawl.py"
API_SCRIPT = "helios/apps/helios/console/app.py"
# Workbench run statuses after which a run will not change again.
FINISHED_SUFFIXES = ("SUCCEEDED", "FAILED", "STOPPED", "TIMEDOUT", "KILLED")


class WorkbenchUnavailable(RuntimeError):
    """The Cloudera AI API cannot be used from this process."""


def job_name() -> str:
    return os.environ.get("HELIOS_CRAWL_JOB_NAME", "helios-crawl")


def crawler_identity() -> str:
    """The user crawls are meant to run as; any other actor can read more than
    the crawler should (CR-0d)."""
    return os.environ.get("HELIOS_CRAWLER_IDENTITY", "srv_helios_crawler")


def workbench() -> tuple[Any, str]:
    """(Cloudera AI API client, project ID)."""
    project_id = os.environ.get("CDSW_PROJECT_ID")
    if not project_id:
        raise WorkbenchUnavailable("not running in a Workbench project (CDSW_PROJECT_ID is unset)")
    try:
        import cmlapi
    except ImportError as exc:
        raise WorkbenchUnavailable("the cmlapi package is not installed") from exc
    domain = os.environ.get("CDSW_DOMAIN")
    try:
        return cmlapi.default_client(url=f"https://{domain}" if domain else None), project_id
    except Exception as exc:  # noqa: BLE001 - cmlapi raises bare errors for missing keys
        raise WorkbenchUnavailable(f"the Workbench API is not usable: {exc}") from exc


def _find_job(client: Any, project_id: str) -> Any | None:
    jobs = client.list_jobs(project_id, page_size=200).jobs or []
    return next((job for job in jobs if job.name == job_name()), None)


def _runtime(client: Any, project_id: str) -> str:
    configured = os.environ.get("HELIOS_CRAWL_JOB_RUNTIME")
    if configured:
        return configured
    applications = client.list_applications(project_id, page_size=200).applications or []
    runtime = next((a.runtime_identifier for a in applications if a.script == API_SCRIPT), None)
    if not runtime:
        raise WorkbenchUnavailable(
            "cannot tell which runtime the crawl Job should use; set HELIOS_CRAWL_JOB_RUNTIME"
        )
    return runtime


def ensure_job(client: Any, project_id: str) -> Any:
    """The crawl Job, created if the project doesn't have it yet."""
    job = _find_job(client, project_id)
    if job is not None:
        return job
    # Request bodies are plain dicts (the client serialises them as it does its
    # own models), so only workbench() needs the cmlapi package.
    return client.create_job(
        {
            "project_id": project_id,
            "name": job_name(),
            "script": JOB_SCRIPT,
            "kernel": "python3",
            "cpu": float(os.environ.get("HELIOS_CRAWL_JOB_CPU", "4")),
            "memory": float(os.environ.get("HELIOS_CRAWL_JOB_MEMORY", "16")),
            "runtime_identifier": _runtime(client, project_id),
        },
        project_id,
    )


def _environment(run: Any) -> dict[str, str]:
    raw = getattr(run, "environment", None)
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    try:
        parsed = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}


def _time(value: Any) -> str | None:
    """ISO time, or None; Workbench reports "not yet" as year 1."""
    if not value or getattr(value, "year", 9999) < 2000:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def launch_view(run: Any) -> dict[str, Any]:
    environment = _environment(run)
    status = str(getattr(run, "status", "") or "")
    return {
        "job_run_id": str(run.id),
        "status": status,
        "active": not status.upper().endswith(FINISHED_SUFFIXES),
        "created_at": _time(getattr(run, "created_at", None)),
        "finished_at": _time(getattr(run, "finished_at", None)),
        "source_id": environment.get("HELIOS_CRAWL_SOURCE") or None,
        "dataset_id": environment.get("HELIOS_CRAWL_DATASET") or None,
        "full": environment.get("HELIOS_CRAWL_FULL") == "1",
        "requested_by": environment.get("HELIOS_CRAWL_REQUESTED_BY") or None,
    }


def launches(client: Any, project_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """The crawl Job's latest runs, newest first; empty if the Job doesn't exist yet."""
    job = _find_job(client, project_id)
    if job is None:
        return []
    runs = client.list_job_runs(project_id, job.id, page_size=limit, sort="-created_at").job_runs
    return [launch_view(run) for run in runs or []]


def launch(
    client: Any,
    project_id: str,
    *,
    source_id: str | None,
    dataset_id: str | None,
    full: bool,
    requested_by: str,
) -> dict[str, Any]:
    job = ensure_job(client, project_id)
    environment = {
        "HELIOS_CRAWL_REQUESTED_BY": requested_by,
        "HELIOS_CRAWL_FULL": "1" if full else "0",
        **({"HELIOS_CRAWL_SOURCE": source_id} if source_id else {}),
        **({"HELIOS_CRAWL_DATASET": dataset_id} if dataset_id else {}),
    }
    run = client.create_job_run(
        {"project_id": project_id, "job_id": job.id, "environment": environment},
        project_id,
        job.id,
    )
    view = launch_view(run)
    # The create response may not echo the environment back.
    return {
        **view,
        "source_id": source_id,
        "dataset_id": dataset_id,
        "full": full,
        "requested_by": requested_by,
    }
