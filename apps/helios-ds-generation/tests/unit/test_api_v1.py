"""Contract tests for the /v1 REST API (spec: "REST contract").

The generation service runs on the Impala-path SQL sink (DuckDB stand-in) with
a recording dispatcher, or an inline dispatcher that really runs the worker.
"""

import duckdb
import pytest
from fastapi.testclient import TestClient

from helios_ds.jobs import InlineDispatcher, JobStore, run_generation_job
from helios_ds.lakehouse import DUCKDB, SqlLakehouseSink
from helios_ds.object_store import LocalObjectStore
from helios_ds_generation.api import main
from helios_ds_generation.api.v1 import service as service_module
from helios_ds_generation.api.v1.service import GenerationService, get_service


class RecordingDispatcher:
    name = "workbench"

    def __init__(self, fail=False):
        self.dispatched, self.stopped, self.fail = [], [], fail

    def dispatch(self, job_id):
        if self.fail:
            raise RuntimeError("Workbench Job 'helios-ds-generate' not found")
        self.dispatched.append(job_id)
        return f"run-{len(self.dispatched)}"

    def stop(self, run_id):
        self.stopped.append(run_id)


@pytest.fixture
def sink(tmp_path):
    sink = SqlLakehouseSink(lambda: duckdb.connect(str(tmp_path / "lakehouse.duckdb")), DUCKDB)
    sink.ensure_tables()
    return sink


def _client(service):
    main.app.dependency_overrides[get_service] = lambda: service
    return TestClient(main.app)


@pytest.fixture
def dispatcher():
    return RecordingDispatcher()


@pytest.fixture
def client(sink, dispatcher):
    yield _client(GenerationService(JobStore(sink), dispatcher))
    main.app.dependency_overrides.clear()


def _create(client, body=None, headers=None):
    response = client.post("/v1/generations", json=body or {}, headers=headers or {})
    assert response.status_code == 202, response.text
    return response.json()


def test_generation_returns_202_and_starts_a_worker_run(client, dispatcher):
    accepted = _create(client)
    assert accepted["state"] == "QUEUED"
    assert accepted["job_id"].startswith("job_")
    assert accepted["status_uri"] == f"/v1/jobs/{accepted['job_id']}"
    assert dispatcher.dispatched == [accepted["job_id"]]
    job = client.get(accepted["status_uri"]).json()
    assert job["workbench_run_id"] == "run-1"
    assert job["dataset_id"] is None


def test_spec_example_request_is_accepted(client):
    _create(
        client,
        {
            "source": {"catalog": "hive", "database": "tpcds", "scale_factor": 1},
            "master_seed": 42,
            "profile": "developer",
            "artifact_counts": {
                "pdf": 500,
                "image": 300,
                "email": 500,
                "chat": 300,
                "audio": 100,
                "video": 50,
            },
            "security_profile": "departmental",
            "difficulty_profile": "mixed",
        },
    )


def test_idempotency_key_returns_the_existing_job(client, dispatcher):
    headers = {"Idempotency-Key": "abc-123"}
    first = _create(client, {"master_seed": 7}, headers)
    second = _create(client, {"master_seed": 7}, headers)
    assert first["job_id"] == second["job_id"]
    assert dispatcher.dispatched == [first["job_id"]]  # started once
    conflict = client.post("/v1/generations", json={"master_seed": 8}, headers=headers)
    assert conflict.status_code == 409


def test_config_hash_is_deterministic(client):
    first = client.get(_create(client, {"master_seed": 7})["status_uri"]).json()
    second = client.get(_create(client, {"master_seed": 7})["status_uri"]).json()
    third = client.get(_create(client, {"master_seed": 8})["status_uri"]).json()
    assert first["job_id"] != second["job_id"]
    assert first["config_hash"] == second["config_hash"] != third["config_hash"]


def test_dispatch_failure_is_recorded_with_its_cause(sink):
    client = _client(GenerationService(JobStore(sink), RecordingDispatcher(fail=True)))
    try:
        accepted = _create(client)
        job = client.get(accepted["status_uri"]).json()
    finally:
        main.app.dependency_overrides.clear()
    assert accepted["state"] == job["state"] == "FAILED"
    assert "not found" in job["error"]


def test_cancel_stops_the_workbench_run(client, dispatcher):
    job_id = _create(client)["job_id"]
    response = client.post(f"/v1/jobs/{job_id}:cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "CANCELLED"
    assert dispatcher.stopped == ["run-1"]
    assert client.post(f"/v1/jobs/{job_id}:cancel").status_code == 409


def test_list_jobs_filters_by_state_newest_first(client):
    kept = _create(client)["job_id"]
    cancelled = _create(client)["job_id"]
    client.post(f"/v1/jobs/{cancelled}:cancel")
    assert [j["job_id"] for j in client.get("/v1/jobs", params={"state": "QUEUED"}).json()] == [
        kept
    ]
    assert [j["job_id"] for j in client.get("/v1/jobs").json()] == [cancelled, kept]


def test_job_status_survives_an_api_restart(sink, dispatcher):
    job_id = _create(_client(GenerationService(JobStore(sink), dispatcher)))["job_id"]
    main.app.dependency_overrides.clear()
    restarted = _client(GenerationService(JobStore(sink), RecordingDispatcher()))
    try:
        assert restarted.get(f"/v1/jobs/{job_id}").json()["state"] == "QUEUED"
    finally:
        main.app.dependency_overrides.clear()


def test_submitted_job_runs_to_success_through_the_worker(
    sink, tmp_path, small_repo, templates, tiny_config_dict
):
    jobs = JobStore(sink)
    store = LocalObjectStore(str(tmp_path / "objects"))

    def runner(job_id):
        run_generation_job(job_id, jobs, small_repo, sink, store, templates)

    client = _client(GenerationService(jobs, InlineDispatcher(runner, background=False)))
    try:
        accepted = _create(client, {"master_seed": 42, "artifact_counts": {"pdf": 3, "email": 2}})
        job = client.get(accepted["status_uri"]).json()
    finally:
        main.app.dependency_overrides.clear()
    assert job["state"] == "SUCCEEDED"
    assert job["progress_percent"] == 100
    assert job["dataset_id"]
    assert len(sink.read_dataset("helios_ds.datasets", job["dataset_id"])) == 1


def test_unconfigured_service_returns_503(monkeypatch):
    monkeypatch.setattr(service_module, "_service", None)
    monkeypatch.delenv("HELIOS_DS_LAKEHOUSE", raising=False)
    response = TestClient(main.app).get("/v1/jobs")
    assert response.status_code == 503
    assert "HELIOS_DS_LAKEHOUSE" in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {"unknown": 1},
        {"difficulty_profile": "impossible"},
        {"artifact_counts": {"hologram": 3}},
        {"artifact_counts": {"pdf": -1}},
        {"source": {"scale_factor": 0}},
    ],
)
def test_invalid_generation_request_is_422(client, body):
    assert client.post("/v1/generations", json=body).status_code == 422


def test_unknown_resources_are_404(client):
    assert client.get("/v1/jobs/job_missing").status_code == 404
    assert client.post("/v1/jobs/job_missing:cancel").status_code == 404
    assert client.get("/v1/datasets/d1").status_code == 404
    assert client.get("/v1/datasets/d1/manifest").status_code == 404
    assert client.get("/v1/datasets/d1/artifacts").status_code == 404
    assert client.get("/v1/artifacts/a1").status_code == 404
    assert client.get("/v1/evaluations/e1").status_code == 404


def test_unimplemented_actions_are_501(client):
    assert client.post("/v1/datasets/d1:crawl").status_code == 501
    assert client.post("/v1/evaluations").status_code == 501


def test_default_request_round_trips(client):
    default = client.get("/v1/config/default").json()
    _create(client, default)


def test_config_template_lists_profiles(client):
    template = client.get("/v1/config/template").json()
    assert {p["name"] for p in template["difficulty_profiles"]} == {"easy", "mixed", "hard"}
    assert "pdf" in template["artifact_types"]


def test_legacy_api_prefix_is_gone(client):
    assert client.post("/api/jobs/submit", json={}).status_code in (404, 405)


def test_scenario_weights_reach_the_plan_config(client):
    job = client.get(
        _create(client, {"scenarios": {"product_return_damage": 1, "promotion_performance": 0}})[
            "status_uri"
        ]
    ).json()
    default = client.get(_create(client, {})["status_uri"]).json()
    assert job["config_hash"] != default["config_hash"]
    assert job["request"]["scenarios"] == {"product_return_damage": 1, "promotion_performance": 0}
    assert (
        client.post("/v1/generations", json={"scenarios": {"product_return_damage": 0}}).status_code
        == 422
    )
    # The default request carries the default weights explicitly.
    assert client.get("/v1/config/default").json()["scenarios"]["product_return_damage"] == 0.25
