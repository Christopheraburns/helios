"""O-1: the normalised ontology contract, the payload cache, and loading into Memgraph.

Memgraph is not available in the test environment, so the Bolt client is faked.
That is enough to pin the parts that are easy to get wrong -- hash determinism,
idempotency, the label whitelist and the startup rebuild -- and leaves only the
Cypher dialect itself to the deployed Application.
"""

from typing import Any

import pytest

from apps.helios.graph import ontology, store
from helios_core.ontology import GraphEdge, GraphNode, OntologyGraph, OntologyGraphError


class FakeBolt:
    """Records statements and replays canned rows for matched substrings."""

    driver_name = "fake"

    def __init__(self, responses: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.recorded: list[tuple[str, dict[str, Any]]] = []
        self.responses = responses or {}

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.recorded.append((" ".join(cypher.split()), parameters or {}))
        for fragment, rows in self.responses.items():
            if fragment in cypher:
                return rows
        return []

    def statements(self) -> str:
        return "\n".join(statement for statement, _ in self.recorded)

    def close(self) -> None:
        pass


def sample_graph(version: str = "2026.09.30", brand_description: str = "A product brand") -> OntologyGraph:
    return OntologyGraph(
        version=version,
        nodes=(
            GraphNode("Class", "Thing", {"layer": "core", "kind": "concept", "abstract": True}),
            GraphNode("Class", "Item", {"layer": "pack", "kind": "entity", "abstract": False}),
            GraphNode("Class", "Brand", {"layer": "pack", "kind": "entity",
                                         "description": brand_description}),
            GraphNode("Attribute", "Item.brand", {"range": "Brand", "owner": "Item",
                                                  "multivalued": False}),
            GraphNode("OssieElement", "tpcds.item", {"model": "tpcds"}),
        ),
        edges=(
            GraphEdge("IS_A", "Class", "Item", "Class", "Thing"),
            GraphEdge("IS_A", "Class", "Brand", "Class", "Thing"),
            GraphEdge("HAS_ATTRIBUTE", "Class", "Item", "Attribute", "Item.brand"),
            GraphEdge("RANGE", "Attribute", "Item.brand", "Class", "Brand"),
            GraphEdge("MAPS_TO", "OssieElement", "tpcds.item", "Class", "Item",
                      {"identifiers": ["i_item_sk"]}),
        ),
    )


# --- contract -------------------------------------------------------------


def test_content_hash_ignores_node_and_edge_order():
    graph = sample_graph()
    shuffled = OntologyGraph(
        version=graph.version,
        nodes=tuple(reversed(graph.nodes)),
        edges=tuple(reversed(graph.edges)),
    )
    assert shuffled.content_hash == graph.content_hash


def test_content_hash_changes_with_content():
    assert sample_graph().content_hash != sample_graph(brand_description="changed").content_hash


def test_content_hash_is_version_specific():
    assert sample_graph(version="a").content_hash != sample_graph(version="b").content_hash


def test_unknown_node_label_is_rejected():
    with pytest.raises(OntologyGraphError, match="unknown node label"):
        GraphNode("Sneaky", "x")


def test_unknown_edge_type_is_rejected():
    with pytest.raises(OntologyGraphError, match="unknown edge type"):
        GraphEdge("DROP_EVERYTHING", "Class", "a", "Class", "b")


def test_duplicate_nodes_are_rejected():
    with pytest.raises(OntologyGraphError, match="duplicate node"):
        OntologyGraph("v", (GraphNode("Class", "Item"), GraphNode("Class", "Item")), ())


def test_dangling_edge_is_rejected():
    with pytest.raises(OntologyGraphError, match="references missing node"):
        OntologyGraph(
            "v",
            (GraphNode("Class", "Item"),),
            (GraphEdge("IS_A", "Class", "Item", "Class", "Absent"),),
        )


def test_roundtrip_through_dict_preserves_hash():
    graph = sample_graph()
    assert OntologyGraph.from_dict(graph.to_dict()).content_hash == graph.content_hash


def test_from_dict_rejects_a_tampered_payload():
    payload = sample_graph().to_dict()
    payload["nodes"][0]["properties"]["layer"] = "customer"
    with pytest.raises(OntologyGraphError, match="content hash mismatch"):
        OntologyGraph.from_dict(payload)


# --- payload cache --------------------------------------------------------


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HELIOS_GRAPH_STORE_DIR", str(tmp_path / "ontology"))
    return tmp_path


def test_save_then_load_active_roundtrips(cache):
    graph = sample_graph()
    store.save(graph)
    store.set_active(graph.version, graph.content_hash)

    loaded = store.load_active()
    assert loaded is not None
    assert loaded.content_hash == graph.content_hash
    assert len(loaded.nodes) == len(graph.nodes)


def test_load_active_is_none_before_anything_is_published(cache):
    assert store.load_active() is None


def test_activating_an_unsaved_version_fails(cache):
    with pytest.raises(FileNotFoundError):
        store.set_active("never-saved", "0" * 64)


def test_saving_twice_is_idempotent(cache):
    graph = sample_graph()
    assert store.save(graph) == store.save(graph)
    assert len(store.list_cached()) == 1


def test_list_cached_marks_the_active_version(cache):
    first = sample_graph(version="v1")
    second = sample_graph(version="v2")
    store.save(first)
    store.save(second)
    store.set_active(second.version, second.content_hash)

    by_version = {entry["version"]: entry for entry in store.list_cached()}
    assert by_version["v2"]["active"] is True
    assert by_version["v1"]["active"] is False


# --- loading into Memgraph ------------------------------------------------


def test_materialise_creates_a_version_and_marks_it_complete():
    client = FakeBolt()
    result = ontology.materialise(client, sample_graph())

    assert result["status"] == "created"
    assert result["nodes"] == 5 and result["edges"] == 5
    statements = client.statements()
    assert "MERGE (v:OntologyVersion {key: $version})" in statements
    assert "SET v.complete = true" in statements
    assert "MERGE (n:Class {version: $version, key: row.key})" in statements
    assert f"MERGE (a)-[r:IS_A]->(b)" in statements
    assert f"MERGE (n)-[:{ontology.IN_VERSION}]->(v)" in statements


def test_materialise_is_a_noop_when_the_hash_already_matches():
    graph = sample_graph()
    client = FakeBolt(
        {
            "MATCH (v:OntologyVersion {key: $version})": [
                {"version": graph.version, "content_hash": graph.content_hash, "complete": True}
            ]
        }
    )
    result = ontology.materialise(client, graph)

    assert result["status"] == "unchanged"
    assert "DETACH DELETE" not in client.statements()
    assert "MERGE (n:Class" not in client.statements()


def test_force_reloads_even_when_unchanged():
    graph = sample_graph()
    client = FakeBolt(
        {
            "MATCH (v:OntologyVersion {key: $version})": [
                {"version": graph.version, "content_hash": graph.content_hash, "complete": True}
            ]
        }
    )
    assert ontology.materialise(client, graph, force=True)["status"] == "replaced"
    assert "DETACH DELETE" in client.statements()


def test_republishing_a_changed_version_drops_the_old_subgraph():
    client = FakeBolt(
        {
            "MATCH (v:OntologyVersion {key: $version})": [
                {"version": "2026.09.30", "content_hash": "stale", "complete": True}
            ]
        }
    )
    result = ontology.materialise(client, sample_graph())

    assert result["status"] == "replaced"
    assert "DETACH DELETE" in client.statements()


def test_incomplete_version_is_reloaded_rather_than_trusted():
    graph = sample_graph()
    client = FakeBolt(
        {
            "MATCH (v:OntologyVersion {key: $version})": [
                {"version": graph.version, "content_hash": graph.content_hash, "complete": False}
            ]
        }
    )
    assert ontology.materialise(client, graph)["status"] == "replaced"


def test_cypher_builders_refuse_labels_outside_the_whitelist():
    with pytest.raises(OntologyGraphError):
        ontology._checked_label("Class) DETACH DELETE (n")
    with pytest.raises(OntologyGraphError):
        ontology._checked_type("IS_A]->() DELETE n //")


def test_neighbourhood_rejects_an_out_of_range_depth():
    client = FakeBolt()
    with pytest.raises(ValueError, match="depth must be between"):
        ontology.neighbourhood(client, "v", "Item", depth=99)
    with pytest.raises(ValueError):
        ontology.neighbourhood(client, "v", "Item", depth=0)


def test_every_parameter_stays_bound_not_interpolated():
    """Version and key values must never be spliced into the statement text."""
    client = FakeBolt()
    ontology.materialise(client, sample_graph(version="'; DROP DATABASE; --"))
    for statement, parameters in client.recorded:
        assert "DROP DATABASE" not in statement
        if "$version" in statement:
            assert parameters.get("version") == "'; DROP DATABASE; --"
