"""O-1: the Helios Graph gateway's ontology endpoints and its startup rebuild.

The Bolt client and the Memgraph process are stubbed, so these exercise routing,
the token gate, request validation and the cache interaction rather than
Memgraph itself.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from apps.helios.graph import gateway
from apps.helios.tests.test_ontology_graph import FakeBolt, sample_graph

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A gateway with a fake Bolt client, a stub process and an isolated cache."""
    monkeypatch.setenv("HELIOS_GRAPH_STORE_DIR", str(tmp_path / "ontology"))
    monkeypatch.setattr(gateway, "_TOKEN", TOKEN)
    monkeypatch.setattr(
        gateway,
        "_memgraph",
        SimpleNamespace(running=True, pid=123, command=[], diagnostic_output=lambda: ""),
    )
    bolt = FakeBolt()
    monkeypatch.setitem(gateway._state, "client", bolt)
    monkeypatch.setitem(gateway._state, "startup_error", None)
    monkeypatch.setitem(gateway._state, "rebuild", None)
    # TestClient without a context manager leaves lifespan unrun, which is what
    # we want: startup is exercised separately below.
    return SimpleNamespace(http=TestClient(gateway.app), bolt=bolt)


def materialise_body(graph, **overrides):
    payload = graph.to_dict()
    body = {"nodes": payload["nodes"], "edges": payload["edges"],
            "content_hash": payload["content_hash"]}
    body.update(overrides)
    return body


# --- token gate -----------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/v1/ontology/versions"),
        ("get", "/v1/ontology/v1/graph"),
        ("get", "/v1/ontology/v1/classes/Item"),
        ("post", "/v1/ontology/v1:activate"),
    ],
)
def test_ontology_endpoints_require_the_service_token(client, method, path):
    assert getattr(client.http, method)(path).status_code == 401


def test_health_and_ready_stay_open_for_workbench_probes(client):
    """Neither may demand a token: the Workbench probes them unauthenticated."""
    assert client.http.get("/health").status_code == 200
    assert client.http.get("/ready").status_code != 401


def test_ready_is_200_only_when_bolt_answers_correctly(client, monkeypatch):
    assert client.http.get("/ready").status_code == 503  # fake returns no rows
    monkeypatch.setitem(gateway._state, "client", FakeBolt({"RETURN 1 AS ok": [{"ok": 1}]}))
    assert client.http.get("/ready").status_code == 200


# --- materialise ----------------------------------------------------------


def test_materialise_loads_caches_and_activates(client):
    graph = sample_graph()
    response = client.http.post(
        f"/v1/ontology/{graph.version}:materialise", json=materialise_body(graph), headers=AUTH
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "created"
    assert body["content_hash"] == graph.content_hash
    assert body["active"] is True

    from apps.helios.graph import store

    assert store.active_pointer() == {
        "version": graph.version,
        "content_hash": graph.content_hash,
        "activated_at": pytest.approx(store.active_pointer()["activated_at"]),
    }
    assert store.load_active().content_hash == graph.content_hash


def test_materialise_without_activate_stages_a_blue_green_swap(client):
    graph = sample_graph()
    client.http.post(
        f"/v1/ontology/{graph.version}:materialise",
        json=materialise_body(graph, activate=False),
        headers=AUTH,
    )
    from apps.helios.graph import store

    assert store.active_pointer() is None
    assert len(store.list_cached()) == 1


def test_materialise_rejects_a_mismatched_content_hash(client):
    graph = sample_graph()
    response = client.http.post(
        f"/v1/ontology/{graph.version}:materialise",
        json=materialise_body(graph, content_hash="0" * 64),
        headers=AUTH,
    )
    assert response.status_code == 422
    assert "content hash mismatch" in response.json()["detail"]


def test_materialise_rejects_an_unknown_node_label(client):
    response = client.http.post(
        "/v1/ontology/v1:materialise",
        json={"nodes": [{"label": "Malicious", "key": "x", "properties": {}}], "edges": []},
        headers=AUTH,
    )
    assert response.status_code == 422
    assert "unknown node label" in response.json()["detail"]


def test_materialise_rejects_a_dangling_edge(client):
    response = client.http.post(
        "/v1/ontology/v1:materialise",
        json={
            "nodes": [{"label": "Class", "key": "Item", "properties": {}}],
            "edges": [
                {
                    "type": "IS_A",
                    "from_label": "Class",
                    "from_key": "Item",
                    "to_label": "Class",
                    "to_key": "Ghost",
                }
            ],
        },
        headers=AUTH,
    )
    assert response.status_code == 422
    assert "references missing node" in response.json()["detail"]


def test_nothing_is_cached_when_the_payload_is_rejected(client):
    client.http.post(
        "/v1/ontology/v1:materialise",
        json={"nodes": [{"label": "Malicious", "key": "x"}], "edges": []},
        headers=AUTH,
    )
    from apps.helios.graph import store

    assert store.list_cached() == []


# --- activate and reads ---------------------------------------------------


def test_activate_rejects_a_version_that_was_never_materialised(client):
    assert client.http.post("/v1/ontology/absent:activate", headers=AUTH).status_code == 404


def test_activate_refuses_an_incomplete_version(client, monkeypatch):
    monkeypatch.setattr(
        gateway.ontology,
        "version_state",
        lambda *_: {"version": "v1", "content_hash": "abc", "complete": False},
    )
    assert client.http.post("/v1/ontology/v1:activate", headers=AUTH).status_code == 409


def test_class_detail_returns_404_for_an_unknown_class(client):
    response = client.http.get("/v1/ontology/v1/classes/Nonexistent", headers=AUTH)
    assert response.status_code == 404
    assert response.json()["detail"] == "class_not_found"


def test_version_graph_passes_filters_through_to_cypher(client):
    response = client.http.get(
        "/v1/ontology/v1/graph", params={"layer": "pack", "kind": "entity"}, headers=AUTH
    )
    assert response.status_code == 200
    assert set(response.json()) >= {"classes", "hierarchy", "relationships", "broken_mappings"}
    filters = [params for _, params in client.bolt.recorded if "layer" in params]
    assert filters and filters[0]["layer"] == "pack" and filters[0]["kind"] == "entity"


def test_version_graph_rejects_an_out_of_range_depth(client):
    response = client.http.get(
        "/v1/ontology/v1/graph", params={"focus": "Item", "depth": 99}, headers=AUTH
    )
    assert response.status_code == 422


def test_reads_report_503_when_memgraph_is_down(client, monkeypatch):
    monkeypatch.setitem(gateway._state, "client", None)
    response = client.http.get("/v1/ontology/versions", headers=AUTH)
    assert response.status_code == 503
    assert response.json()["detail"] == "memgraph_unavailable"


# --- startup rebuild ------------------------------------------------------


def test_rebuild_reloads_the_active_version_after_a_restart(client):
    """The O-1 acceptance path: a restart must restore the active version."""
    from apps.helios.graph import store

    graph = sample_graph()
    store.save(graph)
    store.set_active(graph.version, graph.content_hash)

    fresh = FakeBolt()  # A restarted Memgraph is empty.
    result = gateway._rebuild_active(fresh)

    assert result["status"] == "created"
    assert result["content_hash"] == graph.content_hash
    assert "MERGE (n:Class {version: $version, key: row.key})" in fresh.statements()


def test_rebuild_is_a_noop_when_nothing_has_been_published(client):
    assert gateway._rebuild_active(FakeBolt())["status"] == "nothing_cached"


def test_rebuild_skips_reload_when_memgraph_already_holds_the_version(client):
    """Memgraph's own data directory may have survived; don't reload for nothing."""
    from apps.helios.graph import store

    graph = sample_graph()
    store.save(graph)
    store.set_active(graph.version, graph.content_hash)

    warm = FakeBolt(
        {
            "MATCH (v:OntologyVersion {key: $version})": [
                {"version": graph.version, "content_hash": graph.content_hash, "complete": True}
            ]
        }
    )
    assert gateway._rebuild_active(warm)["status"] == "unchanged"
