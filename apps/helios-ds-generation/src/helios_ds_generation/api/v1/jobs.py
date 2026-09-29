"""Generation and job endpoints (spec: "REST contract").

Job state lives in the helios_ds lakehouse (generation_jobs + job_events), so it
survives API restarts. Work runs as Workbench Job runs started by the
dispatcher. Endpoints are sync (FastAPI runs them in a thread pool) because
lakehouse calls block for up to a couple of seconds.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import ValidationError

from helios_ds.jobs import IdempotencyConflict, JobNotFound, JobState, JobView

from .models import GenerationAccepted, GenerationRequest, Job
from .service import GenerationService, get_service

router = APIRouter()


def _get(service: GenerationService, job_id: str) -> JobView:
    try:
        return service.jobs.get(job_id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found") from None


@router.post(
    "/generations", status_code=status.HTTP_202_ACCEPTED, response_model=GenerationAccepted
)
def create_generation(
    request: GenerationRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    service: GenerationService = Depends(get_service),
) -> GenerationAccepted:
    """Validate the config, record a job, start its worker and return its handle."""
    try:
        config = request.to_dataset_config()
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
        raise HTTPException(status_code=422, detail=errors) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        job, created = service.jobs.create(
            request.model_dump(mode="json"), config, service.dispatcher.name, idempotency_key
        )
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if created:
        try:
            run_id = service.dispatcher.dispatch(job.job_id)
            if run_id:
                service.jobs.append_event(
                    job.job_id,
                    JobState.QUEUED,
                    actor="api",
                    workbench_run_id=run_id,
                    message="worker run started",
                )
        except Exception as exc:
            service.jobs.append_event(
                job.job_id,
                JobState.FAILED,
                actor="api",
                message=f"could not start the worker: {type(exc).__name__}: {exc}",
            )
        job = service.jobs.get(job.job_id)
    return GenerationAccepted(
        job_id=job.job_id, state=job.state, status_uri=f"/v1/jobs/{job.job_id}"
    )


@router.get("/jobs", response_model=List[Job])
def list_jobs(
    state: Optional[JobState] = Query(None, description="Filter by state"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: GenerationService = Depends(get_service),
) -> List[Job]:
    """List jobs, newest first. Not in the spec contract; used by the dashboard."""
    views = [v for v in service.jobs.list() if state is None or v.state is state]
    return [Job.from_view(v) for v in views[offset : offset + limit]]


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str, service: GenerationService = Depends(get_service)) -> Job:
    return Job.from_view(_get(service, job_id))


@router.post("/jobs/{job_id}:cancel", response_model=Job)
def cancel_job(job_id: str, service: GenerationService = Depends(get_service)) -> Job:
    """Cancel a job: stop its Workbench run (if any) and record CANCELLED. A
    worker that is still running stops at its next checkpoint."""
    job = _get(service, job_id)
    if job.terminal:
        raise HTTPException(status_code=409, detail=f"cannot cancel job in state {job.state.value}")
    note = "cancelled through the API"
    if job.workbench_run_id:
        try:
            service.dispatcher.stop(job.workbench_run_id)
        except Exception as exc:
            note += f"; stopping run {job.workbench_run_id} failed: {type(exc).__name__}: {exc}"
    service.jobs.append_event(job_id, JobState.CANCELLED, actor="api", message=note)
    return Job.from_view(_get(service, job_id))
