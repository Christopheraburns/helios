"""The normalised ontology graph (helios_cortex.ontology.OntologyGraph): its content hash
is order-independent and covers the version and every property, only known labels and
edge types are accepted, nodes are unique and edges reach them, and a payload round-trips
through a dict but is refused if its content no longer matches its hash."""
import pytest

from helios_cortex.ontology import GraphEdge, GraphNode, OntologyGraph, OntologyGraphError


def sample_graph(version: str = "2026.09.30", brand_description: str = "A product brand") -> OntologyGraph:
    return OntologyGraph(
        version=version,
        nodes=(
            GraphNode("Class", "Thing", {"layer": "core", "kind": "concept", "abstract": True}),
            GraphNode("Class", "Item", {"layer": "pack", "kind": "entity", "abstract": False}),
            GraphNode("Class", "Brand", {"layer": "pack", "kind": "entity", "description": brand_description}),
            GraphNode("Attribute", "Item.brand", {"range": "Brand", "owner": "Item", "multivalued": False}),
            GraphNode("OssieElement", "tpcds.item", {"model": "tpcds"}),
        ),
        edges=(
            GraphEdge("IS_A", "Class", "Item", "Class", "Thing"),
            GraphEdge("IS_A", "Class", "Brand", "Class", "Thing"),
            GraphEdge("HAS_ATTRIBUTE", "Class", "Item", "Attribute", "Item.brand"),
            GraphEdge("RANGE", "Attribute", "Item.brand", "Class", "Brand"),
            GraphEdge("MAPS_TO", "OssieElement", "tpcds.item", "Class", "Item", {"identifiers": ["i_item_sk"]}),
        ),
    )


def test_content_hash_ignores_node_and_edge_order():
    graph = sample_graph()
    shuffled = OntologyGraph(version=graph.version, nodes=tuple(reversed(graph.nodes)),
                             edges=tuple(reversed(graph.edges)))
    assert shuffled.content_hash == graph.content_hash


def test_content_hash_changes_with_content_and_version():
    assert sample_graph().content_hash != sample_graph(brand_description="changed").content_hash
    assert sample_graph(version="a").content_hash != sample_graph(version="b").content_hash


def test_unknown_labels_and_edge_types_are_rejected():
    with pytest.raises(OntologyGraphError, match="unknown node label"):
        GraphNode("Sneaky", "x")
    with pytest.raises(OntologyGraphError, match="unknown edge type"):
        GraphEdge("DROP_EVERYTHING", "Class", "a", "Class", "b")


def test_duplicate_nodes_and_dangling_edges_are_rejected():
    with pytest.raises(OntologyGraphError, match="duplicate node"):
        OntologyGraph("v", (GraphNode("Class", "Item"), GraphNode("Class", "Item")), ())
    with pytest.raises(OntologyGraphError, match="references missing node"):
        OntologyGraph("v", (GraphNode("Class", "Item"),), (GraphEdge("IS_A", "Class", "Item", "Class", "Absent"),))


def test_a_dict_round_trip_keeps_the_hash_and_a_tampered_payload_is_refused():
    graph = sample_graph()
    assert OntologyGraph.from_dict(graph.to_dict()).content_hash == graph.content_hash
    payload = graph.to_dict()
    payload["nodes"][0]["properties"]["layer"] = "customer"
    with pytest.raises(OntologyGraphError, match="content hash mismatch"):
        OntologyGraph.from_dict(payload)
