"""Schema-agnostic Helios Model graph and authorization projection."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from helios_core import authz
from helios_core.artifacts import ArtifactStore
from helios_core.domain import Model


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
    """Build a graph from only the requested Model's stored artifacts."""

    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts

    def graph_for_model(self, model: Model) -> ModelGraph:
        document, status = self._document(model.id)
        if document is None:
            return _base_graph(model)
        if any("table" in dataset for dataset in document.get("datasets", [])):
            return _proposal_graph(model, document, status)
        return _ossie_graph(model, document, status)

    def _document(
        self, model_id: str
    ) -> tuple[dict[str, Any] | None, str]:
        published = self.artifacts.published_ossie_path(model_id, "json")
        if published.exists():
            with published.open() as handle:
                return json.load(handle), "published"
        proposed = self.artifacts.directory(model_id, "proposed")
        if proposed.is_dir():
            candidates = sorted(
                proposed.glob("*.proposal.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                with candidates[0].open() as handle:
                    return json.load(handle), "proposed"
        return None, "configured"


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


def _base_graph(model: Model) -> ModelGraph:
    root = GraphNode(
        id=f"model:{model.id}",
        kind="domain",
        label=model.name,
        organization_id=model.organization_id,
        model_id=model.id,
        status="configured",
        metadata={"description": model.description},
    )
    nodes = [root]
    edges = []
    for reference in model.data_sources:
        node_id = f"datasource:{reference.data_source_id}"
        nodes.append(
            GraphNode(
                node_id,
                "data_source",
                reference.data_source_id,
                model.organization_id,
                model.id,
                status="configured",
                metadata={"selected_assets": list(reference.selected_assets)},
            )
        )
        edges.append(
            GraphEdge(
                f"uses:{model.id}:{reference.data_source_id}",
                "semantic_relationship",
                root.id,
                node_id,
                model.organization_id,
                model.id,
                status="configured",
            )
        )
    return ModelGraph(
        model.id, model.organization_id, tuple(nodes), tuple(edges)
    )


def _proposal_graph(
    model: Model, document: dict[str, Any], status: str
) -> ModelGraph:
    base = _base_graph(model)
    nodes = list(base.nodes)
    edges = list(base.edges)
    datasets: dict[str, str] = {}
    for dataset in document.get("datasets", []):
        table = dataset["table"]
        dataset_id = f"dataset:{table}"
        datasets[table] = dataset_id
        nodes.append(
            GraphNode(
                dataset_id,
                "dataset",
                dataset.get("name") or table,
                model.organization_id,
                model.id,
                status=status,
                confidence=dataset.get("confidence"),
                evidence=_evidence(dataset.get("source")),
                metadata={
                    "physical_name": table,
                    "dataset_kind": dataset.get("kind", "other"),
                },
            )
        )
        for item in dataset.get("fields", []):
            column = item["column"]
            field_id = f"attribute:{table}:{column}"
            nodes.append(
                GraphNode(
                    field_id,
                    "attribute",
                    item.get("name") or column,
                    model.organization_id,
                    model.id,
                    status=status,
                    confidence=item.get("confidence"),
                    evidence=_evidence(item.get("source")),
                    metadata={
                        "physical_name": column,
                        "datatype": item.get("type"),
                        "role": item.get("role"),
                    },
                )
            )
            edges.append(
                GraphEdge(
                    f"contains:{dataset_id}:{field_id}",
                    "physical_relationship",
                    dataset_id,
                    field_id,
                    model.organization_id,
                    model.id,
                    status=status,
                )
            )
    for metric in document.get("metrics", []):
        metric_id = f"metric:{metric['name']}"
        nodes.append(
            GraphNode(
                metric_id,
                "metric",
                metric["name"],
                model.organization_id,
                model.id,
                status=status,
                confidence=metric.get("confidence"),
                evidence=_evidence(metric.get("source")),
                metadata={"description": metric.get("description", "")},
            )
        )
    for relationship in document.get("relationships", []):
        source = datasets.get(relationship.get("from"))
        target = datasets.get(relationship.get("to"))
        if not source or not target:
            continue
        inferred = relationship.get("source") not in ("catalog", "declared")
        edges.append(
            GraphEdge(
                f"relationship:{source}:{target}:{relationship.get('from_column', '')}",
                "inferred_relationship" if inferred else "physical_relationship",
                source,
                target,
                model.organization_id,
                model.id,
                status=status,
                confidence=relationship.get("confidence"),
                evidence=_evidence(relationship.get("source")),
            )
        )
    return ModelGraph(
        model.id, model.organization_id, tuple(nodes), tuple(edges)
    )


def _ossie_graph(
    model: Model, document: dict[str, Any], status: str
) -> ModelGraph:
    base = _base_graph(model)
    nodes = list(base.nodes)
    edges = list(base.edges)
    datasets: dict[str, str] = {}
    for dataset in document.get("datasets", []):
        name = dataset["name"]
        dataset_id = f"dataset:{name}"
        datasets[name] = dataset_id
        nodes.append(
            GraphNode(
                dataset_id,
                "dataset",
                name,
                model.organization_id,
                model.id,
                status=status,
                metadata={"physical_name": dataset.get("source")},
            )
        )
        for item in dataset.get("fields", []):
            field_id = f"attribute:{name}:{item['name']}"
            nodes.append(
                GraphNode(
                    field_id,
                    "attribute",
                    item.get("label") or item["name"],
                    model.organization_id,
                    model.id,
                    status=status,
                    metadata={
                        "physical_name": item["name"],
                        "datatype": item.get("datatype"),
                    },
                )
            )
            edges.append(
                GraphEdge(
                    f"contains:{dataset_id}:{field_id}",
                    "physical_relationship",
                    dataset_id,
                    field_id,
                    model.organization_id,
                    model.id,
                    status=status,
                )
            )
    for metric in document.get("metrics", []):
        nodes.append(
            GraphNode(
                f"metric:{metric['name']}",
                "metric",
                metric["name"],
                model.organization_id,
                model.id,
                status=status,
                metadata={"description": metric.get("description", "")},
            )
        )
    for relationship in document.get("relationships", []):
        source = datasets.get(relationship.get("from"))
        target = datasets.get(relationship.get("to"))
        if source and target:
            edges.append(
                GraphEdge(
                    f"relationship:{relationship['name']}",
                    "semantic_relationship",
                    source,
                    target,
                    model.organization_id,
                    model.id,
                    status=status,
                )
            )
    return ModelGraph(
        model.id, model.organization_id, tuple(nodes), tuple(edges)
    )


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
    return status.lower() in {"draft", "proposed", "rejected"}


def _evidence(source: Any) -> str | None:
    return f"Derived from {source}" if source else None


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
