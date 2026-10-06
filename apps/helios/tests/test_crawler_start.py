"""Crawls start as runs of the Workbench Job, through the API only."""

import json
from types import SimpleNamespace

import pytest
from apps.helios.console import crawl_jobs
from helios_core.index import runs
from helios_core.index.store import duckdb_index_store

DATASET = "1ca99f86-e6a0-57fc-8311-cadcac4c8302"
ALICE = {"x-forwarded-user": "alice"}


class FakeWorkbench:
    """Enough of the Cloudera AI API client: jobs, applications and job runs."""

    def __init__(self, jobs=()):
        self.jobs = list(jobs)
        self.runs = []
        self.created_jobs = []

    def list_jobs(self, project_id, page_size=None):
        return SimpleNamespace(jobs=self.jobs)

    def list_applications(self, project_id, page_size=None):
        api = SimpleNamespace(script=crawl_jobs.API_SCRIPT, runtime_identifier="runtime:1")
        return SimpleNamespace(applications=[api])

    def create_job(self, body, project_id):
        job = SimpleNamespace(id="job-1", name=body["name"])
        self.created_jobs.append(body)
        self.jobs.append(job)
        return job

    def create_job_run(self, body, project_id, job_id):
        run = SimpleNamespace(
            id=f"run-{len(self.runs) + 1}",
            status="ENGINE_SCHEDULING",
            created_at="2026-10-05T16:00:00Z",
            finished_at=None,
            environment=json.dumps(body["environment"]),
        )
        self.runs.insert(0, run)
        return run

    def list_job_runs(self, project_id, job_id, page_size=None, sort=None):
        return SimpleNamespace(job_runs=self.runs[:page_size])


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


@pytest.fixture
def workbench(monkeypatch):
    fake = FakeWorkbench()
    monkeypatch.setattr(crawl_jobs, "workbench", lambda: (fake, "project-1"))
    return fake


@pytest.fixture
def client(index, monkeypatch):
    from apps.helios.console import crawler, ontology
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setitem(ontology._index, "store", index)
    monkeypatch.setattr(ontology, "index_store", lambda: index)
    app = FastAPI()
    app.state.metadata_repository = SimpleNamespace(
        data_source=lambda source_id: None,
        grants_for_principal=lambda principal_id: [],
        data_sources_for_organization=lambda organization_id: [],
    )
    app.include_router(crawler.crawler_router)
    return TestClient(app)


def test_starting_a_crawl_creates_the_job_once_and_runs_it(client, workbench):
    response = client.post("/api/v1/crawler/runs", json={"dataset_id": DATASET}, headers=ALICE)

    assert response.status_code == 202
    body = response.json()
    assert body["job_run_id"] == "run-1" and body["active"] is True
    assert body["dataset_id"] == DATASET and body["requested_by"].endswith("alice")
    [job] = workbench.created_jobs
    assert (job["name"], job["script"], job["runtime_identifier"]) == ("helios-crawl", "helios/jobs/crawl.py", "runtime:1")
    environment = json.loads(workbench.runs[0].environment)
    assert environment["HELIOS_CRAWL_DATASET"] == DATASET
    assert environment["HELIOS_CRAWL_FULL"] == "0"
    assert "HELIOS_CRAWL_SOURCE" not in environment
    assert "HELIOS_CRAWL_NOTE" not in environment and body["note"] is None
    assert "HELIOS_CRAWL_STRATEGY" not in environment and body["strategy"] == "deterministic"

    workbench.runs[0].status = "ENGINE_SUCCEEDED"
    again = client.post(
        "/api/v1/crawler/runs",
        json={"dataset_id": DATASET, "full": True, "note": "  after the pattern fix ", "strategy": "llm"},
        headers=ALICE,
    )
    assert again.status_code == 202 and len(workbench.created_jobs) == 1
    assert json.loads(workbench.runs[0].environment)["HELIOS_CRAWL_FULL"] == "1"
    assert json.loads(workbench.runs[0].environment)["HELIOS_CRAWL_NOTE"] == "after the pattern fix"
    assert again.json()["note"] == "after the pattern fix"
    assert json.loads(workbench.runs[0].environment)["HELIOS_CRAWL_STRATEGY"] == "llm"
    assert again.json()["strategy"] == "llm"


def test_a_second_crawl_of_the_same_target_waits_for_the_first(client, workbench):
    assert client.post("/api/v1/crawler/runs", json={"dataset_id": DATASET}, headers=ALICE).status_code == 202
    response = client.post("/api/v1/crawler/runs", json={"dataset_id": DATASET}, headers=ALICE)
    assert response.status_code == 409 and "run-1" in response.json()["detail"]
    assert len(workbench.runs) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"dataset_id": DATASET, "source_id": "s"},
        {"dataset_id": "x; rm -rf /"},
        {"dataset_id": "a" * 200},
        {"dataset_id": DATASET, "note": "n" * 201},
        {"dataset_id": DATASET, "strategy": "hybrid"},
    ],
)
def test_bad_requests_start_nothing(client, workbench, body):
    assert client.post("/api/v1/crawler/runs", json=body, headers=ALICE).status_code == 422
    assert workbench.runs == []


def test_starting_needs_a_principal_a_known_source_and_workbench(client, workbench, monkeypatch):
    assert client.post("/api/v1/crawler/runs", json={"dataset_id": DATASET}).status_code == 401
    assert client.post("/api/v1/crawler/runs", json={"source_id": "nope"}, headers=ALICE).status_code == 404

    def unavailable():
        raise crawl_jobs.WorkbenchUnavailable("not running in a Workbench project")

    monkeypatch.setattr(crawl_jobs, "workbench", unavailable)
    response = client.post("/api/v1/crawler/runs", json={"dataset_id": DATASET}, headers=ALICE)
    assert response.status_code == 503 and "Workbench project" in response.json()["detail"]
    assert client.get("/api/v1/crawler/launches", headers=ALICE).json()["available"] is False


def test_launches_and_targets_describe_what_was_and_can_be_crawled(client, workbench, index):
    assert client.get("/api/v1/crawler/launches", headers=ALICE).json()["launches"] == []
    client.post("/api/v1/crawler/runs", json={"dataset_id": DATASET}, headers=ALICE)
    [launch] = client.get("/api/v1/crawler/launches", headers=ALICE).json()["launches"]
    assert (launch["dataset_id"], launch["status"], launch["active"]) == (DATASET, "ENGINE_SCHEDULING", True)

    run = runs.start(
        index, connector="helios_ds", source=DATASET, ontology_version="0.2.0",
        crawler_version="0.6.0", actor="cburns",
        settings={"data_source": {"organization_id": "unregistered"}, "request": {"requested_by": "cloudera-workbench:alice", "note": "baseline"}},
    )
    runs.finish(index, run, {"listed": 1})
    assert client.get("/api/v1/crawler/targets", headers=ALICE).json() == [
        {"kind": "dataset", "id": DATASET, "label": f"Helios-DS dataset {DATASET}", "connector": "helios_ds"}
    ]
    [listed] = client.get("/api/v1/crawler/runs").json()
    assert listed["requested_by"] == "cloudera-workbench:alice"
    assert listed["note"] == "baseline"
    assert listed["strategy"] == "deterministic" and listed["llm"] is None
    assert listed["isolated"] is False  # ran as cburns, not the crawler machine user
