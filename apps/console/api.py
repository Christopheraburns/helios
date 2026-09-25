"""Authorized, model-scoped HTTP API for the Helios console application.

Authentication and HTTP error translation live here at the application boundary.
Resource policy remains in ``helios_core.authz`` and can be reused by MCP or jobs.
"""
from __future__ import annotations

import json
import os
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from helios_core import audit, authz
from helios_core import health as system_health
from helios_core import publication
from helios_core import review as review_store
from helios_core import runs as runstore
from helios_core.artifacts import ArtifactStore
from helios_core.atlas import AtlasClient, AtlasError
from helios_core.config import atlas_config
from helios_core.domain import Model, Organization
from helios_core.graph import (
    ArtifactGraphRepository,
    GraphEdge,
    GraphNode,
    GraphRepository,
    authorize_graph,
    graph_response,
    navigation_graph_response,
)
from helios_core.metadata import (
    AuditEvent,
    AuditSession,
    ConversationVersionConflict,
    MetadataRepository,
    StoredConversation,
)
from apps.console.conversation import (
    ConversationService,
    ConversationUnavailable,
)

api_router = APIRouter(prefix="/api/v1", tags=["api-v1"])

ReviewSection = Literal[
    "datasets",
    "fields",
    "relationships",
    "metrics",
    "glossary_terms",
]
RunLifecycleStatus = Literal[
    "queued",
    "running",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
]


class RunLifecycleDTO(BaseModel):
    """Lifecycle contract; artifact-backed values remain nullable when unavailable."""

    model_config = ConfigDict(extra="allow")

    status: RunLifecycleStatus | None = None
    progress: float | None = Field(default=None, ge=0, le=100)
    initiator: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    warnings: list[Any] = Field(default_factory=list)
    errors: list[Any] = Field(default_factory=list)


class RunDTO(RunLifecycleDTO):
    id: str
    type: str
    model_id: str
    stages: dict[str, bool] = Field(default_factory=dict)
    phases: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, dict[str, int]] = Field(default_factory=dict)


class RunCollectionDTO(BaseModel):
    model_id: str
    runs: list[RunDTO]
    available_actions: list[str]


class ReviewDecisionRequest(BaseModel):
    section: ReviewSection
    element_id: str = Field(min_length=1, max_length=500)
    decision: Literal["accept", "reject", "edit"]
    overrides: dict[str, Any] | None = None
    note: str = Field(default="", max_length=2000)


class DatasetReviewDecisionRequest(BaseModel):
    table: str = Field(min_length=1, max_length=500)
    decision: Literal["accept", "reject"]


class BulkReviewDecisionRequest(BaseModel):
    min_confidence: float = Field(default=0.85, ge=0, le=1)


class ResetReviewRequest(BaseModel):
    section: ReviewSection | None = None


class GlossaryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class GlossaryTermWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: str = Field(default="", max_length=4000)
    long_description: str = Field(default="", max_length=12000)
    abbreviation: str = Field(default="", max_length=100)
    examples: list[str] = Field(default_factory=list, max_length=100)


class GlossaryAssignmentRequest(BaseModel):
    canvas_element_id: str = Field(min_length=1, max_length=1000)


class ConversationTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


class PersistedConversationTurnRequest(ConversationTurnRequest):
    expected_version: int = Field(ge=1)


class ConversationToolTraceResponse(BaseModel):
    tool: str
    arguments: dict[str, Any]
    result: Any


class ConversationQueryResultResponse(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    sql: str | None = None


class ConversationTurnResponse(BaseModel):
    model_id: str
    answer: str
    tool_trace: list[ConversationToolTraceResponse]
    query_result: ConversationQueryResultResponse | None = None


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime


class ConversationSummaryResponse(BaseModel):
    id: str
    model_id: str
    title: str
    version: int
    created_at: datetime
    updated_at: datetime


class ConversationDetailResponse(ConversationSummaryResponse):
    messages: list[ConversationMessageResponse]


class ConversationCollectionResponse(BaseModel):
    model_id: str
    conversations: list[ConversationSummaryResponse]


class PersistedConversationTurnResponse(BaseModel):
    conversation: ConversationDetailResponse
    turn: ConversationTurnResponse


class AuditClientEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "navigation.view",
        "context.organization_select",
        "context.model_select",
        "workspace.collapse",
        "workspace.expand",
        "canvas.lens_change",
        "canvas.node_focus",
        "canvas.node_select",
        "activity.filter_change",
    ]
    path: str | None = Field(default=None, max_length=300)
    resource_type: Literal[
        "application",
        "organization",
        "model",
        "workspace",
        "canvas",
        "node",
        "activity",
    ] | None = None
    resource_id: str | None = Field(default=None, max_length=300)
    model_id: str | None = Field(default=None, max_length=200)


class AuditEventResponse(BaseModel):
    id: str
    occurred_at: datetime
    request_id: str | None
    session_id: str | None
    principal_id: str | None
    organization_id: str | None
    model_id: str | None
    component: str
    event_type: str
    action: str
    resource_type: str | None
    resource_id: str | None
    outcome: str
    severity: str
    http_status: int | None
    duration_ms: float | None
    summary: str
    details: dict[str, Any]
    diagnostics: dict[str, Any] | None = None


class AuditEventCollectionResponse(BaseModel):
    items: list[AuditEventResponse]
    page: dict[str, Any]
    filters: dict[str, Any]
    available_actions: list[str]


class AuditSessionResponse(BaseModel):
    session_id: str
    principal_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    event_count: int
    organization_id: str | None


class AuditSessionCollectionResponse(BaseModel):
    sessions: list[AuditSessionResponse]
    available_actions: list[str]


class ResourceStore:
    """Small application-boundary store, replaceable by persistent storage later."""

    def __init__(
        self,
        organizations: tuple[Organization, ...] = (),
        models: tuple[Model, ...] = (),
    ):
        self._organizations = {organization.id: organization for organization in organizations}
        self._models = {model.id: model for model in models}
        for model in models:
            if model.organization_id not in self._organizations:
                raise ValueError(
                    f"model {model.id!r} references unknown organization "
                    f"{model.organization_id!r}"
                )

    def organization(self, organization_id: str) -> Organization | None:
        return self._organizations.get(organization_id)

    def model(self, model_id: str) -> Model | None:
        return self._models.get(model_id)

    def models_for_organization(self, organization_id: str) -> list[Model]:
        return sorted(
            (
                model
                for model in self._models.values()
                if model.organization_id == organization_id
            ),
            key=lambda model: model.name.lower(),
        )


@dataclass(frozen=True)
class AuthorizedModel:
    model: Model
    principal: authz.Principal
    policy: authz.Policy

    @property
    def available_actions(self) -> list[str]:
        return [
            action.value
            for action in authz.Action
            if self.policy.can(
                self.principal,
                action,
                _resource_for_action(self.model, action),
            ).allowed
        ]


def principal_from_request(request: Request) -> authz.Principal | None:
    """Translate trusted Workbench identity context when one is present."""
    development_subject = (
        os.environ.get("HELIOS_DEV_USER")
        if os.environ.get("HELIOS_DEV") == "1"
        else None
    )
    subject = (
        request.headers.get("remote-user")
        or request.headers.get("x-forwarded-user")
        or development_subject
    )
    if not subject:
        return None
    return authz.Principal(
        issuer="cloudera-workbench",
        subject=subject,
        kind=authz.PrincipalKind.HUMAN,
        display_name=subject,
    )


def current_principal(request: Request) -> authz.Principal:
    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(401, "authenticated principal is required")
    return principal


def resource_store(request: Request) -> ResourceStore | MetadataRepository:
    override = getattr(request.app.state, "resource_store", None)
    return override or request.app.state.metadata_repository


def authorization_policy(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
) -> authz.Policy:
    override = getattr(request.app.state, "authorization_policy", None)
    if override is not None:
        return override
    repository: MetadataRepository = request.app.state.metadata_repository
    return authz.Policy(repository.grants_for_principal(principal.id))


def graph_repository(request: Request) -> GraphRepository:
    override = getattr(request.app.state, "graph_repository", None)
    return override or ArtifactGraphRepository(ArtifactStore(runstore.ROOT))


def atlas_client(request: Request) -> AtlasClient:
    override = getattr(request.app.state, "atlas_client", None)
    if override is not None:
        return override
    configuration = atlas_config()
    if configuration is None:
        raise HTTPException(503, "Atlas glossary service is not configured")
    return AtlasClient(configuration)


def conversation_service(request: Request) -> ConversationService:
    override = getattr(request.app.state, "conversation_service", None)
    if override is not None:
        return override
    try:
        return ConversationService.from_env()
    except ConversationUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


def load_organization(
    org_id: str,
    store: Annotated[ResourceStore | MetadataRepository, Depends(resource_store)],
) -> Organization:
    organization = store.organization(org_id)
    if organization is None:
        raise HTTPException(404, "organization not found")
    return organization


def load_model(
    model_id: str,
    store: Annotated[ResourceStore | MetadataRepository, Depends(resource_store)],
) -> Model:
    model = store.model(model_id)
    if model is None:
        raise HTTPException(404, "model not found")
    return model


def authorize_organization(
    action: authz.Action,
) -> Callable[..., Organization]:
    def dependency(
        organization: Annotated[Organization, Depends(load_organization)],
        principal: Annotated[authz.Principal, Depends(current_principal)],
        policy: Annotated[authz.Policy, Depends(authorization_policy)],
    ) -> Organization:
        resource = authz.Resource(
            "organization", organization.id, organization_id=organization.id
        )
        _require(policy, principal, action, resource)
        return organization

    return dependency


def authorize_model(
    action: authz.Action,
    resource_type: str = "model",
) -> Callable[..., AuthorizedModel]:
    def dependency(
        model: Annotated[Model, Depends(load_model)],
        principal: Annotated[authz.Principal, Depends(current_principal)],
        policy: Annotated[authz.Policy, Depends(authorization_policy)],
    ) -> AuthorizedModel:
        resource_id = model.id if resource_type == "model" else (
            getattr(model, f"{resource_type}_id", None) or resource_type
        )
        resource = authz.Resource(
            resource_type,
            resource_id,
            organization_id=model.organization_id,
            model_id=None if resource_type == "model" else model.id,
        )
        _require(policy, principal, action, resource)
        return AuthorizedModel(model, principal, policy)

    return dependency


def _require(
    policy: authz.Policy,
    principal: authz.Principal,
    action: authz.Action,
    resource: authz.Resource,
) -> None:
    try:
        policy.require(principal, action, resource)
    except authz.AuthorizationDenied as exc:
        raise HTTPException(403, str(exc)) from exc


def _resource_for_action(model: Model, action: authz.Action) -> authz.Resource:
    prefix = action.value.split(".", 1)[0]
    if prefix == "organization":
        return authz.Resource(
            "organization", model.organization_id, model.organization_id
        )
    if prefix == "datasource":
        return authz.Resource(
            "datasource", "referenced", model.organization_id, model.id
        )
    if prefix in {"model", "discovery", "query"}:
        return authz.Resource("model", model.id, model.organization_id)
    resource_id = getattr(model, f"{prefix}_id", None) or prefix
    return authz.Resource(prefix, resource_id, model.organization_id, model.id)


def _model_metadata(context: AuthorizedModel) -> dict:
    model = context.model
    return {
        "id": model.id,
        "organization_id": model.organization_id,
        "name": model.name,
        "description": model.description,
        "data_sources": [
            {
                "data_source_id": reference.data_source_id,
                "selected_assets": list(reference.selected_assets),
            }
            for reference in model.data_sources
        ],
        "available_actions": context.available_actions,
    }


def _model_overview(
    context: AuthorizedModel,
    metadata: MetadataRepository,
    graphs: GraphRepository,
) -> dict:
    model = context.model
    stored = metadata.stored_model(model.id)
    if stored is None:
        raise HTTPException(404, "model not found")

    graph = authorize_graph(
        graphs.graph_for_model(model),
        model,
        context.principal,
        context.policy,
    )
    nodes = {node.id: node for node in graph.nodes}
    semantic_nodes = [
        node for node in graph.nodes if node.status != "configured"
    ]
    publication_state = (
        "published"
        if any(node.status == "published" for node in semantic_nodes)
        else "proposed"
        if any(node.status == "proposed" for node in semantic_nodes)
        else "configured"
    )
    relationships = [
        edge
        for edge in graph.edges
        if edge.kind in {"semantic_relationship", "inferred_relationship"}
        and nodes.get(edge.source)
        and nodes.get(edge.target)
        and nodes[edge.source].kind == "dataset"
        and nodes[edge.target].kind == "dataset"
    ]

    known_runs = {run["id"]: run for run in runstore.list_runs()}
    latest_run_id = model.discovery_run_ids[-1] if model.discovery_run_ids else None
    latest_run = known_runs.get(latest_run_id) if latest_run_id else None
    unresolved_review_items: int | None = None
    review_status = "not_available"
    if latest_run_id:
        proposal = runstore.load(latest_run_id, "propose")
        if proposal is not None:
            review = review_store.load(
                os.path.join(runstore.RUNS_DIR, latest_run_id, "review.json"),
                latest_run_id,
            )
            review_summary = review_store.summary(review, proposal)
            unresolved_review_items = sum(
                section["pending"] for section in review_summary.values()
            )
            review_status = (
                "pending" if unresolved_review_items else "complete"
            )

    stages = latest_run.get("stages", {}) if latest_run else {}
    discovery_status = (
        "not_started"
        if latest_run_id is None
        else "unavailable"
        if latest_run is None
        else "proposals_ready"
        if stages.get("propose")
        else "profile_complete"
        if stages.get("profile")
        else "harvest_complete"
        if stages.get("harvest")
        else "started"
    )
    creator = metadata.principal(stored.created_by)

    return {
        **_model_metadata(context),
        "status": stored.status,
        "creator": {
            "id": stored.created_by,
            "display_name": (
                creator.display_name if creator else stored.created_by
            ),
        },
        "created_at": stored.created_at.isoformat(),
        "updated_at": stored.updated_at.isoformat(),
        "data_sources": [
            {
                "data_source_id": reference.data_source_id,
                "name": (
                    source.name if source is not None else reference.data_source_id
                ),
                "connector": source.connector if source is not None else None,
                "selected_assets": list(reference.selected_assets),
            }
            for reference in model.data_sources
            for source in [metadata.data_source(reference.data_source_id)]
        ],
        "summary": {
            "dataset_count": sum(
                node.kind == "dataset" for node in graph.nodes
            ),
            "relationship_count": len(relationships),
            "concept_count": sum(
                node.kind == "concept" for node in graph.nodes
            ),
            "metric_count": sum(
                node.kind == "metric" for node in graph.nodes
            ),
        },
        "lifecycle": {
            "publication_state": publication_state,
            "discovery_status": discovery_status,
            "review_status": review_status,
            "unresolved_review_items": unresolved_review_items,
            "latest_run_id": latest_run_id,
        },
    }


def _latest_successful_run(model: Model, stage: str) -> dict | None:
    timestamp_key = {
        "harvest": "harvested_at",
        "profile": "profiled_at",
        "propose": "proposed_at",
    }[stage]
    for run_id in reversed(model.discovery_run_ids):
        artifact = runstore.load(run_id, stage)
        if artifact is not None:
            return {
                "run_id": run_id,
                "completed_at": artifact.get(timestamp_key) or None,
            }
    return None


def _model_status_components(
    overview: dict,
) -> list[system_health.HealthComponent]:
    lifecycle = overview["lifecycle"]
    publication_state = lifecycle["publication_state"]
    if publication_state == "published":
        semantic_status: system_health.HealthState = "healthy"
        semantic_description = "A published semantic model is available."
    elif publication_state == "proposed":
        semantic_status = "degraded"
        semantic_description = "Semantic model proposals require review."
    else:
        semantic_status = "unknown"
        semantic_description = "No published semantic model is available."

    discovery_state = lifecycle["discovery_status"]
    if discovery_state == "proposals_ready":
        discovery_status: system_health.HealthState = "healthy"
        discovery_description = "The latest discovery run produced proposals."
    elif discovery_state == "profile_complete":
        discovery_status = "degraded"
        discovery_description = "Profiling completed; proposals are pending."
    elif discovery_state == "harvest_complete":
        discovery_status = "degraded"
        discovery_description = (
            "Discovery harvest completed; profiling is pending."
        )
    elif discovery_state == "started":
        discovery_status = "degraded"
        discovery_description = "A discovery run has not completed."
    elif discovery_state == "unavailable":
        discovery_status = "unavailable"
        discovery_description = (
            "The latest discovery run artifacts are unavailable."
        )
    else:
        discovery_status = "unknown"
        discovery_description = "No discovery run has been recorded."

    return [
        system_health.HealthComponent(
            "api",
            "Helios API",
            "healthy",
            "The authorized status API responded.",
        ),
        system_health.HealthComponent(
            "semantic-model",
            "Semantic model",
            semantic_status,
            semantic_description,
        ),
        system_health.HealthComponent(
            "discovery-profile",
            "Discovery and profiling",
            discovery_status,
            discovery_description,
        ),
    ]


def _profile_details(model: Model, details: dict) -> dict:
    dataset_identity = details.get("dataset_physical_identity")
    if dataset_identity is None and details.get("physical_name") is None:
        dataset_identity = details.get("physical_identity")
    if not dataset_identity:
        return {}

    profile = None
    for run_id in reversed(model.discovery_run_ids):
        profile = runstore.load(run_id, "profile")
        if profile is not None:
            break
    if profile is None:
        return {}
    table = (profile.get("tables") or {}).get(dataset_identity)
    if not isinstance(table, dict):
        return {}
    stats = table.get("stats") or {}
    result = {}
    if stats.get("row_count") is not None:
        result["row_count"] = stats["row_count"]

    physical_name = details.get("physical_name")
    column = (stats.get("columns") or {}).get(physical_name)
    if isinstance(column, dict):
        if column.get("type") is not None:
            result["physical_type"] = column["type"]
        if column.get("null_rate") is not None:
            result["null_percentage"] = column["null_rate"] * 100
        if column.get("ndv") is not None:
            result["distinct_values"] = column["ndv"]
        if column.get("min") is not None:
            result["minimum"] = column["min"]
        if column.get("max") is not None:
            result["maximum"] = column["max"]
    return result


def _proposal_element_ids(proposal: dict, section: str) -> set[str]:
    if section == "datasets":
        return {
            review_store.dataset_id(dataset)
            for dataset in proposal.get("datasets", [])
        }
    if section == "fields":
        return {
            review_store.field_id(dataset, field)
            for dataset in proposal.get("datasets", [])
            for field in dataset.get("fields", [])
        }
    if section == "relationships":
        return {
            review_store.relationship_id(relationship)
            for relationship in proposal.get("relationships", [])
        }
    if section == "metrics":
        return {
            review_store.metric_id(metric)
            for metric in proposal.get("metrics", [])
        }
    if section == "glossary_terms":
        return {
            review_store.term_id(term)
            for term in proposal.get("glossary_terms", [])
        }
    return set()


def _require_model_run(model: Model, run_id: str) -> None:
    if (
        run_id not in set(model.discovery_run_ids)
        or "/" in run_id
        or "\\" in run_id
        or ".." in run_id
    ):
        raise HTTPException(404, "run not found for model")


def _proposal_canvas(section: ReviewSection, item: dict, element_id: str) -> dict:
    if section == "datasets":
        focus_id = f"dataset:{item.get('table')}"
        return {"element_id": focus_id, "focus_node_id": focus_id, "lens": "semantic"}
    if section == "fields":
        focus_id = f"attribute:{item.get('table')}:{item.get('column')}"
        return {"element_id": focus_id, "focus_node_id": focus_id, "lens": "semantic"}
    if section == "metrics":
        focus_id = f"metric:{item.get('name')}"
        return {"element_id": focus_id, "focus_node_id": focus_id, "lens": "semantic"}
    if section == "glossary_terms":
        focus_id = f"concept:{item.get('name')}"
        return {"element_id": focus_id, "focus_node_id": focus_id, "lens": "ontology"}
    source = f"dataset:{item.get('from')}"
    target = f"dataset:{item.get('to')}"
    return {
        "element_id": f"relationship:{source}:{target}:{item.get('from_column', '')}",
        "focus_node_id": source,
        "related_node_ids": [source, target],
        "lens": "semantic",
    }


def _proposal_rows(proposal: dict, section: ReviewSection) -> list[tuple[str, dict]]:
    if section == "datasets":
        return [
            (review_store.dataset_id(dataset), dict(dataset))
            for dataset in proposal.get("datasets") or []
        ]
    if section == "fields":
        return [
            (
                review_store.field_id(dataset, field),
                {"table": dataset.get("table"), **field},
            )
            for dataset in proposal.get("datasets") or []
            for field in dataset.get("fields") or []
        ]
    id_function = {
        "relationships": review_store.relationship_id,
        "metrics": review_store.metric_id,
        "glossary_terms": review_store.term_id,
    }[section]
    return [
        (id_function(item), dict(item))
        for item in proposal.get(section) or []
    ]


def _load_model_review(
    model: Model,
    run_id: str,
) -> tuple[dict, dict, dict, dict, Path]:
    if (
        run_id not in set(model.discovery_run_ids)
        or "/" in run_id
        or ".." in run_id
    ):
        raise HTTPException(status_code=404, detail="review run not found for model")
    run_dir = Path(runstore.RUNS_DIR) / run_id
    proposal = runstore.load(run_id, "propose")
    if not proposal:
        raise HTTPException(status_code=404, detail="proposal not found")
    harvest = runstore.load(run_id, "harvest") or {}
    review_path = run_dir / "review.json"
    return (
        model,
        proposal,
        harvest,
        review_store.load(str(review_path), run_id),
        review_path,
    )


def _proposal_collection(
    *,
    model: Model,
    run_id: str,
    proposal: dict,
    review: dict,
    section: ReviewSection,
    decision: str | None,
    query: str | None,
    offset: int,
    limit: int,
) -> dict:
    rows = _proposal_rows(proposal, section)
    projected = []
    query_text = (query or "").casefold()
    for element_id, item in rows:
        audit = review.get(section, {}).get(element_id)
        item_decision = (audit or {}).get("decision", "pending")
        if decision and item_decision != decision:
            continue
        if query_text and query_text not in json.dumps(
            item, sort_keys=True, default=str
        ).casefold():
            continue
        projected.append(
            {
                "id": element_id,
                "section": section,
                "proposal": item,
                "confidence": item.get("confidence"),
                "provenance": {
                    "source": item.get("source"),
                    "llm": (
                        proposal.get("llm")
                        if isinstance(proposal.get("llm"), dict)
                        else None
                    ),
                },
                "review": {
                    "decision": item_decision,
                    "overrides": (audit or {}).get("overrides"),
                    "note": (audit or {}).get("note"),
                },
                "canvas": {
                    "review_run_id": run_id,
                    **_proposal_canvas(section, item, element_id),
                },
                "available_actions": [
                    "accept",
                    "reject",
                    "edit",
                    "view_in_canvas",
                ],
            }
        )
    total = len(projected)
    return {
        "model_id": model.id,
        "run_id": run_id,
        "section": section,
        "items": projected[offset : offset + limit],
        "page": {
            "offset": offset,
            "limit": limit,
            "returned": len(projected[offset : offset + limit]),
            "total": total,
            "has_more": offset + limit < total,
        },
        "filters": {"decision": decision, "query": query},
        "reviewed_at": review.get("reviewed_at"),
        "reviewed_by": review.get("reviewed_by"),
        "available_actions": [
            "decide",
            "cascade",
            "bulk_accept",
            "reset",
        ],
    }


def _review_section_counts(proposal: dict, review: dict) -> dict[str, dict[str, int]]:
    return review_store.summary(review, proposal)


def _review_summary(
    *,
    model_id: str,
    run_id: str,
    proposal: dict,
    harvest: dict,
    review: dict,
    permissions: list[str],
) -> dict:
    prepared = publication.prepare_reviewed_publication(
        model_id=model_id,
        model_name=publication.model_name_for_proposal(proposal, harvest),
        run_id=run_id,
        proposal=proposal,
        review=review,
    )
    manifest_path = (
        ArtifactStore(runstore.ROOT).directory(model_id, "published")
        / "manifest.json"
    )
    manifest = (
        json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    )
    counts = _review_section_counts(proposal, review)
    actions: list[str] = []
    if "model.edit" in permissions:
        actions.extend(["decide", "cascade", "bulk_accept", "reset"])
    if "model.publish" in permissions:
        actions.append("publish")
    return {
        "model_id": model_id,
        "run_id": run_id,
        "sections": counts,
        "reviewed_at": review.get("reviewed_at"),
        "reviewed_by": review.get("reviewed_by"),
        "preflight_issues": prepared.preflight_issues,
        "validation_errors": list(prepared.errors),
        "publish_ready": not prepared.errors,
        "publication": manifest,
        "available_actions": actions,
    }


def _save_review(
    review_path: Path,
    review: dict,
    context: AuthorizedModel,
) -> dict:
    review_store.save(str(review_path), review, reviewed_by=context.principal.id)
    return review_store.load(str(review_path), review["run_id"])


def _conversation_message(body: ConversationTurnRequest) -> str:
    message = body.message.strip()
    if not message:
        raise HTTPException(422, "message must not be empty")
    return message


def _conversation_title(message: str) -> str:
    title = " ".join(message.split())
    return title if len(title) <= 80 else f"{title[:79].rstrip()}…"


def _conversation_response(
    conversation: StoredConversation,
    *,
    include_messages: bool,
) -> dict:
    response = {
        "id": conversation.id,
        "model_id": conversation.model_id,
        "title": conversation.title,
        "version": conversation.version,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }
    if include_messages:
        response["messages"] = [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at,
            }
            for message in conversation.messages
        ]
    return response


def _audit_event_response(
    event: AuditEvent,
    *,
    include_diagnostics: bool = False,
) -> dict:
    details = dict(event.details or {})
    diagnostics = details.pop("diagnostics", None)
    return {
        "id": event.id,
        "occurred_at": event.occurred_at,
        "request_id": event.request_id,
        "session_id": event.session_id,
        "principal_id": event.principal_id,
        "organization_id": event.organization_id,
        "model_id": event.model_id,
        "component": event.component,
        "event_type": event.event_type,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "outcome": event.outcome,
        "severity": event.severity,
        "http_status": event.http_status,
        "duration_ms": event.duration_ms,
        "summary": event.summary,
        "details": details,
        "diagnostics": (
            diagnostics
            if include_diagnostics and isinstance(diagnostics, dict)
            else None
        ),
    }


def _audit_session_response(session: AuditSession) -> dict:
    return {
        "session_id": session.session_id,
        "principal_id": session.principal_id,
        "first_seen_at": session.first_seen_at,
        "last_seen_at": session.last_seen_at,
        "event_count": session.event_count,
        "organization_id": session.organization_id,
    }


def _can_manage_organization(
    principal: authz.Principal,
    policy: authz.Policy,
    organization_id: str,
) -> bool:
    return policy.can(
        principal,
        authz.Action.ORGANIZATION_MANAGE,
        authz.Resource(
            "organization", organization_id, organization_id
        ),
    ).allowed


@api_router.get("/healthz")
def api_health() -> dict:
    return {"status": "ok"}


@api_router.get("/diagnostics")
def api_diagnostics(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    grants = repository.grants_for_principal(principal.id)
    organization_ids = {grant.organization_id for grant in grants}
    return {
        "status": "ok",
        "principal": {
            "id": principal.id,
            "issuer": principal.issuer,
            "subject": principal.subject,
            "display_name": principal.display_name,
            "kind": principal.kind.value,
        },
        "accessible_organization_count": len(organization_ids),
    }


@api_router.get(
    "/audit/events",
    response_model=AuditEventCollectionResponse,
)
def list_audit_events(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
    organization_id: str | None = None,
    principal_id: str | None = None,
    session_id: str | None = None,
    model_id: str | None = None,
    component: str | None = None,
    event_type: str | None = None,
    outcome: str | None = None,
    severity: str | None = None,
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    include_all: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    normalized_session_id = audit.normalize_correlation_id(session_id)
    if session_id and normalized_session_id is None:
        raise HTTPException(422, "session_id is invalid")
    organization_admin = bool(
        organization_id
        and _can_manage_organization(principal, policy, organization_id)
    )
    if include_all and not organization_admin:
        raise HTTPException(
            403,
            "organization administration permission is required",
        )
    if principal_id and principal_id != principal.id and not organization_admin:
        raise HTTPException(403, "another principal's audit events are unavailable")
    effective_principal = (
        principal_id
        if organization_admin and principal_id
        else None
        if organization_admin and include_all
        else principal.id
    )
    events, total = repository.audit_events(
        principal_id=effective_principal,
        organization_id=organization_id,
        session_id=normalized_session_id,
        model_id=model_id,
        component=component,
        event_type=event_type,
        outcome=outcome,
        severity=severity,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        offset=offset,
        limit=limit,
    )
    actions = ["audit.read"]
    if organization_admin:
        actions.append("audit.read_organization")
    return {
        "items": [_audit_event_response(item) for item in events],
        "page": {
            "offset": offset,
            "limit": limit,
            "returned": len(events),
            "total": total,
            "has_more": offset + len(events) < total,
        },
        "filters": {
            "organization_id": organization_id,
            "principal_id": effective_principal,
            "session_id": session_id,
            "model_id": model_id,
            "component": component,
            "event_type": event_type,
            "outcome": outcome,
            "severity": severity,
            "occurred_from": occurred_from,
            "occurred_to": occurred_to,
            "include_all": include_all,
        },
        "available_actions": actions,
    }


@api_router.get(
    "/audit/events/{event_id}",
    response_model=AuditEventResponse,
)
def get_audit_event(
    event_id: str,
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    event = repository.audit_event(event_id)
    if event is None:
        raise HTTPException(404, "audit event not found")
    own_event = event.principal_id == principal.id
    organization_admin = bool(
        event.organization_id
        and _can_manage_organization(
            principal, policy, event.organization_id
        )
    )
    if not own_event and not organization_admin:
        raise HTTPException(404, "audit event not found")
    return _audit_event_response(
        event,
        include_diagnostics=organization_admin,
    )


@api_router.get(
    "/audit/sessions",
    response_model=AuditSessionCollectionResponse,
)
def list_audit_sessions(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
    organization_id: str | None = None,
    include_all: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    organization_admin = bool(
        organization_id
        and _can_manage_organization(principal, policy, organization_id)
    )
    if include_all and not organization_admin:
        raise HTTPException(
            403,
            "organization administration permission is required",
        )
    sessions = repository.audit_sessions(
        principal_id=None if include_all else principal.id,
        organization_id=organization_id,
        limit=limit,
    )
    actions = ["audit.read"]
    if organization_admin:
        actions.append("audit.read_organization")
    return {
        "sessions": [
            _audit_session_response(session) for session in sessions
        ],
        "available_actions": actions,
    }


@api_router.post("/audit/client-events")
def record_client_audit_event(
    body: AuditClientEventRequest,
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    organization_id = None
    model_id = None
    if body.model_id:
        model = repository.model(body.model_id)
        if model is None:
            raise HTTPException(404, "model not found")
        _require(
            policy,
            principal,
            authz.Action.MODEL_READ,
            authz.Resource(
                "model", model.id, model.organization_id
            ),
        )
        model_id = model.id
        organization_id = model.organization_id
    elif body.resource_type == "organization" and body.resource_id:
        grant_organizations = {
            grant.organization_id
            for grant in repository.grants_for_principal(principal.id)
        }
        if body.resource_id not in grant_organizations:
            raise HTTPException(403, "organization is unavailable")
        organization_id = body.resource_id
    audit.emit(
        repository,
        component="ui",
        event_type="ui.activity",
        action=body.action,
        outcome="success",
        summary=f"UI activity: {body.action}",
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        organization_id=organization_id,
        model_id=model_id,
        details={"path": body.path} if body.path else {},
    )
    return {"ok": True}


@api_router.get("/organizations")
def list_accessible_organizations(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    organization_ids = {
        grant.organization_id
        for grant in repository.grants_for_principal(principal.id)
    }
    organizations = []
    for organization_id in organization_ids:
        organization = repository.organization(organization_id)
        if organization is None:
            continue
        resource = authz.Resource(
            "organization",
            organization.id,
            organization_id=organization.id,
        )
        available_actions = [
            action.value
            for action in (
                authz.Action.ORGANIZATION_READ,
                authz.Action.ORGANIZATION_MANAGE,
            )
            if policy.can(principal, action, resource).allowed
        ]
        organizations.append(
            {
                "id": organization.id,
                "name": organization.name,
                "available_actions": available_actions,
            }
        )
    organizations.sort(key=lambda item: item["name"].lower())
    return {
        "organizations": organizations,
        "count": len(organizations),
    }


@api_router.get("/models")
def list_accessible_models(
    request: Request,
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
    organization_id: str | None = None,
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    candidates: dict[str, Model] = {}
    for grant in repository.grants_for_principal(principal.id):
        if organization_id and grant.organization_id != organization_id:
            continue
        if grant.resource.resource_type == "organization":
            for model in repository.models_for_organization(grant.organization_id):
                candidates[model.id] = model
        elif grant.model_id:
            model = repository.model(grant.model_id)
            if model is not None:
                candidates[model.id] = model

    models = [
        _model_metadata(AuthorizedModel(model, principal, policy))
        for model in candidates.values()
        if policy.can(
            principal,
            authz.Action.MODEL_READ,
            authz.Resource("model", model.id, model.organization_id),
        ).allowed
    ]
    models.sort(key=lambda item: item["name"].lower())
    return {
        "organization_id": organization_id,
        "models": models,
        "count": len(models),
    }


@api_router.get("/organizations/{org_id}")
def get_organization(
    organization: Annotated[
        Organization,
        Depends(authorize_organization(authz.Action.ORGANIZATION_READ)),
    ],
) -> dict:
    return {
        "id": organization.id,
        "name": organization.name,
        "member_ids": list(organization.member_ids),
    }


@api_router.get("/organizations/{org_id}/models")
def list_organization_models(
    organization: Annotated[
        Organization,
        Depends(authorize_organization(authz.Action.ORGANIZATION_READ)),
    ],
    store: Annotated[ResourceStore | MetadataRepository, Depends(resource_store)],
    principal: Annotated[authz.Principal, Depends(current_principal)],
    policy: Annotated[authz.Policy, Depends(authorization_policy)],
) -> dict:
    return {
        "organization_id": organization.id,
        "models": [
            _model_metadata(AuthorizedModel(model, principal, policy))
            for model in store.models_for_organization(organization.id)
        ],
    }


@api_router.get("/models/{model_id}")
def get_model(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
) -> dict:
    return _model_metadata(context)


@api_router.get("/models/{model_id}/overview")
def get_model_overview(
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    graphs: Annotated[GraphRepository, Depends(graph_repository)],
) -> dict:
    return _model_overview(
        context,
        request.app.state.metadata_repository,
        graphs,
    )


@api_router.get(
    "/models/{model_id}/conversations",
    response_model=ConversationCollectionResponse,
)
def list_model_conversations(
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    conversations = repository.conversations_for_principal(
        context.model.id, context.principal.id
    )
    return {
        "model_id": context.model.id,
        "conversations": [
            _conversation_response(item, include_messages=False)
            for item in conversations
        ],
    }


@api_router.post(
    "/models/{model_id}/conversations",
    response_model=PersistedConversationTurnResponse,
)
async def create_model_conversation(
    body: ConversationTurnRequest,
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    service: Annotated[
        ConversationService,
        Depends(conversation_service),
    ],
) -> dict:
    message = _conversation_message(body)
    try:
        turn = await service.turn(
            context.principal,
            context.model.organization_id,
            context.model.id,
            message,
            history=[],
        )
    except ConversationUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    repository: MetadataRepository = request.app.state.metadata_repository
    conversation = repository.create_conversation(
        context.model.id,
        context.principal.id,
        _conversation_title(message),
        message,
        turn["answer"],
    )
    return {
        "conversation": _conversation_response(
            conversation, include_messages=True
        ),
        "turn": turn,
    }


@api_router.get(
    "/models/{model_id}/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
)
def get_model_conversation(
    conversation_id: str,
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
) -> dict:
    repository: MetadataRepository = request.app.state.metadata_repository
    conversation = repository.conversation_for_principal(
        conversation_id,
        context.model.id,
        context.principal.id,
    )
    if conversation is None:
        raise HTTPException(404, "conversation not found")
    return _conversation_response(conversation, include_messages=True)


@api_router.post(
    "/models/{model_id}/conversations/{conversation_id}/turns",
    response_model=PersistedConversationTurnResponse,
)
async def append_model_conversation_turn(
    conversation_id: str,
    body: PersistedConversationTurnRequest,
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    service: Annotated[
        ConversationService,
        Depends(conversation_service),
    ],
) -> dict:
    message = _conversation_message(body)
    repository: MetadataRepository = request.app.state.metadata_repository
    conversation = repository.conversation_for_principal(
        conversation_id,
        context.model.id,
        context.principal.id,
    )
    if conversation is None:
        raise HTTPException(404, "conversation not found")
    if conversation.version != body.expected_version:
        raise HTTPException(
            409,
            "conversation changed; reload before sending another message",
        )
    history = [
        {"role": item.role, "content": item.content}
        for item in conversation.messages
    ]
    try:
        turn = await service.turn(
            context.principal,
            context.model.organization_id,
            context.model.id,
            message,
            history=history,
        )
        updated = repository.append_conversation_turn(
            conversation.id,
            context.model.id,
            context.principal.id,
            body.expected_version,
            message,
            turn["answer"],
        )
    except ConversationVersionConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConversationUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "conversation": _conversation_response(
            updated, include_messages=True
        ),
        "turn": turn,
    }


@api_router.post(
    "/models/{model_id}/conversation/turns",
    response_model=ConversationTurnResponse,
)
async def create_model_conversation_turn(
    body: ConversationTurnRequest,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    service: Annotated[
        ConversationService,
        Depends(conversation_service),
    ],
) -> dict:
    message = _conversation_message(body)
    try:
        return await service.turn(
            context.principal,
            context.model.organization_id,
            context.model.id,
            message,
        )
    except ConversationUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc


@api_router.get("/models/{model_id}/status")
def get_model_status(
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    graphs: Annotated[GraphRepository, Depends(graph_repository)],
    details: bool = False,
) -> dict:
    metadata: MetadataRepository = request.app.state.metadata_repository
    overview = _model_overview(context, metadata, graphs)
    components = _model_status_components(overview)
    details_available = "organization.manage" in context.available_actions
    infrastructure: list[system_health.HealthComponent] = []
    if details:
        if not details_available:
            raise HTTPException(
                403,
                "organization administration permission is required",
            )
        checker = getattr(
            request.app.state,
            "system_health_checker",
            system_health.collect_infrastructure_status,
        )
        infrastructure = list(checker(metadata))

    all_components = components + infrastructure
    issues = [
        component.description
        for component in all_components
        if component.status in {"degraded", "unavailable"}
    ]
    return {
        "model_id": context.model.id,
        "status": system_health.aggregate_status(all_components),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "components": [
            component.to_dict() for component in components
        ],
        "details_available": details_available,
        "details": [
            component.to_dict() for component in infrastructure
        ] if details else [],
        "recent_activity": {
            "discovery": _latest_successful_run(
                context.model, "harvest"
            ),
            "profile": _latest_successful_run(context.model, "profile"),
        },
        "issues": issues,
    }


@api_router.get("/models/{model_id}/graph")
def get_model_graph(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    repository: Annotated[GraphRepository, Depends(graph_repository)],
    navigation: bool = False,
    lens: Annotated[
        str | None,
        Query(pattern="^(physical|semantic|ontology)$"),
    ] = None,
    review_run_id: Annotated[str | None, Query(max_length=200)] = None,
    focus_node_id: str | None = None,
    depth: Annotated[int, Query(ge=0, le=3)] = 1,
    include_attributes: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    edge_limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    query: Annotated[str | None, Query(max_length=200)] = None,
) -> dict:
    if review_run_id:
        _require(
            context.policy,
            context.principal,
            authz.Action.MODEL_EDIT,
            _resource_for_action(context.model, authz.Action.MODEL_EDIT),
        )
        review_loader = getattr(repository, "review_graph_for_model", None)
        if not callable(review_loader):
            raise HTTPException(404, "review graph not available")
        try:
            graph = review_loader(context.model, review_run_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "review run not found") from exc
    else:
        graph = repository.graph_for_model(context.model)
    try:
        authorized = authorize_graph(
            graph, context.model, context.principal, context.policy
        )
    except ValueError as exc:
        raise HTTPException(404, "model graph not found") from exc
    if navigation or lens or focus_node_id or query:
        try:
            return navigation_graph_response(
                authorized,
                lens=lens,
                focus_node_id=focus_node_id,
                depth=depth,
                include_attributes=include_attributes,
                limit=limit,
                edge_limit=edge_limit,
                query=query,
            )
        except KeyError as exc:
            raise HTTPException(404, "graph node not found") from exc
    return graph_response(authorized)


@api_router.get("/models/{model_id}/graph/detail")
def get_model_graph_detail(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    repository: Annotated[GraphRepository, Depends(graph_repository)],
    element_id: Annotated[str, Query(min_length=1, max_length=500)],
    review_run_id: Annotated[str | None, Query(max_length=200)] = None,
) -> dict:
    if review_run_id:
        _require(
            context.policy,
            context.principal,
            authz.Action.MODEL_EDIT,
            _resource_for_action(context.model, authz.Action.MODEL_EDIT),
        )
        review_graph_loader = getattr(
            repository, "review_graph_for_model", None
        )
        if not callable(review_graph_loader):
            raise HTTPException(404, "review graph not available")
        try:
            source_graph = review_graph_loader(
                context.model, review_run_id
            )
        except FileNotFoundError as exc:
            raise HTTPException(404, "review run not found") from exc
    else:
        source_graph = repository.graph_for_model(context.model)
    graph = authorize_graph(
        source_graph,
        context.model,
        context.principal,
        context.policy,
    )
    element: GraphNode | GraphEdge | None = next(
        (node for node in graph.nodes if node.id == element_id),
        None,
    )
    element_type = "node"
    if element is None:
        element = next(
            (edge for edge in graph.edges if edge.id == element_id),
            None,
        )
        element_type = "edge"
    if element is None:
        raise HTTPException(404, "graph element not found")

    detail_loader = getattr(
        repository,
        (
            "review_detail_for_element"
            if review_run_id
            else "detail_for_element"
        ),
        None,
    )
    details = (
        dict(
            detail_loader(context.model, review_run_id, element_id)
            if review_run_id
            else detail_loader(context.model, element_id)
        )
        if callable(detail_loader)
        else {}
    )
    details.update(
        {
            key: value
            for key, value in element.metadata.items()
            if key
            in {
                "review_section",
                "review_element_id",
                "review_decision",
                "review_note",
                "review_overrides",
                "reviewed_at",
                "reviewed_by",
            }
            and value is not None
        }
    )
    dataset_identity = (
        details.get("dataset_physical_identity")
        or (
            details.get("physical_identity")
            if isinstance(element, GraphNode) and element.kind == "dataset"
            else None
        )
    )
    if isinstance(dataset_identity, str):
        parts = dataset_identity.split(".")
        if len(parts) >= 2:
            details.setdefault("schema", parts[-2])
        if len(parts) >= 3:
            details.setdefault("catalog", ".".join(parts[:-2]))
    details.update(_profile_details(context.model, details))
    if isinstance(element, GraphNode) and element.kind == "dataset":
        details["relationship_count"] = sum(
            edge.source == element.id or edge.target == element.id
            for edge in graph.edges
            if edge.kind
            in {
                "physical_relationship",
                "inferred_relationship",
                "semantic_relationship",
                "ontology_relationship",
            }
            and not edge.id.startswith("contains:")
        )
    if isinstance(element, GraphNode) and context.model.data_sources:
        details["data_source_ids"] = [
            reference.data_source_id
            for reference in context.model.data_sources
        ]

    return {
        "element_type": element_type,
        "id": element.id,
        "kind": element.kind,
        "label": element.label if isinstance(element, GraphNode) else None,
        "source": element.source if isinstance(element, GraphEdge) else None,
        "target": element.target if isinstance(element, GraphEdge) else None,
        "status": element.status,
        "confidence": element.confidence,
        "evidence": element.evidence,
        "details": details,
        "available_actions": list(element.permitted_actions),
    }


@api_router.get("/models/{model_id}/reviews/{run_id}")
def get_model_review(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_EDIT)),
    ],
    run_id: str,
) -> dict:
    _, proposal, harvest, review, _ = _load_model_review(
        context.model, run_id
    )
    return _review_summary(
        model_id=context.model.id,
        run_id=run_id,
        proposal=proposal,
        harvest=harvest,
        review=review,
        permissions=context.available_actions,
    )


@api_router.post("/models/{model_id}/reviews/{run_id}/decisions")
def decide_model_proposal(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_EDIT)),
    ],
    body: ReviewDecisionRequest,
    run_id: str,
) -> dict:
    _, proposal, harvest, review, review_path = _load_model_review(
        context.model, run_id
    )
    if body.element_id not in _proposal_element_ids(proposal, body.section):
        raise HTTPException(404, "proposal element not found")

    review_store.decide(
        review,
        body.section,
        body.element_id,
        body.decision,
        body.overrides,
        body.note,
    )
    review = _save_review(review_path, review, context)
    entry = review[body.section][body.element_id]
    return {
        "ok": True,
        "run_id": run_id,
        "section": body.section,
        "element_id": body.element_id,
        "entry": entry,
        "reviewed_at": review["reviewed_at"],
        "reviewed_by": review["reviewed_by"],
        "summary": _review_summary(
            model_id=context.model.id,
            run_id=run_id,
            proposal=proposal,
            harvest=harvest,
            review=review,
            permissions=context.available_actions,
        ),
    }


@api_router.post("/models/{model_id}/reviews/{run_id}/decisions/dataset")
def decide_model_dataset(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_EDIT)),
    ],
    body: DatasetReviewDecisionRequest,
    run_id: str,
) -> dict:
    _, proposal, harvest, review, review_path = _load_model_review(
        context.model, run_id
    )
    try:
        changed = review_store.cascade_dataset(
            review, proposal, body.table, body.decision
        )
    except KeyError:
        raise HTTPException(404, "proposal dataset not found")
    review = _save_review(review_path, review, context)
    return {
        "ok": True,
        "changed": changed,
        "summary": _review_summary(
            model_id=context.model.id,
            run_id=run_id,
            proposal=proposal,
            harvest=harvest,
            review=review,
            permissions=context.available_actions,
        ),
    }


@api_router.post("/models/{model_id}/reviews/{run_id}/decisions/bulk")
def bulk_accept_model_proposals(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_EDIT)),
    ],
    body: BulkReviewDecisionRequest,
    run_id: str,
) -> dict:
    _, proposal, harvest, review, review_path = _load_model_review(
        context.model, run_id
    )
    changed = review_store.bulk_accept(review, proposal, body.min_confidence)
    review = _save_review(review_path, review, context)
    return {
        "ok": True,
        "changed": changed,
        "summary": _review_summary(
            model_id=context.model.id,
            run_id=run_id,
            proposal=proposal,
            harvest=harvest,
            review=review,
            permissions=context.available_actions,
        ),
    }


@api_router.post("/models/{model_id}/reviews/{run_id}/reset")
def reset_model_review(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_EDIT)),
    ],
    body: ResetReviewRequest,
    run_id: str,
) -> dict:
    _, proposal, harvest, review, review_path = _load_model_review(
        context.model, run_id
    )
    review_store.clear(review, body.section)
    review = _save_review(review_path, review, context)
    return {
        "ok": True,
        "summary": _review_summary(
            model_id=context.model.id,
            run_id=run_id,
            proposal=proposal,
            harvest=harvest,
            review=review,
            permissions=context.available_actions,
        ),
    }


@api_router.post("/models/{model_id}/reviews/{run_id}/publish")
def publish_model_review(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_PUBLISH)),
    ],
    run_id: str,
) -> dict:
    _, proposal, harvest, review, _ = _load_model_review(
        context.model, run_id
    )
    try:
        result = publication.publish_reviewed_proposal(
            ArtifactStore(runstore.ROOT),
            model_id=context.model.id,
            model_name=publication.model_name_for_proposal(proposal, harvest),
            run_id=run_id,
            proposal=proposal,
            review=review,
        )
    except publication.PublicationValidationError as exc:
        raise HTTPException(
            422,
            {
                "code": "publication_validation_failed",
                "errors": list(exc.errors),
            },
        )
    return {
        "ok": True,
        "model_id": context.model.id,
        "run_id": run_id,
        "manifest": result.manifest,
    }


def _atlas_request(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except AtlasError as exc:
        status = exc.status if exc.status in {400, 404, 409, 422} else 502
        raise HTTPException(status, str(exc)) from exc


def _glossary_document(context: AuthorizedModel, atlas: AtlasClient) -> dict:
    glossary_id = context.model.glossary_id
    if not glossary_id:
        raise HTTPException(404, "this model does not have a glossary")
    document = _atlas_request(lambda: atlas.get_glossary(glossary_id))
    if document.get("guid") not in {None, glossary_id}:
        raise HTTPException(502, "Atlas returned an unexpected glossary")
    return document


def _term_document(
    context: AuthorizedModel,
    atlas: AtlasClient,
    term_id: str,
) -> dict:
    glossary = _glossary_document(context, atlas)
    term = _atlas_request(lambda: atlas.get_term(term_id))
    anchor = term.get("anchor") or {}
    if anchor.get("glossaryGuid") != glossary.get("guid"):
        raise HTTPException(404, "glossary term not found")
    return term


def _term_summary(term: dict) -> dict:
    return {
        "id": term.get("guid"),
        "name": term.get("name") or "",
        "definition": term.get("shortDescription") or "",
        "long_description": term.get("longDescription") or "",
        "abbreviation": term.get("abbreviation") or "",
        "examples": list(term.get("examples") or []),
        "status": "published",
        "confidence": None,
        "evidence": [],
    }


def _authorized_attribute_assets(
    context: AuthorizedModel,
    graphs: GraphRepository,
) -> dict[str, dict[str, Any]]:
    graph = authorize_graph(
        graphs.graph_for_model(context.model),
        context.model,
        context.principal,
        context.policy,
    )
    nodes = {node.id: node for node in graph.nodes}
    parents = {
        edge.target: edge.source
        for edge in graph.edges
        if edge.kind in {"physical", "physical_relationship"}
    }
    assets: dict[str, dict[str, Any]] = {}
    for node in graph.nodes:
        if node.kind != "attribute":
            continue
        dataset = nodes.get(parents.get(node.id, ""))
        source = dataset.metadata.get("physical_name") if dataset else None
        column = node.metadata.get("physical_name")
        if not isinstance(source, str) or not isinstance(column, str):
            continue
        physical_identity = f"{source}.{column}".split("@", 1)[0]
        if len(physical_identity.split(".")) != 3:
            continue
        assets[node.id] = {
            "node": node,
            "dataset": dataset,
            "physical_identity": physical_identity,
        }
    return assets


def _require_datasource_read(context: AuthorizedModel) -> None:
    _require(
        context.policy,
        context.principal,
        authz.Action.DATASOURCE_READ,
        _resource_for_action(context.model, authz.Action.DATASOURCE_READ),
    )


def _save_model_glossary(
    request: Request,
    context: AuthorizedModel,
    glossary_id: str | None,
) -> Model:
    repository: MetadataRepository = request.app.state.metadata_repository
    stored = repository.stored_model(context.model.id)
    if stored is None:
        raise HTTPException(404, "model not found")
    updated = replace(stored.model, glossary_id=glossary_id)
    repository.save_model(updated, stored.created_by, stored.status)
    return updated


@api_router.get("/models/{model_id}/glossary")
def get_model_glossary(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_READ, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
) -> dict:
    if not context.model.glossary_id:
        return {
            "model_id": context.model.id,
            "glossary_id": None,
            "glossary": None,
            "available_actions": context.available_actions,
        }
    glossary = _glossary_document(context, atlas)
    terms, truncated = _all_glossary_terms(atlas, glossary["guid"])
    return {
        "model_id": context.model.id,
        "glossary_id": glossary["guid"],
        "glossary": {
            "id": glossary.get("guid"),
            "name": glossary.get("name") or "",
            "description": glossary.get("shortDescription") or "",
            "term_count": len(terms),
            "term_count_truncated": truncated,
        },
        "available_actions": context.available_actions,
    }


@api_router.post("/models/{model_id}/glossary", status_code=201)
def create_model_glossary(
    request: Request,
    body: GlossaryCreateRequest,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
) -> dict:
    if context.model.glossary_id:
        raise HTTPException(409, "this model already has a glossary")
    glossary = _atlas_request(
        lambda: atlas.create_glossary(body.name.strip(), body.description.strip())
    )
    glossary_id = glossary.get("guid")
    if not glossary_id:
        raise HTTPException(502, "Atlas did not return a glossary identifier")
    try:
        _save_model_glossary(request, context, glossary_id)
    except Exception:
        try:
            atlas.delete_glossary(glossary_id)
        except AtlasError:
            pass
        raise
    return {
        "model_id": context.model.id,
        "glossary_id": glossary_id,
        "glossary": {
            "id": glossary_id,
            "name": glossary.get("name") or body.name.strip(),
            "description": glossary.get("shortDescription")
            or body.description.strip(),
            "term_count": 0,
        },
        "available_actions": context.available_actions,
    }


@api_router.delete("/models/{model_id}/glossary")
def delete_model_glossary(
    request: Request,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
    confirm: bool = False,
) -> dict:
    glossary = _glossary_document(context, atlas)
    if not confirm:
        terms, truncated = _all_glossary_terms(atlas, glossary["guid"])
        raise HTTPException(
            409,
            {
                "code": "confirmation_required",
                "term_count": len(terms),
                "term_count_truncated": truncated,
            },
        )
    glossary_id = context.model.glossary_id
    _atlas_request(lambda: atlas.delete_glossary(glossary_id))
    _save_model_glossary(request, context, None)
    return {"ok": True, "model_id": context.model.id}


def _all_glossary_terms(atlas: AtlasClient, glossary_id: str) -> tuple[list[dict], bool]:
    terms: list[dict] = []
    page_size = 500
    maximum = 10_000
    while len(terms) < maximum:
        page = _atlas_request(
            lambda: atlas.list_terms(glossary_id, page_size, len(terms))
        )
        terms.extend(page)
        if len(page) < page_size:
            return terms, False
    return terms, True


@api_router.get("/models/{model_id}/glossary/terms")
def list_model_glossary_terms(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_READ, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
    query: str = Query(default="", max_length=200),
    status: Literal["published"] | None = None,
    sort: Literal["name", "definition", "abbreviation"] = "name",
    direction: Literal["asc", "desc"] = "asc",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    glossary = _glossary_document(context, atlas)
    terms, truncated = _all_glossary_terms(atlas, glossary["guid"])
    needle = query.strip().casefold()
    if needle:
        terms = [
            term
            for term in terms
            if needle in (term.get("name") or "").casefold()
            or needle in (term.get("shortDescription") or "").casefold()
            or needle in (term.get("longDescription") or "").casefold()
        ]
    if status and status != "published":
        terms = []
    sort_field = {
        "name": "name",
        "definition": "shortDescription",
        "abbreviation": "abbreviation",
    }[sort]
    terms.sort(
        key=lambda term: (term.get(sort_field) or "").casefold(),
        reverse=direction == "desc",
    )
    total = len(terms)
    return {
        "model_id": context.model.id,
        "glossary_id": glossary["guid"],
        "items": [_term_summary(term) for term in terms[offset : offset + limit]],
        "offset": offset,
        "limit": limit,
        "total": total,
        "truncated": truncated,
        "available_actions": context.available_actions,
    }


@api_router.post("/models/{model_id}/glossary/terms", status_code=201)
def create_model_glossary_term(
    body: GlossaryTermWriteRequest,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
) -> dict:
    glossary = _glossary_document(context, atlas)
    term = _atlas_request(
        lambda: atlas.create_term(
            glossary["guid"],
            body.name.strip(),
            body.definition.strip(),
            body.long_description.strip(),
            body.abbreviation.strip(),
            [example.strip() for example in body.examples if example.strip()],
        )
    )
    return {
        "term": _term_summary(term),
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/glossary/terms/{term_id}")
def get_model_glossary_term(
    term_id: str,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_READ, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
    graphs: Annotated[GraphRepository, Depends(graph_repository)],
) -> dict:
    term = _term_document(context, atlas, term_id)
    authorized_assets = _authorized_attribute_assets(context, graphs)
    canvas_by_physical = {
        item["physical_identity"]: element_id
        for element_id, item in authorized_assets.items()
    }
    assignments = []
    for assignment in _atlas_request(lambda: atlas.assigned_entities(term_id)):
        physical_identity = (assignment.get("displayText") or "").split("@", 1)[0]
        canvas_id = canvas_by_physical.get(physical_identity)
        if not canvas_id:
            continue
        node = authorized_assets[canvas_id]["node"]
        assignments.append(
            {
                "id": assignment.get("guid"),
                "name": assignment.get("displayText") or node.label,
                "type": assignment.get("typeName") or "attribute",
                "canvas_element_id": canvas_id,
                "canvas_lens": "physical",
            }
        )
    return {
        "term": {**_term_summary(term), "assignments": assignments},
        "available_actions": context.available_actions,
    }


@api_router.patch("/models/{model_id}/glossary/terms/{term_id}")
def update_model_glossary_term(
    term_id: str,
    body: GlossaryTermWriteRequest,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
) -> dict:
    _term_document(context, atlas, term_id)
    term = _atlas_request(
        lambda: atlas.update_term(
            term_id,
            name=body.name.strip(),
            shortDescription=body.definition.strip(),
            longDescription=body.long_description.strip(),
            abbreviation=body.abbreviation.strip(),
            examples=[
                example.strip() for example in body.examples if example.strip()
            ],
        )
    )
    return {
        "term": _term_summary(term),
        "available_actions": context.available_actions,
    }


@api_router.delete("/models/{model_id}/glossary/terms/{term_id}")
def delete_model_glossary_term(
    term_id: str,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
) -> dict:
    _term_document(context, atlas, term_id)
    _atlas_request(lambda: atlas.delete_term(term_id))
    return {"ok": True, "term_id": term_id}


@api_router.post("/models/{model_id}/glossary/import")
async def import_model_glossary_terms(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
    file: UploadFile = File(...),
) -> dict:
    glossary = _glossary_document(context, atlas)
    content = await file.read(5_000_001)
    if len(content) > 5_000_000:
        raise HTTPException(413, "glossary CSV must be 5 MB or smaller")
    try:
        text = content.decode("utf-8-sig")
        rows = list(csv.DictReader(StringIO(text)))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(422, "glossary import must be a valid UTF-8 CSV") from exc
    if not rows:
        raise HTTPException(422, "glossary import contains no terms")
    glossary_names = {
        (row.get("GlossaryName") or "").strip() for row in rows
    }
    if glossary_names != {glossary.get("name") or ""}:
        raise HTTPException(
            422,
            "every imported row must name the model's linked glossary",
        )
    result = _atlas_request(
        lambda: atlas.import_csv_bytes(file.filename or "glossary.csv", content)
    )
    return {
        "ok": True,
        "imported": len((result or {}).get("successImportInfoList", [])),
        "failed": len((result or {}).get("failedImportInfoList", [])),
    }


@api_router.get("/models/{model_id}/glossary/assignable-assets")
def list_model_glossary_assignable_assets(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    graphs: Annotated[GraphRepository, Depends(graph_repository)],
    query: str = Query(default="", max_length=200),
) -> dict:
    _require_datasource_read(context)
    needle = query.strip().casefold()
    assets = _authorized_attribute_assets(context, graphs)
    items = [
        {
            "id": element_id,
            "name": item["node"].label,
            "dataset_id": item["dataset"].id if item["dataset"] else None,
        }
        for element_id, item in assets.items()
        if not needle
        or needle in item["node"].label.casefold()
        or needle in element_id.casefold()
        or needle in item["physical_identity"].casefold()
    ]
    return {"items": sorted(items, key=lambda item: item["name"].casefold())[:200]}


@api_router.post(
    "/models/{model_id}/glossary/terms/{term_id}/assignments",
    status_code=201,
)
def assign_model_glossary_term(
    term_id: str,
    body: GlossaryAssignmentRequest,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
    graphs: Annotated[GraphRepository, Depends(graph_repository)],
) -> dict:
    _require_datasource_read(context)
    _term_document(context, atlas, term_id)
    assets = _authorized_attribute_assets(context, graphs)
    asset = assets.get(body.canvas_element_id)
    if asset is None:
        raise HTTPException(404, "authorized glossary assignment target not found")
    database, table_name, column = asset["physical_identity"].split(".", 2)
    hit = _atlas_request(lambda: atlas.find_column(database, table_name, column))
    if hit is None:
        raise HTTPException(404, "the selected attribute is not registered in Atlas")
    _atlas_request(lambda: atlas.assign(term_id, [hit]))
    return {"ok": True}


@api_router.delete(
    "/models/{model_id}/glossary/terms/{term_id}/assignments/{assignment_id}"
)
def unassign_model_glossary_term(
    term_id: str,
    assignment_id: str,
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.GLOSSARY_EDIT, "glossary")),
    ],
    atlas: Annotated[AtlasClient, Depends(atlas_client)],
    graphs: Annotated[GraphRepository, Depends(graph_repository)],
) -> dict:
    _require_datasource_read(context)
    _term_document(context, atlas, term_id)
    authorized_assets = _authorized_attribute_assets(context, graphs)
    authorized_physical = {
        item["physical_identity"] for item in authorized_assets.values()
    }
    assignment = next(
        (
            item
            for item in _atlas_request(lambda: atlas.assigned_entities(term_id))
            if item.get("guid") == assignment_id
        ),
        None,
    )
    if assignment is None:
        raise HTTPException(404, "glossary assignment not found")
    physical_identity = (assignment.get("displayText") or "").split("@", 1)[0]
    if physical_identity not in authorized_physical:
        raise HTTPException(404, "authorized glossary assignment not found")
    _atlas_request(lambda: atlas.unassign(term_id, assignment_id))
    return {"ok": True}


@api_router.get("/models/{model_id}/semantic")
def get_model_semantic(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.SEMANTIC_READ, "semantic")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "semantic_model_id": context.model.semantic_model_id,
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/ontology")
def get_model_ontology(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.ONTOLOGY_READ, "ontology")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "ontology_id": context.model.ontology_id,
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/runs", response_model=RunCollectionDTO)
def get_model_runs(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
) -> dict:
    known = {run["id"]: run for run in runstore.list_runs()}
    return {
        "model_id": context.model.id,
        "runs": [
            {
                **known.get(
                    run_id,
                    {
                        "id": run_id,
                        "type": "discovery",
                        "model_id": context.model.id,
                        "status": None,
                        "progress": None,
                        "stages": {},
                        "phases": [],
                        "counts": {
                            "discovered": {},
                            "profiled": {},
                            "proposed": {},
                        },
                        "warnings": [],
                        "errors": [],
                        "missing": True,
                    },
                ),
                "model_id": context.model.id,
            }
            for run_id in context.model.discovery_run_ids
        ],
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/runs/{run_id}", response_model=RunDTO)
def get_model_run(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    run_id: str,
) -> dict:
    _require_model_run(context.model, run_id)
    run = runstore.detail(run_id)
    if not any(run["stages"].values()):
        raise HTTPException(404, "run artifacts not found")
    return {
        **run,
        "model_id": context.model.id,
        "available_actions": context.available_actions,
    }


@api_router.get("/models/{model_id}/runs/{run_id}/profile")
def get_model_run_profile_summary(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    run_id: str,
) -> dict:
    _require_model_run(context.model, run_id)
    _require(
        context.policy,
        context.principal,
        authz.Action.DATASOURCE_READ,
        _resource_for_action(context.model, authz.Action.DATASOURCE_READ),
    )
    summary = runstore.profile_summary(run_id)
    if summary is None:
        raise HTTPException(404, "run profile summary not found")
    return {
        "model_id": context.model.id,
        **summary,
        "provenance": {
            "artifacts": ["harvest", "profile"],
            "run_id": run_id,
        },
        "available_actions": ["view_table_profile"],
    }


@api_router.get("/models/{model_id}/runs/{run_id}/profile/tables/{table_id:path}")
def get_model_run_table_profile(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_READ)),
    ],
    run_id: str,
    table_id: str,
) -> dict:
    _require_model_run(context.model, run_id)
    _require(
        context.policy,
        context.principal,
        authz.Action.DATASOURCE_READ,
        _resource_for_action(context.model, authz.Action.DATASOURCE_READ),
    )
    table = runstore.table_profile(run_id, table_id)
    if table is None:
        raise HTTPException(404, "table profile not found")
    return {
        "model_id": context.model.id,
        **table,
        "provenance": {
            "artifact": "profile",
            "run_id": run_id,
            "table_id": table_id,
        },
        "canvas": {
            "element_id": f"dataset:{table_id}",
            "focus_node_id": f"dataset:{table_id}",
            "lens": "physical",
        },
        "available_actions": ["view_in_canvas"],
    }


@api_router.get("/models/{model_id}/runs/{run_id}/proposals")
def get_model_run_proposals(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.MODEL_EDIT)),
    ],
    run_id: str,
    section: ReviewSection,
    decision: Annotated[
        Literal["pending", "accept", "reject", "edit"] | None,
        Query(),
    ] = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    _require_model_run(context.model, run_id)
    _, proposal, _, review, _ = _load_model_review(context.model, run_id)
    return _proposal_collection(
        model=context.model,
        run_id=run_id,
        proposal=proposal,
        review=review,
        section=section,
        decision=decision,
        query=query,
        offset=offset,
        limit=limit,
    )


@api_router.get("/models/{model_id}/versions")
def get_model_versions(
    context: Annotated[
        AuthorizedModel,
        Depends(authorize_model(authz.Action.SEMANTIC_READ, "semantic")),
    ],
) -> dict:
    return {
        "model_id": context.model.id,
        "versions": list(context.model.version_ids),
        "available_actions": context.available_actions,
    }
