"""CR-0c: ontology versions recorded in helios_index; the disk cache is rebuilt from it."""

import itertools
from pathlib import Path

import pytest
import yaml
from helios_core.index import TABLES, ddl_statements
from helios_core.index import ontology_versions as versions
from helios_core.index.store import duckdb_index_store
from helios_core.ontology.mapping import load_mappings
from helios_core.ontology.parser import parse

REPO_ROOT = Path(__file__).resolve().parents[3]
EXTENSION = REPO_ROOT / "ontology/customers/example-tenant/extension.yaml"
OSSIE = REPO_ROOT / "models/published/tpcds.ossie.yaml"


@pytest.fixture
def index():
    store = duckdb_index_store()
    store.ensure_tables()
    return store


@pytest.fixture(scope="module")
def graph():
    mappings = load_mappings(REPO_ROOT / "ontology/mappings/ossie")
    model = yaml.safe_load(OSSIE.read_text())
    return parse(str(EXTENSION), version="0.2.0", mappings=mappings, ossie_model=model).graph


def clock():
    ticks = itertools.count()
    return lambda: f"2026-10-01T00:00:{next(ticks):02d}+00:00"


def test_impala_ddl_creates_iceberg_tables_in_helios_index():
    statements = ddl_statements()
    assert statements[0] == "CREATE DATABASE IF NOT EXISTS helios_index"
    for table in TABLES:
        create = next(s for s in statements if f"TABLE IF NOT EXISTS {table} " in s)
        assert "STORED AS ICEBERG" in create


def test_publish_is_idempotent_and_round_trips_the_graph(index, graph):
    record, created = versions.publish(index, graph, "ontology/x.yaml", "alice", clock())
    assert created and record.content_hash == graph.content_hash
    again, created_again = versions.publish(index, graph, "ontology/x.yaml", "bob", clock())
    assert not created_again and again.published_by == "alice"
    assert len(versions.versions(index)) == 1
    restored = versions.graph_of(versions.versions(index)[0])
    assert restored.content_hash == graph.content_hash
    assert restored.canonical_json() == graph.canonical_json()


def test_a_version_cannot_be_republished_with_different_content(index, graph):
    versions.publish(index, graph, "ontology/x.yaml", "alice")
    other = parse(str(EXTENSION), version="0.2.0").graph  # no mappings: different content
    with pytest.raises(versions.VersionConflict, match="new version"):
        versions.publish(index, other, "ontology/x.yaml", "alice")


def test_the_latest_activation_is_active(index, graph):
    tick = clock()
    assert versions.active(index) is None
    with pytest.raises(versions.UnknownVersion):
        versions.activate(index, "9.9.9", "alice", tick)
    versions.publish(index, graph, "ontology/x.yaml", "alice", tick)
    first = versions.activate(index, "0.2.0", "alice", tick)
    second = versions.activate(index, "0.2.0", "bob", tick)
    assert versions.active(index) == second and first.content_hash == graph.content_hash


def test_missing_from_cache(index, graph):
    versions.publish(index, graph, "ontology/x.yaml", "alice")
    assert [r.version for r in versions.missing_from_cache(index, set())] == ["0.2.0"]
    assert versions.missing_from_cache(index, {("0.2.0", graph.content_hash)}) == []


def test_api_restores_the_disk_cache_from_the_lakehouse(index, graph, tmp_path, monkeypatch):
    from apps.helios.console import ontology as api
    from apps.helios.graph import store

    monkeypatch.setenv("HELIOS_GRAPH_STORE_DIR", str(tmp_path / "cache"))
    monkeypatch.setitem(api._index, "store", index)
    versions.publish(index, graph, "ontology/x.yaml", "alice")
    versions.activate(index, "0.2.0", "alice")
    assert store.list_cached() == []
    assert api.restore_cache_from_lakehouse() == 1
    assert store.active_pointer()["content_hash"] == graph.content_hash
    assert store.load_active().content_hash == graph.content_hash
    assert api.restore_cache_from_lakehouse() == 0


@pytest.fixture
def client(index, tmp_path, monkeypatch):
    from apps.helios.console import ontology as api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HELIOS_GRAPH_STORE_DIR", str(tmp_path / "cache"))
    monkeypatch.setitem(api._index, "store", index)
    # Identity comes from the API's principal_from_request; stubbed here so the test
    # doesn't import the whole API (and its MCP client).
    monkeypatch.setattr(api, "_actor", lambda request: "cloudera-workbench:alice")
    monkeypatch.setattr(
        api, "require_ontology_edit", lambda request, org: "cloudera-workbench:alice"
    )
    app = FastAPI()
    app.include_router(api.ontology_router)
    return TestClient(app)


SCHEMA = "ontology/customers/example-tenant/extension.yaml"


def test_publish_records_in_the_lakehouse_and_refuses_changed_content(client, index):
    body = {"version": "0.2.0", "schema_path": SCHEMA}
    first = client.post("/api/v1/ontology:publish", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["recorded_in_lakehouse"] and first.json()["broken_mappings"] == []
    assert client.post("/api/v1/ontology:publish", json=body).status_code == 200  # same content
    [record] = versions.versions(index)
    assert record.schema_path == SCHEMA and record.published_by.endswith("alice")
    changed = {"version": "0.2.0", "schema_path": "ontology/core/core.yaml"}
    conflict = client.post("/api/v1/ontology:publish", json=changed)
    assert conflict.status_code == 409 and "new version" in conflict.json()["detail"]


def test_activate_records_and_points_the_cache(client, index):
    from apps.helios.graph import store

    assert client.post("/api/v1/ontology/0.2.0:activate").status_code == 404
    client.post("/api/v1/ontology:publish", json={"version": "0.2.0", "schema_path": SCHEMA})
    response = client.post("/api/v1/ontology/0.2.0:activate")
    assert response.status_code == 200, response.text
    assert versions.active(index).version == "0.2.0"
    assert store.active_pointer()["version"] == "0.2.0"


# --- O-6: check, schemas, versions, permission ------------------------------------------


def test_schemas_lists_publishable_roots_most_complete_first(client):
    schemas = client.get("/api/v1/ontology/schemas").json()
    assert [s["layer"] for s in schemas] == ["customer", "pack", "core"]
    assert schemas[0]["schema_path"] == SCHEMA
    assert schemas[1]["version"] == "0.2.0"


def test_check_reports_status_and_changes_without_writing(client, index):
    body = {"version": "0.2.0", "schema_path": SCHEMA}
    first = client.post("/api/v1/ontology:check", json=body).json()
    assert first["status"] == "new" and first["broken_mappings"] == []
    assert first["changes"] is None  # nothing active yet
    assert versions.versions(index) == []  # a check writes nothing

    client.post("/api/v1/ontology:publish", json=body)
    client.post("/api/v1/ontology/0.2.0:activate")
    assert client.post("/api/v1/ontology:check", json=body).json()["status"] == "identical"

    core = client.post(
        "/api/v1/ontology:check",
        json={"version": "0.2.0", "schema_path": "ontology/core/core.yaml"},
    ).json()
    assert core["status"] == "conflict"
    assert "Customer" in core["changes"]["classes_removed"]
    assert core["changes"]["mappings_removed"]

    bad = client.post(
        "/api/v1/ontology:check", json={"version": "1", "schema_path": "../etc/passwd"}
    )
    assert bad.status_code == 400


def test_versions_list_the_lakehouse_record_and_flag_cache_only_versions(client, index, graph):
    from apps.helios.graph import store

    legacy = parse(str(EXTENSION), version="0.1.0").graph
    store.save(legacy)  # published to the cache before CR-0c
    client.post("/api/v1/ontology:publish", json={"version": "0.2.0", "schema_path": SCHEMA})
    client.post("/api/v1/ontology/0.2.0:activate")
    listed = {v["version"]: v for v in client.get("/api/v1/ontology/versions").json()}
    assert listed["0.2.0"]["in_lakehouse"] and listed["0.2.0"]["is_active"]
    assert listed["0.2.0"]["published_by"] == "cloudera-workbench:alice"
    assert listed["0.1.0"]["in_lakehouse"] is False and not listed["0.1.0"]["is_active"]
    graph_payload = client.get("/api/v1/ontology/0.2.0/graph").json()
    assert graph_payload["version"] == "0.2.0"


def test_a_version_with_several_cached_contents_is_refused(client):
    from apps.helios.graph import store

    store.save(parse(str(EXTENSION), version="0.1.0").graph)
    store.save(parse(str(REPO_ROOT / "ontology/core/core.yaml"), version="0.1.0").graph)
    response = client.get("/api/v1/ontology/0.1.0/graph")
    assert response.status_code == 409 and "new version" in response.json()["detail"]


def test_require_ontology_edit_checks_identity_organization_and_role(monkeypatch):
    import sys
    import types

    from apps.helios.console import ontology as api
    from fastapi import HTTPException
    from helios_core import authz

    principal = {"value": None}
    fake_api = types.ModuleType("apps.helios.console.api")
    fake_api.principal_from_request = lambda request: principal["value"]
    monkeypatch.setitem(sys.modules, "apps.helios.console.api", fake_api)

    class Decision:
        def __init__(self, allowed):
            self.allowed, self.reason = allowed, "no role granting ontology.edit"

    class FakePolicy:
        def __init__(self, allowed):
            self.allowed = allowed

        def can(self, who, action, resource):
            assert action == authz.Action.ONTOLOGY_EDIT and resource.organization_id == "org1"
            return Decision(self.allowed)

    request = types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace()))

    def status(org):
        try:
            return api.require_ontology_edit(request, org)
        except HTTPException as exc:
            return exc.status_code

    assert status("org1") == 401
    principal["value"] = types.SimpleNamespace(id="cloudera-workbench:alice")
    assert status(None) == 400
    request.app.state.authorization_policy = FakePolicy(False)
    assert status("org1") == 403
    request.app.state.authorization_policy = FakePolicy(True)
    assert status("org1") == "cloudera-workbench:alice"
