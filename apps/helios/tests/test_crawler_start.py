"""Crawls start as runs of the Workbench Job (crawl_jobs). The old console's crawler API
called these; it went with the console (burndown 8.2), so they are tested directly."""

import json
from types import SimpleNamespace

import pytest
from apps.helios.crawler import crawl_jobs

DATASET = "1ca99f86-e6a0-57fc-8311-cadcac4c8302"


class FakeWorkbench:
    """Enough of the Cloudera AI API client: jobs and job runs."""

    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.runs = []
        self.created_jobs = []

    def list_jobs(self, project_id, page_size=None):
        return SimpleNamespace(jobs=self.jobs)

    def create_job(self, body, project_id):
        job = SimpleNamespace(id="job-1", name=body["name"])
        self.created_jobs.append(body)
        self.jobs.append(job)
        return job

    def create_job_run(self, body, project_id, job_id):
        run = SimpleNamespace(
            id=f"run-{len(self.runs) + 1}",
            status="ENGINE_SCHEDULING",
            created_at=None,
            finished_at=None,
            environment=json.dumps(body["environment"]),
        )
        self.runs.insert(0, run)
        return run

    def list_job_runs(self, project_id, job_id, page_size=None, sort=None):
        return SimpleNamespace(job_runs=self.runs[:page_size])


@pytest.fixture
def workbench(monkeypatch):
    monkeypatch.setenv("HELIOS_CRAWL_JOB_RUNTIME", "runtime:1")
    return FakeWorkbench()


def test_launching_creates_the_job_once_and_runs_it(workbench):
    launched = crawl_jobs.launch(workbench, "project-1", dataset_id=DATASET, full=False, requested_by="alice")

    assert launched["job_run_id"] == "run-1" and launched["active"] is True
    assert (launched["dataset_id"], launched["requested_by"], launched["note"]) == (DATASET, "alice", None)
    [job] = workbench.created_jobs
    assert (job["name"], job["script"], job["runtime_identifier"]) == (
        "helios-crawl", "helios/apps/helios/crawler/job.py", "runtime:1")
    environment = json.loads(workbench.runs[0].environment)
    assert environment == {"HELIOS_CRAWL_DATASET": DATASET, "HELIOS_CRAWL_FULL": "0",
                           "HELIOS_CRAWL_REQUESTED_BY": "alice"}

    workbench.runs[0].status = "ENGINE_SUCCEEDED"
    again = crawl_jobs.launch(workbench, "project-1", dataset_id=DATASET, full=True, requested_by="alice",
                              note="after the pattern fix", strategy="llm")
    assert len(workbench.created_jobs) == 1 and again["job_run_id"] == "run-2"
    environment = json.loads(workbench.runs[0].environment)
    assert (environment["HELIOS_CRAWL_FULL"], environment["HELIOS_CRAWL_NOTE"], environment["HELIOS_CRAWL_STRATEGY"]) == (
        "1", "after the pattern fix", "llm")


def test_launches_list_the_jobs_runs_newest_first(workbench):
    assert crawl_jobs.launches(workbench, "project-1") == []
    crawl_jobs.launch(workbench, "project-1", dataset_id=DATASET, full=False, requested_by="alice")
    workbench.runs[0].status = "ENGINE_SUCCEEDED"
    crawl_jobs.launch(workbench, "project-1", dataset_id=DATASET, full=True, requested_by="bob", strategy="llm")
    newest, oldest = crawl_jobs.launches(workbench, "project-1")
    assert (newest["job_run_id"], newest["active"], newest["requested_by"], newest["full"], newest["strategy"]) == (
        "run-2", True, "bob", True, "llm")
    assert (oldest["job_run_id"], oldest["active"], oldest["strategy"]) == ("run-1", False, "deterministic")


def test_the_job_needs_a_runtime_and_a_workbench_project(monkeypatch):
    monkeypatch.delenv("HELIOS_CRAWL_JOB_RUNTIME", raising=False)
    with pytest.raises(crawl_jobs.WorkbenchUnavailable, match="HELIOS_CRAWL_JOB_RUNTIME"):
        crawl_jobs.launch(FakeWorkbench(), "project-1", dataset_id=DATASET, full=False, requested_by="alice")
    monkeypatch.delenv("CDSW_PROJECT_ID", raising=False)
    with pytest.raises(crawl_jobs.WorkbenchUnavailable, match="CDSW_PROJECT_ID"):
        crawl_jobs.workbench()


def test_the_job_crawls_the_dataset_its_run_names(monkeypatch):
    """crawler/job.py, as Workbench runs it: the run's environment becomes the crawl's arguments."""
    import runpy

    from apps.helios.crawler import __main__ as crawler_main

    seen = []
    monkeypatch.setattr(crawler_main, "main", lambda argv: seen.append(argv) or 0)
    monkeypatch.setenv("HELIOS_CRAWL_DATASET", DATASET)
    monkeypatch.setenv("HELIOS_CRAWL_FULL", "1")
    monkeypatch.setenv("HELIOS_CRAWL_STRATEGY", "llm")
    runpy.run_path(str(crawl_jobs.__file__).replace("crawl_jobs.py", "job.py"))
    assert seen == [["crawl", "--dataset", DATASET, "--full", "--strategy", "llm"]]
    monkeypatch.delenv("HELIOS_CRAWL_DATASET")
    with pytest.raises(SystemExit, match="Nothing to crawl"):
        runpy.run_path(str(crawl_jobs.__file__).replace("crawl_jobs.py", "job.py"))
