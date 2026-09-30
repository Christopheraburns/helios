"""R-03: review API (ADR 0001 stage B)."""

import duckdb
import pytest
from fastapi.testclient import TestClient

from helios_ds.jobs import JobStore
from helios_ds.lakehouse import DUCKDB, SqlLakehouseSink
from helios_ds.object_store import LocalObjectStore
from helios_ds.pipeline import plan_and_publish
from helios_ds_generation.api import main
from helios_ds_generation.api.v1.service import GenerationService, get_service

ALICE = {"Remote-User": "alice"}


class NoDispatch:
    name = "inline"

    def dispatch(self, job_id):
        return None

    def stop(self, run_id):
        pass


@pytest.fixture
def review(tmp_path, tiny_config, small_repo, templates, monkeypatch):
    monkeypatch.delenv("HELIOS_DS_DEV_USER", raising=False)
    sink = SqlLakehouseSink(lambda: duckdb.connect(str(tmp_path / "lakehouse.duckdb")), DUCKDB)
    sink.ensure_tables()
    store = LocalObjectStore(str(tmp_path / "objects"))
    result = plan_and_publish(tiny_config, small_repo, templates, sink, store)
    main.app.dependency_overrides[get_service] = lambda: GenerationService(
        JobStore(sink), NoDispatch()
    )
    yield TestClient(main.app), result
    main.app.dependency_overrides.clear()


def test_whoami_reports_the_identity_header(review, monkeypatch):
    client, _ = review
    anonymous = client.get("/v1/whoami").json()
    assert anonymous["user"] is None and anonymous["identity_header"] is None
    me = client.get("/v1/whoami", headers=ALICE).json()
    assert me == {**me, "user": "alice", "identity_header": "remote-user"}
    assert "remote-user" in me["headers_seen"]
    monkeypatch.setenv("HELIOS_DS_DEV_USER", "dev")
    assert client.get("/v1/whoami").json()["identity_header"] == "HELIOS_DS_DEV_USER"


def test_dataset_list_counts_rendered_artifacts(review):
    client, result = review
    (listed,) = client.get("/v1/datasets").json()
    single = client.get(f"/v1/datasets/{result.dataset_id}").json()
    assert listed["rendered_artifact_count"] == single["rendered_artifact_count"] == 9
    assert (
        listed["rendered_by_type"]
        == single["rendered_by_type"]
        == {
            "chat": 3,
            "email": 3,
            "pdf": 3,
        }
    )


def test_datasets_can_be_filtered_by_state(review):
    client, result = review
    assert [
        d["dataset_id"] for d in client.get("/v1/datasets", params={"state": "IN_REVIEW"}).json()
    ] == [result.dataset_id]
    assert client.get("/v1/datasets", params={"state": "READY"}).json() == []


def test_dataset_review_summary(review):
    client, result = review
    summary = client.get(f"/v1/review/datasets/{result.dataset_id}").json()
    assert summary["counts"] == {
        "total": 9,
        "unreviewed": 9,
        "accepted": 0,
        "flagged": 0,
        "comments": 0,
    }
    assert {i["artifact_type"] for i in summary["items"]} == {"pdf", "email", "chat"}
    assert all(i["headline"] and i["status"] == "UNREVIEWED" for i in summary["items"])
    assert summary["dataset"]["state"] == "IN_REVIEW"


def test_review_bundle_has_preview_story_and_ground_truth(review):
    client, result = review
    items = client.get(f"/v1/review/datasets/{result.dataset_id}").json()["items"]
    for item in items:
        bundle = client.get(f"/v1/review/artifacts/{item['artifact_id']}").json()
        assert bundle["preview"]["kind"] == item["artifact_type"]
        assert bundle["scenario"]["facts"]["i_item_id"]
        assert bundle["mentions"], item
        assert all(m["canonical_name"] and m["surface_form"] for m in bundle["mentions"])
        assert any(
            e["claim_type"] == "PACKAGING_DAMAGED" and e["statement"] for e in bundle["evidence"]
        )
        predicates = {(r["predicate"], r["from_this_artifact"]) for r in bundle["relationships"]}
        assert ("REFERS_TO_SALE", False) in predicates
        assert ("MENTIONS", True) in predicates and ("DISCUSSES", True) in predicates
        assert bundle["status"] == "UNREVIEWED" and bundle["marks"] == []


def test_marks_need_identity_and_the_latest_status_wins(review):
    client, result = review
    artifact_id = client.get(f"/v1/review/datasets/{result.dataset_id}").json()["items"][0][
        "artifact_id"
    ]
    url = f"/v1/review/artifacts/{artifact_id}/marks"

    assert client.post(url, json={"status": "ACCEPTED"}).status_code == 401
    assert client.post(url, json={"status": "FLAGGED"}, headers=ALICE).status_code == 422
    assert client.post(url, json={"status": "BOGUS"}, headers=ALICE).status_code == 422

    accepted = client.post(url, json={"status": "accepted"}, headers=ALICE)
    assert accepted.status_code == 201 and accepted.json()["reviewer"] == "alice"
    flagged = client.post(
        url, json={"status": "FLAGGED", "comment": "Tone reads oddly"}, headers=ALICE
    )
    assert flagged.status_code == 201
    client.post(
        url, json={"status": "COMMENT", "comment": "Otherwise fine"}, headers={"Remote-User": "bob"}
    )

    bundle = client.get(f"/v1/review/artifacts/{artifact_id}").json()
    assert bundle["status"] == "FLAGGED"  # the COMMENT did not change it
    assert [m["status"] for m in bundle["marks"]] == ["ACCEPTED", "FLAGGED", "COMMENT"]
    assert [m["reviewer"] for m in bundle["marks"]] == ["alice", "alice", "bob"]

    counts = client.get(f"/v1/review/datasets/{result.dataset_id}").json()["counts"]
    assert counts == {"total": 9, "unreviewed": 8, "accepted": 0, "flagged": 1, "comments": 2}
    assert (
        client.post(
            "/v1/review/artifacts/nope/marks", json={"status": "ACCEPTED"}, headers=ALICE
        ).status_code
        == 404
    )


# --- R-06 / R-07 -----------------------------------------------------------------


def test_approval_needs_an_identity_and_records_the_approver(review):
    client, result = review
    url = f"/v1/review/datasets/{result.dataset_id}:approve"
    assert client.post(url, json={}).status_code == 401
    response = client.post(url, json={"note": "  looks right "}, headers=ALICE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dataset"]["state"] == "READY" and body["superseded"] == []
    ready = body["dataset"]["lifecycle"][-1]
    assert (ready["actor"], ready["reason"]) == ("alice", "looks right")
    assert client.post(url, json={}, headers=ALICE).status_code == 409
    artifact_id = client.get(f"/v1/review/datasets/{result.dataset_id}").json()["items"][0][
        "artifact_id"
    ]
    closed = client.post(
        f"/v1/review/artifacts/{artifact_id}/marks", json={"status": "ACCEPTED"}, headers=ALICE
    )
    assert closed.status_code == 409


def test_flags_do_not_block_approval_but_failed_validation_does(review, tmp_path):
    client, result = review
    items = client.get(f"/v1/review/datasets/{result.dataset_id}").json()["items"]
    client.post(
        f"/v1/review/artifacts/{items[0]['artifact_id']}/marks",
        json={"status": "FLAGGED", "comment": "typo"},
        headers=ALICE,
    )
    from helios_ds.manifests import manifest_key

    path = tmp_path / "objects" / manifest_key(result.dataset_id)
    original = path.read_bytes()
    path.chmod(0o644)
    path.write_bytes(b"{}")
    url = f"/v1/review/datasets/{result.dataset_id}:approve"
    blocked = client.post(url, json={}, headers=ALICE)
    assert blocked.status_code == 409
    assert any("manifest" in p for p in blocked.json()["detail"]["problems"])
    path.write_bytes(original)
    assert client.post(url, json={}, headers=ALICE).json()["dataset"]["state"] == "READY"


def test_reject_needs_a_reason(review):
    client, result = review
    url = f"/v1/review/datasets/{result.dataset_id}:reject"
    assert client.post(url, json={"note": "x"}).status_code == 401
    assert client.post(url, json={"note": " "}, headers=ALICE).status_code == 422
    response = client.post(url, json={"note": "email tone too harsh"}, headers=ALICE)
    assert response.json()["dataset"]["state"] == "REJECTED"
    assert client.post(url, json={"note": "again"}, headers=ALICE).status_code == 409
    assert client.get("/v1/datasets", params={"state": "IN_REVIEW"}).json() == []


def test_history_endpoint(review):
    client, result = review
    items = client.get(f"/v1/review/datasets/{result.dataset_id}").json()["items"]
    client.post(
        f"/v1/review/artifacts/{items[0]['artifact_id']}/marks",
        json={"status": "ACCEPTED"},
        headers=ALICE,
    )
    client.post(f"/v1/review/datasets/{result.dataset_id}:approve", json={}, headers=ALICE)
    history = client.get(f"/v1/review/datasets/{result.dataset_id}/history").json()
    assert [e["action"] for e in history if e["kind"] == "lifecycle"][-1] == "READY"
    assert [(e["action"], e["actor"]) for e in history if e["kind"] == "review"] == [
        ("ACCEPTED", "alice")
    ]
    ready = next(e for e in history if e["action"] == "READY")
    assert ready["review_counts"]["accepted"] == 1
    assert client.get("/v1/review/datasets/nope/history").status_code == 404


def test_delete_dataset_endpoint(review):
    client, result = review
    url = f"/v1/datasets/{result.dataset_id}:delete"
    assert client.post(url, json={}).status_code == 401
    response = client.post(url, json={"reason": "old run"}, headers=ALICE)
    assert response.status_code == 200, response.text
    assert response.json()["objects_deleted"] > 0
    assert client.get("/v1/datasets").json() == []
    assert client.get(f"/v1/datasets/{result.dataset_id}").status_code == 404
    assert client.post(url, json={}, headers=ALICE).status_code == 404
