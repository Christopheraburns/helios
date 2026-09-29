"""The generation worker: runs one job to completion and records its events.

Workbench runs this as the ``helios-ds-generate`` Job (workbench/run_generation.py)
with HELIOS_DS_JOB_ID set. It is safe to run more than once for the same job:
the pipeline is idempotent (deterministic IDs, write-if-hash-matches objects,
publish-once lakehouse rows), and a job that already finished is left alone.

Phase 2 runs the Foundation pipeline (plan + manifest + publish). Phase 3 adds
artifact rendering here, with progress events per batch.
"""

import os
from typing import Optional

from ..backends import lakehouse_from_uri, object_store_from_uri, tpcds_from_uri
from ..config import DatasetConfig
from ..lakehouse import LakehouseSink
from ..object_store import ObjectStore
from ..pipeline import plan_and_publish
from ..templates import TemplateRegistry
from ..tpcds import TpcdsRepository
from .store import JobState, JobStore, JobView


class JobCancelled(RuntimeError):
    pass


def _check_cancelled(jobs: JobStore, job_id: str) -> None:
    if jobs.get(job_id).state is JobState.CANCELLED:
        raise JobCancelled(job_id)


def run_generation_job(
    job_id: str,
    jobs: JobStore,
    tpcds: TpcdsRepository,
    sink: LakehouseSink,
    store: ObjectStore,
    templates: Optional[TemplateRegistry] = None,
    workbench_run_id: Optional[str] = None,
) -> JobView:
    job = jobs.get(job_id)
    if job.terminal:
        return job  # already finished (or cancelled): nothing to do on a re-run

    jobs.append_event(job_id, JobState.RUNNING, actor="worker", workbench_run_id=workbench_run_id)
    try:
        config = DatasetConfig.model_validate_json(job.record.config_json)
        _check_cancelled(jobs, job_id)
        result = plan_and_publish(
            config, tpcds, templates or TemplateRegistry.load(), sink, store, job_id=job_id
        )
        _check_cancelled(jobs, job_id)
    except JobCancelled:
        return jobs.get(job_id)
    except Exception as exc:
        jobs.append_event(
            job_id, JobState.FAILED, actor="worker", message=f"{type(exc).__name__}: {exc}"
        )
        raise
    jobs.append_event(
        job_id,
        JobState.SUCCEEDED,
        actor="worker",
        progress_percent=100,
        dataset_id=result.dataset_id,
        message=(
            f"dataset {result.dataset_id} is {result.state.value}; "
            f"{'published' if result.newly_published else 'already published, reproduced'}"
        ),
    )
    return jobs.get(job_id)


def run_from_env(job_id: Optional[str] = None) -> JobView:
    """Build backends from HELIOS_DS_* settings and run one job."""
    job_id = job_id or os.environ["HELIOS_DS_JOB_ID"]
    sink = lakehouse_from_uri()
    return run_generation_job(
        job_id,
        JobStore(sink),
        tpcds_from_uri(),
        sink,
        object_store_from_uri(),
    )
