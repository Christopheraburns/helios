"""Schema-agnostic Helios Model graph and authorization projection."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from helios_core import authz
from helios_core import review as review_store
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

    def detail_for_element(
        self, model: Model, element_id: str
    ) -> dict[str, Any]:
        document, _ = self._document(model.id)
        if document is None:
            return {}
        if any("table" in dataset for dataset in document.get("datasets", [])):
            return _proposal_detail(document, element_id)
        return _ossie_detail(document, element_id)

    def review_graph_for_model(
        self, model: Model, run_id: str
    ) -> ModelGraph:
        if (
            run_id not in model.discovery_run_ids
            or "/" in run_id
            or ".." in run_id
        ):
            raise FileNotFoundError(run_id)
        run_directory = self.artifacts.root / "runs" / run_id
        proposal_path = run_directory / "propose.json"
        if not proposal_path.is_file():
            raise FileNotFoundError(run_id)
        with proposal_path.open() as handle:
            proposal = json.load(handle)
        review = review_store.load(
            str(run_directory / "review.json"),
            run_id,
        )
        graph = _proposal_graph(model, proposal, "needs_review")
        return _apply_review_states(graph, review)

    def review_detail_for_element(
        self, model: Model, run_id: str, element_id: str
    ) -> dict[str, Any]:
        if (
            run_id not in model.discovery_run_ids
            or "/" in run_id
            or ".." in run_id
        ):
            raise FileNotFoundError(run_id)
        proposal_path = (
            self.artifacts.root / "runs" / run_id / "propose.json"
        )
        if not proposal_path.is_file():
            raise FileNotFoundError(run_id)
        with proposal_path.open() as handle:
            return _proposal_detail(json.load(handle), element_id)


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


def navigation_graph_response(
    graph: ModelGraph,
    *,
    lens: str | None = None,
    focus_node_id: str | None = None,
    depth: int = 1,
    include_attributes: bool = False,
    limit: int = 100,
    edge_limit: int = 500,
    query: str | None = None,
) -> dict[str, Any]:
    """Return a bounded projection of an already-authorized graph."""
    if lens is not None:
        graph = project_graph_lens(graph, lens)
    nodes_by_id = {node.id: node for node in graph.nodes}
    searchable = [
        node
        for node in graph.nodes
        if include_attributes or node.kind not in {"attribute", "column"}
    ]
    normalized_query = (query or "").strip().casefold()
    total_matches: int | None = None

    if normalized_query:
        matches = [
            node
            for node in searchable
            if normalized_query
            in " ".join(
                (
                    node.id,
                    node.kind,
                    node.label,
                    json.dumps(node.metadata, sort_keys=True, default=str),
                )
            ).casefold()
        ]
        matches.sort(key=lambda node: (node.label.casefold(), node.id))
        total_matches = len(matches)
        selected_ids = {node.id for node in matches[:limit]}
    else:
        if focus_node_id is not None and focus_node_id not in nodes_by_id:
            raise KeyError(focus_node_id)
        roots = (
            [focus_node_id]
            if focus_node_id
            else sorted(
                (
                    node.id
                    for node in searchable
                    if node.kind == "domain"
                ),
                key=lambda node_id: (
                    nodes_by_id[node_id].label.casefold(),
                    node_id,
                ),
            )
        )
        if not roots and searchable:
            roots = [min(searchable, key=lambda node: node.label.casefold()).id]

        adjacency: dict[str, set[str]] = {
            node.id: set() for node in graph.nodes
        }
        for edge in graph.edges:
            adjacency.setdefault(edge.source, set()).add(edge.target)
            adjacency.setdefault(edge.target, set()).add(edge.source)

        selected_ids: set[str] = set()
        frontier = list(roots)
        for level in range(depth + 1):
            next_frontier: list[str] = []
            for node_id in sorted(
                frontier,
                key=lambda item: (
                    nodes_by_id[item].label.casefold(),
                    item,
                ),
            ):
                node = nodes_by_id[node_id]
                if (
                    node.kind in {"attribute", "column"}
                    and not include_attributes
                    and node_id != focus_node_id
                ):
                    continue
                if len(selected_ids) >= limit:
                    break
                selected_ids.add(node_id)
                if level < depth:
                    next_frontier.extend(
                        neighbor
                        for neighbor in adjacency.get(node_id, ())
                        if neighbor not in selected_ids
                    )
            if len(selected_ids) >= limit:
                break
            frontier = next_frontier

    selected_nodes = tuple(
        node for node in graph.nodes if node.id in selected_ids
    )
    all_selected_edges = tuple(
        edge
        for edge in graph.edges
        if edge.source in selected_ids and edge.target in selected_ids
    )
    selected_edges = all_selected_edges[:edge_limit]
    selected_graph = ModelGraph(
        graph.model_id,
        graph.organization_id,
        selected_nodes,
        selected_edges,
    )
    response = graph_response(selected_graph)

    all_adjacency: dict[str, set[str]] = {
        node.id: set() for node in graph.nodes
    }
    for edge in graph.edges:
        all_adjacency.setdefault(edge.source, set()).add(edge.target)
        all_adjacency.setdefault(edge.target, set()).add(edge.source)
    hidden_neighbors = {
        node_id: len(all_adjacency.get(node_id, set()) - selected_ids)
        for node_id in selected_ids
    }
    nodes_truncated = (
        total_matches > len(selected_ids)
        if total_matches is not None
        else any(hidden_neighbors.values())
    )
    response["navigation"] = {
        "focus_node_id": focus_node_id,
        "truncated": (
            nodes_truncated
            or len(all_selected_edges) > len(selected_edges)
        ),
        "authorized_node_count": len(graph.nodes),
        "authorized_edge_count": len(graph.edges),
        "returned_node_count": len(selected_nodes),
        "returned_edge_count": len(selected_edges),
        "total_match_count": total_matches,
        "hidden_neighbor_count": hidden_neighbors,
        "expandable_node_ids": sorted(
            node_id
            for node_id, count in hidden_neighbors.items()
            if count > 0
        ),
    }
    return response


def project_graph_lens(graph: ModelGraph, lens: str) -> ModelGraph:
    """Project one authorized graph without changing its model identity."""
    normalized = lens.strip().lower()
    node_kinds = {
        "physical": {
            "domain",
            "data_source",
            "dataset",
            "table",
            "view",
            "attribute",
            "column",
        },
        "semantic": {
            "domain",
            "concept",
            "dimension",
            "metric",
            "measure",
            "dataset",
        },
        "ontology": {
            "domain",
            "concept",
            "ontology_concept",
            "property",
            "ontology_property",
        },
    }
    if normalized not in node_kinds:
        raise ValueError(f"unknown graph lens {lens!r}")

    nodes = tuple(
        node for node in graph.nodes if node.kind in node_kinds[normalized]
    )
    node_ids = {node.id for node in nodes}
    allowed_edges = {
        "physical": {"physical_relationship", "inferred_relationship"},
        "semantic": {"semantic_relationship", "inferred_relationship"},
        "ontology": {"ontology_relationship", "conceptual_relationship"},
    }[normalized]
    edges = tuple(
        edge
        for edge in graph.edges
        if edge.source in node_ids
        and edge.target in node_ids
        and (
            edge.kind in allowed_edges
            or edge.id.startswith("contains:")
            or edge.id.startswith("uses:")
        )
    )
    return ModelGraph(
        graph.model_id,
        graph.organization_id,
        nodes,
        edges,
    )


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
    root = base.nodes[0]
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
                    "semantic_role": dataset.get("kind"),
                    "description": dataset.get("description"),
                    "review_section": "datasets",
                    "review_element_id": review_store.dataset_id(dataset),
                },
            )
        )
        edges.append(
            GraphEdge(
                f"contains:{root.id}:{dataset_id}",
                "semantic_relationship",
                root.id,
                dataset_id,
                model.organization_id,
                model.id,
                status=status,
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
                        "description": item.get("description"),
                        "business_terms": item.get("glossary_terms"),
                        "review_section": "fields",
                        "review_element_id": review_store.field_id(
                            dataset, item
                        ),
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
                metadata={
                    "description": metric.get("description", ""),
                    "review_section": "metrics",
                    "review_element_id": review_store.metric_id(metric),
                },
            )
        )
        edges.append(
            GraphEdge(
                f"contains:{root.id}:{metric_id}",
                "semantic_relationship",
                root.id,
                metric_id,
                model.organization_id,
                model.id,
                status=status,
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
                metadata={
                    "review_section": "relationships",
                    "review_element_id": review_store.relationship_id(
                        relationship
                    ),
                    "match_ratio": relationship.get("match_ratio"),
                    "distinct_values": relationship.get("distinct_values"),
                    "unmatched_values": relationship.get("unmatched"),
                },
            )
        )
    for term in document.get("glossary_terms", []):
        term_id = f"concept:{term['name']}"
        nodes.append(
            GraphNode(
                term_id,
                "concept",
                term["name"],
                model.organization_id,
                model.id,
                status=status,
                confidence=term.get("confidence"),
                evidence=_evidence(term.get("source")),
                metadata={
                    "description": term.get("description"),
                    "review_section": "glossary_terms",
                    "review_element_id": review_store.term_id(term),
                },
            )
        )
        edges.append(
            GraphEdge(
                f"contains:{root.id}:{term_id}",
                "semantic_relationship",
                root.id,
                term_id,
                model.organization_id,
                model.id,
                status=status,
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
    root = base.nodes[0]
    datasets: dict[str, str] = {}
    for dataset in document.get("datasets", []):
        name = dataset["name"]
        dataset_id = f"dataset:{name}"
        dataset_extension = _helios_extension(dataset)
        datasets[name] = dataset_id
        nodes.append(
            GraphNode(
                dataset_id,
                "dataset",
                name,
                model.organization_id,
                model.id,
                status=status,
                metadata={
                    "physical_name": dataset.get("source"),
                    "semantic_role": dataset_extension.get("kind"),
                },
            )
        )
        edges.append(
            GraphEdge(
                f"contains:{root.id}:{dataset_id}",
                "semantic_relationship",
                root.id,
                dataset_id,
                model.organization_id,
                model.id,
                status=status,
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
        metric_id = f"metric:{metric['name']}"
        nodes.append(
            GraphNode(
                metric_id,
                "metric",
                metric["name"],
                model.organization_id,
                model.id,
                status=status,
                metadata={"description": metric.get("description", "")},
            )
        )
        edges.append(
            GraphEdge(
                f"contains:{root.id}:{metric_id}",
                "semantic_relationship",
                root.id,
                metric_id,
                model.organization_id,
                model.id,
                status=status,
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


def _proposal_detail(
    document: dict[str, Any], element_id: str
) -> dict[str, Any]:
    for dataset in document.get("datasets", []):
        table = dataset["table"]
        if element_id == f"dataset:{table}":
            return _compact(
                {
                    "description": dataset.get("description"),
                    "physical_identity": table,
                    "semantic_role": dataset.get("kind"),
                    "primary_key": dataset.get("primary_key"),
                }
            )
        for field in dataset.get("fields", []):
            if element_id == f"attribute:{table}:{field['column']}":
                return _compact(
                    {
                        "description": field.get("description"),
                        "physical_identity": f"{table}.{field['column']}",
                        "dataset_physical_identity": table,
                        "physical_name": field["column"],
                        "physical_type": field.get("type"),
                        "semantic_role": field.get("role"),
                        "business_terms": field.get("glossary_terms"),
                        "proposed_term": field.get("proposed_term"),
                        "refers_to": field.get("refers_to"),
                    }
                )
    for relationship in document.get("relationships", []):
        source = f"dataset:{relationship.get('from')}"
        target = f"dataset:{relationship.get('to')}"
        relationship_id = (
            f"relationship:{source}:{target}:"
            f"{relationship.get('from_column', '')}"
        )
        if element_id == relationship_id:
            return _compact(
                {
                    "source_physical_identity": relationship.get("from"),
                    "source_column": relationship.get("from_column"),
                    "target_physical_identity": relationship.get("to"),
                    "target_column": relationship.get("to_column"),
                    "relationship_type": relationship.get("source"),
                    "target_rows": relationship.get("to_rows"),
                    "distinct_values": relationship.get("distinct_values"),
                    "unmatched_values": relationship.get("unmatched"),
                    "match_ratio": relationship.get("match_ratio"),
                    "accepted": relationship.get("accepted"),
                }
            )
    return {}


def _apply_review_states(
    graph: ModelGraph, review: dict[str, Any]
) -> ModelGraph:
    status_by_decision = {
        "accept": "approved",
        "edit": "approved",
        "reject": "rejected",
        "pending": "needs_review",
    }

    def apply(element: GraphNode | GraphEdge):
        section = element.metadata.get("review_section")
        element_id = element.metadata.get("review_element_id")
        if not section or not element_id:
            return element
        entry = review.get(section, {}).get(element_id, {})
        decision = entry.get("decision", "pending")
        audit = _compact(
            {
                "review_decision": decision,
                "review_note": entry.get("note"),
                "review_overrides": entry.get("overrides"),
                "reviewed_at": review.get("reviewed_at"),
                "reviewed_by": review.get("reviewed_by"),
            }
        )
        return replace(
            element,
            status=status_by_decision.get(decision, "needs_review"),
            metadata={**element.metadata, **audit},
        )

    return ModelGraph(
        graph.model_id,
        graph.organization_id,
        tuple(apply(node) for node in graph.nodes),
        tuple(apply(edge) for edge in graph.edges),
    )


def _ossie_detail(
    document: dict[str, Any], element_id: str
) -> dict[str, Any]:
    for dataset in document.get("datasets", []):
        name = dataset["name"]
        source = dataset.get("source")
        dataset_extension = _helios_extension(dataset)
        if element_id == f"dataset:{name}":
            return _compact(
                {
                    "description": dataset.get("description"),
                    "physical_identity": source,
                    "semantic_role": dataset_extension.get("kind"),
                    "primary_key": dataset.get("primary_key"),
                }
            )
        for field in dataset.get("fields", []):
            if element_id != f"attribute:{name}:{field['name']}":
                continue
            extension = _helios_extension(field)
            return _compact(
                {
                    "description": field.get("description"),
                    "physical_identity": (
                        f"{source}.{field['name']}" if source else field["name"]
                    ),
                    "dataset_physical_identity": source,
                    "physical_name": field["name"],
                    "physical_type": (
                        extension.get("engine_type")
                        or field.get("datatype")
                    ),
                    "semantic_role": extension.get("role"),
                    "business_terms": extension.get("glossary_terms"),
                    "refers_to": extension.get("refers_to"),
                }
            )
    for relationship in document.get("relationships", []):
        if element_id != f"relationship:{relationship['name']}":
            continue
        extension = _helios_extension(relationship)
        return _compact(
            {
                "source_physical_identity": relationship.get("from"),
                "source_columns": relationship.get("from_columns"),
                "target_physical_identity": relationship.get("to"),
                "target_columns": relationship.get("to_columns"),
                "relationship_type": extension.get("source"),
                "match_ratio": extension.get("match_ratio"),
            }
        )
    return {}


def _helios_extension(document: dict[str, Any]) -> dict[str, Any]:
    for extension in document.get("custom_extensions") or []:
        if extension.get("vendor_name") != "HELIOS":
            continue
        try:
            return json.loads(extension.get("data") or "{}")
        except json.JSONDecodeError:
            return {}
    return {}


def _compact(document: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if value is not None and value != [] and value != ""
    }


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
