"""Generation and job endpoints.

Jobs are held in memory and never execute: this is the P0 contract stub. Task
J-01 moves job state to PostgreSQL and J-02/J-03 add Celery execution,
idempotency keys and real cancellation.
"""

from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from .models import CANCELLABLE_STATES, GenerationAccepted, GenerationRequest, Job, JobState

router = APIRouter()

_jobs: Dict[str, Job] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(job_id: str) -> Job:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return job


@router.post(
    "/generations", status_code=status.HTTP_202_ACCEPTED, response_model=GenerationAccepted
)
async def create_generation(request: GenerationRequest) -> GenerationAccepted:
    """Validate the config, create a job and return its handle."""
    try:
        config = request.to_dataset_config()
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        raise HTTPException(status_code=422, detail=errors) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    job_id = f"job_{uuid4().hex}"
    now = _now()
    _jobs[job_id] = Job(
        job_id=job_id,
        state=JobState.QUEUED,
        config_hash=config.config_hash(),
        request=request,
        created_at=now,
        updated_at=now,
    )
    return GenerationAccepted(job_id=job_id, state=JobState.QUEUED, status_uri=f"/v1/jobs/{job_id}")


@router.get("/jobs", response_model=List[Job])
async def list_jobs(
    state: Optional[JobState] = Query(None, description="Filter by state"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> List[Job]:
    """List jobs, newest first. Not in the spec contract; used by the dashboard."""
    jobs = [j for j in _jobs.values() if state is None or j.state == state]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return jobs[offset : offset + limit]


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    return _get(job_id)


@router.post("/jobs/{job_id}:cancel", response_model=Job)
async def cancel_job(job_id: str) -> Job:
    """Cooperative cancellation."""
    job = _get(job_id)
    if job.state not in CANCELLABLE_STATES:
        raise HTTPException(status_code=409, detail=f"cannot cancel job in state {job.state.value}")
    job.state = JobState.CANCELLED
    job.updated_at = _now()
    return job
