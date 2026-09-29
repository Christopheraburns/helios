"""Contract tests for the /v1 REST stubs (spec: "REST contract")."""

import pytest
from fastapi.testclient import TestClient

from helios_ds_generation.api import main
from helios_ds_generation.api.v1 import jobs


@pytest.fixture
def client():
    jobs._jobs.clear()
    return TestClient(main.app)


def _create(client, body=None):
    response = client.post("/v1/generations", json=body or {})
    assert response.status_code == 202
    return response.json()


def test_generation_returns_202_with_job_handle(client):
    accepted = _create(client)
    assert accepted["state"] == "QUEUED"
    assert accepted["job_id"].startswith("job_")
    assert accepted["status_uri"] == f"/v1/jobs/{accepted['job_id']}"


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


def test_job_status_and_deterministic_dataset_id(client):
    first = client.get(_create(client, {"master_seed": 7})["status_uri"]).json()
    second = client.get(_create(client, {"master_seed": 7})["status_uri"]).json()
    third = client.get(_create(client, {"master_seed": 8})["status_uri"]).json()
    assert first["job_id"] != second["job_id"]
    assert first["dataset_id"] == second["dataset_id"] != third["dataset_id"]


def test_cancel(client):
    job_id = _create(client)["job_id"]
    response = client.post(f"/v1/jobs/{job_id}:cancel")
    assert response.status_code == 200
    assert response.json()["state"] == "CANCELLED"
    assert client.post(f"/v1/jobs/{job_id}:cancel").status_code == 409


def test_list_jobs_filters_by_state(client):
    kept = _create(client)["job_id"]
    cancelled = _create(client)["job_id"]
    client.post(f"/v1/jobs/{cancelled}:cancel")
    queued = client.get("/v1/jobs", params={"state": "QUEUED"}).json()
    assert [j["job_id"] for j in queued] == [kept]
    assert len(client.get("/v1/jobs").json()) == 2


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
