"""DS-2: the data-source API (registry in the metadata store, RBAC, validation, test)."""

import types

import pytest
from apps.helios.console import data_sources, ontology
from apps.helios.crawler.connectors.base import ConnectionTest, SourceAsset
from fastapi import FastAPI
from fastapi.testclient import TestClient

BASE = "/api/v1/organizations/acme/data-sources"
CORPUS = {
    "name": "Helios-DS development corpus",
    "connector": "helios_ds",
    "connection_ref": "S3 Object Store",
    "description": "The crawler's development corpus",
    "scope": {"dataset_id": "1ca99f86-e6a0-57fc-8311-cadcac4c8302"},
}


@pytest.fixture
def api(persistent_auth_stack, monkeypatch):
    who = {"subject": "admin"}
    monkeypatch.setattr(
        data_sources,
        "_principal",
        lambda request: types.SimpleNamespace(id=f"cloudera-workbench:{who['subject']}"),
    )
    monkeypatch.setitem(ontology._index, "store", None)  # no helios_index here
    app = FastAPI()
    app.state.metadata_repository = persistent_auth_stack.repository
    app.include_router(data_sources.data_sources_router)
    return TestClient(app), who


def test_types_are_listed_with_scope_schemas(api):
    client, _ = api
    types_ = {t["connector"] for t in client.get("/api/v1/data-source-types").json()}
    assert types_ == {"helios_ds", "object_store", "table_rows"}


def test_admin_creates_lists_updates_and_deletes_a_source(api):
    client, _ = api
    created = client.post(BASE, json=CORPUS)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"].startswith("ds_") and body["crawlable"]
    assert body["crawl"] == {"enabled": True, "settings_version": None, "schedule": "manual"}
    assert body["updated_by"] == "cloudera-workbench:admin"

    listed = {s["id"]: s for s in client.get(BASE).json()}
    assert listed[body["id"]]["scope"]["dataset_id"].startswith("1ca99f86")
    assert listed["shared-warehouse"]["crawlable"] is False  # the models' warehouse source

    changed = client.put(f"{BASE}/{body['id']}", json={**CORPUS, "crawl": {"enabled": False}})
    assert changed.json()["crawl"]["enabled"] is False
    assert client.get(f"{BASE}/{body['id']}").json()["recent_crawls"] == []

    assert client.delete(f"{BASE}/{body['id']}").json() == {"deleted": body["id"]}
    assert client.get(f"{BASE}/{body['id']}").status_code == 404


def test_a_source_used_by_a_model_cannot_be_deleted(api):
    client, _ = api
    assert client.delete(f"{BASE}/shared-warehouse").status_code == 409


def test_invalid_scopes_are_rejected_with_their_problems(api):
    client, _ = api
    bad = client.post(BASE, json={**CORPUS, "connector": "object_store", "scope": {"prefix": "x"}})
    assert bad.status_code == 422
    assert any("bucket" in p for p in bad.json()["detail"]["problems"])
    rows = {
        "name": "t",
        "connector": "table_rows",
        "connection_ref": "S3 Object Store",
        "scope": {"table": "db.t", "key_columns": ["id"], "text_columns": ["body"]},
    }
    assert any("'impala'" in p for p in client.post(BASE, json=rows).json()["detail"]["problems"])


def test_permissions_and_organization_scoping(api):
    client, who = api
    created = client.post(BASE, json=CORPUS).json()
    who["subject"] = "viewer"  # a model viewer has no organization-level data-source rights
    assert client.get(BASE).status_code == 403
    assert client.post(BASE, json=CORPUS).status_code == 403
    who["subject"] = "admin"
    other = f"/api/v1/organizations/other/data-sources/{created['id']}"
    assert client.get(other).status_code == 403  # admin of acme only
    assert client.get("/api/v1/organizations/nope/data-sources").status_code == 404


def test_test_connection_reports_reachability_and_a_sample(api, monkeypatch):
    client, _ = api
    created = client.post(BASE, json=CORPUS).json()

    class Fake:
        def test(self):
            return ConnectionTest(
                True,
                "301 assets in scope; reading works",
                [SourceAsset("a1", created["id"], "application/pdf", {}, "v", 10)],
            )

    monkeypatch.setattr(data_sources, "_connector_for", lambda source: Fake())
    result = client.post(f"{BASE}/{created['id']}:test").json()
    assert result["ok"] and result["sample"][0]["asset_id"] == "a1"

    def broken(source):
        raise RuntimeError("Unable to authenticate with RAZ")

    monkeypatch.setattr(data_sources, "_connector_for", broken)
    failed = client.post(f"{BASE}/{created['id']}:test").json()
    assert failed["ok"] is False and "RAZ" in failed["detail"]
    assert client.post(f"{BASE}/shared-warehouse:test").status_code == 400
