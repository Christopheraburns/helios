"""The console's model graph: helios_model.graph scoped to the console's organizations,
and authorize_graph, which projects it for one principal under the console's policy.
Deleted with the console (burndown 8.2)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from apps.helios.console import authz
from apps.helios.console.domain import Model
from helios_cortex.artifacts import ArtifactStore
from helios_model import graph as model_graph
from helios_model.graph import ossie_detail as _ossie_detail  # noqa: F401 - used by the console's tests
from helios_model.graph import project_graph_lens  # noqa: F401 - re-exported
from helios_model.graph import proposal_detail as _proposal_detail  # noqa: F401


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    organization_id: str
    model_id: str
    status: str = "published"
    confidence: float | None = None
    evidence: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    permitted_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphEdge:
    id: str
    kind: str
    source: str
    target: str
    organization_id: str
    model_id: str
    status: str = "published"
    confidence: float | None = None
    evidence: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    permitted_actions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelGraph:
    model_id: str
    organization_id: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class GraphRepository(Protocol):
    def graph_for_model(self, model: Model) -> ModelGraph: ...


class ArtifactGraphRepository:
    """helios_model's graph of the model's artifacts, in the model's organization."""

    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts
        self._graphs = model_graph.ArtifactGraphRepository(artifacts)

    def graph_for_model(self, model: Model) -> ModelGraph:
        return _scoped(self._graphs.graph_for_model(model), model.organization_id)

    def detail_for_element(self, model: Model, element_id: str) -> dict[str, Any]:
        return self._graphs.detail_for_element(model, element_id)

    def review_graph_for_model(self, model: Model, run_id: str) -> ModelGraph:
        return _scoped(self._graphs.review_graph_for_model(model, run_id), model.organization_id)

    def review_detail_for_element(self, model: Model, run_id: str, element_id: str) -> dict[str, Any]:
        return self._graphs.review_detail_for_element(model, run_id, element_id)


def _scoped(graph: model_graph.ModelGraph, organization_id: str) -> ModelGraph:
    def node(n: model_graph.GraphNode) -> GraphNode:
        return GraphNode(n.id, n.kind, n.label, organization_id, n.model_id, n.status, n.confidence,
                         n.evidence, n.metadata)

    def edge(e: model_graph.GraphEdge) -> GraphEdge:
        return GraphEdge(e.id, e.kind, e.source, e.target, organization_id, e.model_id, e.status,
                         e.confidence, e.evidence, e.metadata)

    return ModelGraph(graph.model_id, organization_id, tuple(map(node, graph.nodes)),
                      tuple(map(edge, graph.edges)))


def authorize_graph(
    graph: ModelGraph,
    model: Model,
    principal: authz.Principal,
    policy: authz.Policy,
) -> ModelGraph:
    """Return an authorized projection with no dangling or cross-scope edges."""
    if (
        graph.model_id != model.id
        or graph.organization_id != model.organization_id
    ):
        raise ValueError("graph ownership does not match requested model")

    visible: dict[str, GraphNode] = {}
    for node in graph.nodes:
        if (
            node.organization_id != model.organization_id
            or node.model_id != model.id
        ):
            continue
        action = _read_action(node.kind)
        if not policy.can(
            principal, action, _resource(model, action, node.id)
        ).allowed:
            continue
        if _is_unpublished(node.status) and not policy.can(
            principal,
            authz.Action.MODEL_EDIT,
            _resource(model, authz.Action.MODEL_EDIT, node.id),
        ).allowed:
            continue
        visible[node.id] = replace(
            node,
            permitted_actions=_permitted_actions(
                model, principal, policy, node.kind, node.id
            ),
        )

    edges: list[GraphEdge] = []
    for edge in graph.edges:
        if (
            edge.organization_id != model.organization_id
            or edge.model_id != model.id
            or edge.source not in visible
            or edge.target not in visible
        ):
            continue
        action = _read_action(edge.kind)
        if not policy.can(
            principal, action, _resource(model, action, edge.id)
        ).allowed:
            continue
        if _is_unpublished(edge.status) and not policy.can(
            principal,
            authz.Action.MODEL_EDIT,
            _resource(model, authz.Action.MODEL_EDIT, edge.id),
        ).allowed:
            continue
        edges.append(
            replace(
                edge,
                permitted_actions=_permitted_actions(
                    model, principal, policy, edge.kind, edge.id
                ),
            )
        )

    return ModelGraph(
        model.id,
        model.organization_id,
        tuple(visible.values()),
        tuple(edges),
    )


def graph_response(graph: ModelGraph) -> dict[str, Any]:
    """Transport-neutral response contract; contains authorized objects only."""
    return {
        "model_id": graph.model_id,
        "organization_id": graph.organization_id,
        "nodes": [_node_dict(node) for node in graph.nodes],
        "edges": [_edge_dict(edge) for edge in graph.edges],
        "summary": {
            "node_count": len(graph.nodes),
            "edge_count": len(graph.edges),
            "node_kinds": sorted({node.kind for node in graph.nodes}),
            "edge_kinds": sorted({edge.kind for edge in graph.edges}),
        },
    }


def navigation_graph_response(graph: ModelGraph, **options: Any) -> dict[str, Any]:
    """Return a bounded projection of an already-authorized graph."""
    selected, navigation = model_graph.navigate(graph, **options)
    navigation["authorized_node_count"] = navigation.pop("graph_node_count")
    navigation["authorized_edge_count"] = navigation.pop("graph_edge_count")
    return {**graph_response(selected), "navigation": navigation}


def _read_action(kind: str) -> authz.Action:
    if kind in {
        "data_source",
        "dataset",
        "table",
        "view",
        "attribute",
        "column",
        "physical_relationship",
    }:
        return authz.Action.DATASOURCE_READ
    if kind in {"concept", "ontology_relationship"}:
        return authz.Action.ONTOLOGY_READ
    if kind == "domain":
        return authz.Action.MODEL_READ
    return authz.Action.SEMANTIC_READ


def _resource(
    model: Model, action: authz.Action, resource_id: str
) -> authz.Resource:
    prefix = action.value.split(".", 1)[0]
    if prefix == "model":
        return authz.Resource("model", model.id, model.organization_id)
    if prefix == "datasource":
        return authz.Resource(
            "datasource", resource_id, model.organization_id, model.id
        )
    return authz.Resource(
        prefix, resource_id, model.organization_id, model.id
    )


def _permitted_actions(
    model: Model,
    principal: authz.Principal,
    policy: authz.Policy,
    kind: str,
    resource_id: str,
) -> tuple[str, ...]:
    read = _read_action(kind)
    candidates = [read, authz.Action.MODEL_EDIT]
    if read is authz.Action.SEMANTIC_READ:
        candidates.append(authz.Action.SEMANTIC_EDIT)
    elif read is authz.Action.ONTOLOGY_READ:
        candidates.append(authz.Action.ONTOLOGY_EDIT)
    return tuple(
        action.value
        for action in candidates
        if policy.can(
            principal, action, _resource(model, action, resource_id)
        ).allowed
    )


def _is_unpublished(status: str) -> bool:
    return status.lower() in {
        "draft",
        "proposed",
        "needs_review",
        "approved",
        "rejected",
    }


def _node_dict(node: GraphNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind,
        "label": node.label,
        "status": node.status,
        "confidence": node.confidence,
        "evidence": node.evidence,
        "metadata": node.metadata,
        "permitted_actions": list(node.permitted_actions),
    }


def _edge_dict(edge: GraphEdge) -> dict[str, Any]:
    return {
        "id": edge.id,
        "kind": edge.kind,
        "source": edge.source,
        "target": edge.target,
        "status": edge.status,
        "confidence": edge.confidence,
        "evidence": edge.evidence,
        "metadata": edge.metadata,
        "permitted_actions": list(edge.permitted_actions),
    }
