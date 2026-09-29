"""Phase 2 job layer: lakehouse job state, worker, dispatchers."""

import sys
import types

import pytest

from helios_ds.jobs import (
    IdempotencyConflict,
    InlineDispatcher,
    JobState,
    JobStore,
    WorkbenchJobsDispatcher,
    run_generation_job,
)

REQUEST = {"master_seed": 42}


def _store(make_sink):
    sink = make_sink()
    sink.ensure_tables()
    return JobStore(sink), sink


def test_create_and_get(make_sink, tiny_config):
    jobs, _ = _store(make_sink)
    job, created = jobs.create(REQUEST, tiny_config, "workbench")
    assert created and job.state is JobState.QUEUED
    assert jobs.get(job.job_id).record.config_hash == tiny_config.config_hash()


def test_idempotency_key(make_sink, tiny_config):
    jobs, _ = _store(make_sink)
    first, _ = jobs.create(REQUEST, tiny_config, "workbench", idempotency_key="k")
    again, created = jobs.create(REQUEST, tiny_config, "workbench", idempotency_key="k")
    assert (again.job_id, created) == (first.job_id, False)
    with pytest.raises(IdempotencyConflict):
        jobs.create({"master_seed": 1}, tiny_config, "workbench", idempotency_key="k")


def test_first_terminal_event_wins(make_sink, tiny_config):
    jobs, _ = _store(make_sink)
    job, _ = jobs.create(REQUEST, tiny_config, "workbench")
    jobs.append_event(job.job_id, JobState.RUNNING, "worker")
    jobs.append_event(job.job_id, JobState.CANCELLED, "api")
    jobs.append_event(job.job_id, JobState.SUCCEEDED, "worker", progress_percent=100)  # too late
    view = jobs.get(job.job_id)
    assert view.state is JobState.CANCELLED
    assert [e.state for e in view.events] == ["QUEUED", "RUNNING", "CANCELLED", "SUCCEEDED"]


def test_state_survives_a_new_store_instance(make_sink, tiny_config):
    jobs, sink = _store(make_sink)
    job, _ = jobs.create(REQUEST, tiny_config, "workbench")
    jobs.append_event(job.job_id, JobState.RUNNING, "worker")
    assert JobStore(sink).get(job.job_id).state is JobState.RUNNING
    assert [v.job_id for v in JobStore(sink).list()] == [job.job_id]


def test_worker_runs_the_pipeline_and_records_success(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("w")
    jobs = JobStore(sink)
    job, _ = jobs.create(REQUEST, tiny_config, "workbench")
    done = run_generation_job(job.job_id, jobs, small_repo, sink, store, templates)
    assert done.state is JobState.SUCCEEDED and done.progress_percent == 100
    assert len(sink.read_dataset("helios_ds.datasets", done.dataset_id)) == 1
    runs = sink.read_dataset("helios_ds.generation_runs", done.dataset_id)
    assert [r.job_id for r in runs] == [job.job_id]

    # A second run of a finished job changes nothing.
    events = len(jobs.get(job.job_id).events)
    run_generation_job(job.job_id, jobs, small_repo, sink, store, templates)
    assert len(jobs.get(job.job_id).events) == events


def test_worker_retry_after_a_crash_creates_no_duplicates(
    make_env, tiny_config, small_repo, templates
):
    sink, store = make_env("w")
    jobs = JobStore(sink)
    job, _ = jobs.create(REQUEST, tiny_config, "workbench")
    jobs.append_event(job.job_id, JobState.RUNNING, "worker")  # a run that died mid-way
    done = run_generation_job(job.job_id, jobs, small_repo, sink, store, templates)
    assert done.state is JobState.SUCCEEDED
    assert len(sink.read_dataset("helios_ds.datasets", done.dataset_id)) == 1


def test_cancelled_job_does_not_run(make_env, tiny_config, small_repo, templates):
    sink, store = make_env("w")
    jobs = JobStore(sink)
    job, _ = jobs.create(REQUEST, tiny_config, "workbench")
    jobs.append_event(job.job_id, JobState.CANCELLED, "api")
    assert run_generation_job(job.job_id, jobs, small_repo, sink, store, templates).state is (
        JobState.CANCELLED
    )
    assert sink.read("helios_ds.datasets") == []


def test_worker_failure_records_an_actionable_cause(make_env, tiny_config, templates):
    sink, store = make_env("w")
    jobs = JobStore(sink)
    job, _ = jobs.create(REQUEST, tiny_config, "workbench")

    class BrokenTpcds:
        backend = "broken"

        def fingerprint(self, tables):
            raise ConnectionError("Impala Virtual Warehouse is suspended")

    with pytest.raises(ConnectionError):
        run_generation_job(job.job_id, jobs, BrokenTpcds(), sink, store, templates)
    view = jobs.get(job.job_id)
    assert view.state is JobState.FAILED
    assert view.message == "ConnectionError: Impala Virtual Warehouse is suspended"


class _FakeCml:
    def __init__(self, jobs):
        self._jobs, self.runs, self.stopped = jobs, [], []

    def list_jobs(self, project_id, page_size):
        return types.SimpleNamespace(jobs=self._jobs)

    def create_job_run(self, body, project_id, job_id):
        self.runs.append((project_id, job_id, body.environment))
        return types.SimpleNamespace(id="run-42")

    def stop_job_run(self, project_id, job_id, run_id):
        self.stopped.append((project_id, job_id, run_id))


@pytest.fixture
def fake_cmlapi(monkeypatch):
    module = types.ModuleType("cmlapi")
    module.CreateJobRunRequest = lambda **kwargs: types.SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "cmlapi", module)


def test_workbench_dispatcher_starts_and_stops_runs(fake_cmlapi):
    client = _FakeCml([types.SimpleNamespace(name="helios-ds-generate", id="wb-job-1")])
    dispatcher = WorkbenchJobsDispatcher(client, "proj-1")
    assert dispatcher.dispatch("job_abc") == "run-42"
    assert client.runs == [("proj-1", "wb-job-1", {"HELIOS_DS_JOB_ID": "job_abc"})]
    dispatcher.stop("run-42")
    assert client.stopped == [("proj-1", "wb-job-1", "run-42")]


def test_workbench_dispatcher_explains_a_missing_job(fake_cmlapi):
    with pytest.raises(LookupError, match="setup_jobs.py"):
        WorkbenchJobsDispatcher(_FakeCml([]), "proj-1").dispatch("job_abc")


def test_inline_dispatcher_runs_the_worker():
    seen = []
    assert InlineDispatcher(seen.append, background=False).dispatch("job_x") is None
    assert seen == ["job_x"]
