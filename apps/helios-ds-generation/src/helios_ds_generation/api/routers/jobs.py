"""Job management endpoints for generation runs."""
from datetime import datetime, timezone
from typing import List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter()


class JobConfig(BaseModel):
    """Job configuration request."""
    tpcds_scale_factor: int = Field(default=1, ge=1, le=100)
    master_seed: int = Field(default=42)
    artifact_targets: dict = Field(default_factory=dict)
    difficulty_profile: str = Field(default="balanced")


class JobStatus(BaseModel):
    """Job status response."""
    job_id: str
    status: str  # PENDING, RUNNING, COMPLETED, FAILED
    config: JobConfig
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    artifacts_generated: int = 0
    scenarios_processed: int = 0
    error_message: Optional[str] = None


class JobSummary(BaseModel):
    """Brief job summary for listing."""
    job_id: str
    status: str
    progress_percent: int
    started_at: Optional[str] = None
    artifacts_generated: int


# In-memory job store (placeholder; would use database in production)
_jobs: dict = {}


@router.post("/submit", response_model=JobStatus)
async def submit_job(config: JobConfig) -> JobStatus:
    """Submit a new generation job.

    Args:
        config: Generation configuration

    Returns:
        JobStatus with newly created job ID
    """
    job_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()

    job = JobStatus(
        job_id=job_id,
        status="PENDING",
        config=config,
        started_at=now,
    )

    _jobs[job_id] = job
    return job


@router.get("/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str) -> JobStatus:
    """Get status of a specific job.

    Args:
        job_id: Job identifier

    Returns:
        Current job status

    Raises:
        HTTPException: If job not found
    """
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return _jobs[job_id]


@router.get("", response_model=List[JobSummary])
async def list_jobs(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> List[JobSummary]:
    """List generation jobs.

    Args:
        status: Optional status filter
        limit: Maximum jobs to return
        offset: Offset for pagination

    Returns:
        List of job summaries
    """
    jobs = list(_jobs.values())

    if status:
        jobs = [j for j in jobs if j.status == status]

    # Sort by started_at descending (most recent first)
    jobs.sort(key=lambda j: j.started_at or "", reverse=True)

    return [
        JobSummary(
            job_id=j.job_id,
            status=j.status,
            progress_percent=j.progress_percent,
            started_at=j.started_at,
            artifacts_generated=j.artifacts_generated,
        )
        for j in jobs[offset : offset + limit]
    ]


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict:
    """Cancel a running job.

    Args:
        job_id: Job identifier

    Returns:
        Cancellation confirmation

    Raises:
        HTTPException: If job not found or not cancellable
    """
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    job = _jobs[job_id]
    if job.status not in ("PENDING", "RUNNING"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel job with status {job.status}"
        )

    job.status = "CANCELLED"
    return {"job_id": job_id, "status": "cancelled"}
