"""Dispatchers start the worker for a job.

- ``WorkbenchJobsDispatcher``: starts a run of the ``helios-ds-generate``
  Workbench Job through the Cloudera AI API, passing HELIOS_DS_JOB_ID in the
  run's environment. Workbench schedules it with the Job's runtime and resource
  profile, keeps its logs, and can stop it. This replaces Celery + Redis
  (decision 2026-09-29: no broker or database services in this environment).
- ``InlineDispatcher``: runs the worker in a background thread of the calling
  process. For local development and tests only.
"""

import os
import threading
from typing import Any, Callable, Optional, Protocol

GENERATE_JOB_NAME = "helios-ds-generate"


class Dispatcher(Protocol):
    name: str

    def dispatch(self, job_id: str) -> Optional[str]:
        """Start the worker for ``job_id``. Returns a run ID when there is one."""
        ...

    def stop(self, run_id: str) -> None: ...


class WorkbenchJobsDispatcher:
    name = "workbench"

    def __init__(self, client: Any, project_id: str, job_name: str = GENERATE_JOB_NAME):
        self.client = client
        self.project_id = project_id
        self.job_name = job_name
        self._workbench_job_id: Optional[str] = None

    def _job_id(self) -> str:
        if self._workbench_job_id is None:
            jobs = self.client.list_jobs(self.project_id, page_size=100).jobs
            matches = [j.id for j in jobs if j.name == self.job_name]
            if not matches:
                raise LookupError(
                    f"Workbench Job {self.job_name!r} not found in project {self.project_id}; "
                    "create it with workbench/setup_jobs.py"
                )
            self._workbench_job_id = matches[0]
        return self._workbench_job_id

    def dispatch(self, job_id: str) -> Optional[str]:
        import cmlapi

        body = cmlapi.CreateJobRunRequest(
            project_id=self.project_id,
            job_id=self._job_id(),
            environment={"HELIOS_DS_JOB_ID": job_id},
        )
        run = self.client.create_job_run(body, self.project_id, self._job_id())
        return str(run.id)

    def stop(self, run_id: str) -> None:
        self.client.stop_job_run(self.project_id, self._job_id(), run_id)


class InlineDispatcher:
    name = "inline"

    def __init__(self, runner: Callable[[str], Any], background: bool = True):
        self.runner = runner
        self.background = background

    def dispatch(self, job_id: str) -> Optional[str]:
        if self.background:
            threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        else:
            self._run(job_id)
        return None

    def _run(self, job_id: str) -> None:
        try:
            self.runner(job_id)
        except Exception:  # the worker has already recorded FAILED with the cause
            pass

    def stop(self, run_id: str) -> None:
        pass  # inline runs stop cooperatively at their next checkpoint


def workbench_client() -> Any:
    """Cloudera AI API client. Uses HELIOS_DS_API_KEY when set, otherwise the
    workload's own API credentials."""
    import cmlapi

    domain = os.environ.get("CDSW_DOMAIN")
    url = f"https://{domain}" if domain else None
    return cmlapi.default_client(url=url, cml_api_key=os.environ.get("HELIOS_DS_API_KEY") or None)


def dispatcher_from_env(runner: Optional[Callable[[str], Any]] = None) -> Dispatcher:
    """HELIOS_DS_DISPATCHER=workbench (default) or inline."""
    kind = os.environ.get("HELIOS_DS_DISPATCHER", "workbench")
    if kind == "workbench":
        return WorkbenchJobsDispatcher(workbench_client(), os.environ["CDSW_PROJECT_ID"])
    if kind == "inline":
        if runner is None:
            from .worker import run_from_env

            runner = run_from_env
        return InlineDispatcher(runner)
    raise ValueError(f"unknown HELIOS_DS_DISPATCHER {kind!r}; use workbench or inline")
