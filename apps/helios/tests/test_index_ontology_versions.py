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
