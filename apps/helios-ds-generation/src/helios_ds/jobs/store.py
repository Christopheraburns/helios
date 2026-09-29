"""Generation job state in the lakehouse (helios_ds.generation_jobs + job_events).

Job state is an append-only event log, so the API and workers never update rows
in place. The current view of a job is reduced from its events in time order,
and **the first terminal event wins**: once a job is SUCCEEDED, FAILED or
CANCELLED, later events (for example a worker finishing after a cancel) are
ignored. That keeps concurrent writers safe without row locks.

The lakehouse is slow per write (about 1-2 s through Impala), so callers write
events at coarse milestones, never per artifact.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from ..config import DatasetConfig
from ..lakehouse import LakehouseSink
from ..schemas import GenerationJobRecord, JobEventRecord

JOBS = "helios_ds.generation_jobs"
EVENTS = "helios_ds.job_events"


class JobState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})


class JobNotFound(KeyError):
    pass


class IdempotencyConflict(ValueError):
    """The idempotency key was already used for a different request."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_hash(request: Dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class JobView:
    """A job as reduced from its record and events."""

    record: GenerationJobRecord
    state: JobState
    updated_at: str
    progress_percent: int = 0
    dataset_id: Optional[str] = None
    workbench_run_id: Optional[str] = None
    message: Optional[str] = None
    events: List[JobEventRecord] = field(default_factory=list)

    @property
    def job_id(self) -> str:
        return self.record.job_id

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL


def reduce_job(record: GenerationJobRecord, events: List[JobEventRecord]) -> JobView:
    ordered = sorted(events, key=lambda e: (e.occurred_at, e.event_id))
    view = JobView(record=record, state=JobState.QUEUED, updated_at=record.created_at)
    for event in ordered:
        if view.terminal:
            break  # first terminal event wins
        view.state = JobState(event.state)
        view.updated_at = event.occurred_at
        view.progress_percent = event.progress_percent
        view.dataset_id = event.dataset_id or view.dataset_id
        view.workbench_run_id = event.workbench_run_id or view.workbench_run_id
        view.message = event.message
    view.events = ordered
    return view


class JobStore:
    def __init__(self, sink: LakehouseSink, clock: Callable[[], str] = utc_now):
        self.sink = sink
        self.clock = clock

    def create(
        self,
        request: Dict[str, Any],
        config: DatasetConfig,
        dispatcher: str,
        idempotency_key: Optional[str] = None,
    ) -> tuple[JobView, bool]:
        """Record a new QUEUED job. Returns (job, created). With an idempotency key
        that was already used for the same request, returns the existing job."""
        digest = request_hash(request)
        if idempotency_key:
            existing = self.sink.read(JOBS, where={"idempotency_key": idempotency_key})
            if existing:
                record = existing[0]
                assert isinstance(record, GenerationJobRecord)
                if record.request_hash != digest:
                    raise IdempotencyConflict(
                        f"Idempotency-Key {idempotency_key!r} was used for a different request"
                    )
                return self.get(record.job_id), False
        record = GenerationJobRecord(
            job_id=f"job_{uuid.uuid4().hex}",
            created_at=self.clock(),
            config_hash=config.config_hash(),
            config_json=config.canonical_json(),
            request=request,
            request_hash=digest,
            idempotency_key=idempotency_key,
            dispatcher=dispatcher,
        )
        self.sink.append(JOBS, [record])
        self.append_event(record.job_id, JobState.QUEUED, actor="api")
        return self.get(record.job_id), True

    def append_event(
        self,
        job_id: str,
        state: JobState,
        actor: str,
        progress_percent: int = 0,
        dataset_id: Optional[str] = None,
        workbench_run_id: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        self.sink.append(
            EVENTS,
            [
                JobEventRecord(
                    event_id=uuid.uuid4().hex,
                    job_id=job_id,
                    occurred_at=self.clock(),
                    state=state.value,
                    progress_percent=progress_percent,
                    actor=actor,
                    dataset_id=dataset_id,
                    workbench_run_id=workbench_run_id,
                    message=message[:4000] if message else None,
                )
            ],
        )

    def get(self, job_id: str) -> JobView:
        records = self.sink.read(JOBS, where={"job_id": job_id})
        if not records:
            raise JobNotFound(job_id)
        record = records[0]
        assert isinstance(record, GenerationJobRecord)
        events = [
            e
            for e in self.sink.read(EVENTS, where={"job_id": job_id})
            if isinstance(e, JobEventRecord)
        ]
        return reduce_job(record, events)

    def list(self) -> List[JobView]:
        """All jobs, newest first."""
        events_by_job: Dict[str, List[JobEventRecord]] = {}
        for event in self.sink.read(EVENTS):
            assert isinstance(event, JobEventRecord)
            events_by_job.setdefault(event.job_id, []).append(event)
        views = [
            reduce_job(record, events_by_job.get(record.job_id, []))
            for record in self.sink.read(JOBS)
            if isinstance(record, GenerationJobRecord)
        ]
        return sorted(views, key=lambda v: v.record.created_at, reverse=True)
