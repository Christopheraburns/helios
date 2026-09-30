"""Review API (task R-03, ADR 0001: stage B).

- GET  /v1/review/datasets/{dataset_id}          review summary + per-artifact status
- GET  /v1/review/artifacts/{artifact_id}        everything a reviewer needs for one
                                                 artifact: preview, story and facts,
                                                 hidden ground truth, marks
- POST /v1/review/artifacts/{artifact_id}/marks  accept / flag / comment (advisory)
- POST /v1/review/datasets/{dataset_id}:approve  IN_REVIEW -> READY (R-06); supersedes
                                                 the other READY datasets in the lineage
- POST /v1/review/datasets/{dataset_id}:reject   IN_REVIEW -> REJECTED, with a reason
- GET  /v1/review/datasets/{dataset_id}/history  audit trail (R-07)

Marks, approvals and rejections record the authenticated Workbench user and are
refused without one.

Shows hidden ground truth: generation project only.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from helios_ds.audit import dataset_history
from helios_ds.lifecycle import DatasetLifecycle, DatasetState, InvalidTransition, ValidationFailed
from helios_ds.manifests import manifest_key
from helios_ds.review import InvalidMark, summarize

from .datasets import (
    ArtifactPreview,
    DatasetSummary,
    _artifact,
    _info,
    _manifest,
    _scenario_index,
    _summary,
    preview_artifact,
)
from .identity import require_user
from .service import GenerationService, get_service

router = APIRouter(prefix="/review")


class ReviewItem(BaseModel):
    artifact_id: str
    artifact_type: str
    template_id: str
    scenario_id: str
    headline: Optional[str]
    status: str
    comment_count: int
    last_mark_at: Optional[str]


class LineageMember(BaseModel):
    dataset_id: str
    state: Optional[str]
    created_at: Optional[str]


class DatasetReview(BaseModel):
    dataset: DatasetSummary
    counts: Dict[str, int]
    items: List[ReviewItem]
    # Other datasets generated from the same config; approving this one
    # supersedes those that are READY.
    lineage: List[LineageMember] = []


class DecisionRequest(BaseModel):
    note: Optional[str] = None  # approval note (optional) or rejection reason (required)


class DecisionResult(BaseModel):
    dataset: DatasetSummary
    superseded: List[str] = []


class AuditEventView(BaseModel):
    occurred_at: str
    kind: str
    action: str
    actor: str
    dataset_id: str
    artifact_id: Optional[str] = None
    note: Optional[str] = None
    related_dataset_id: Optional[str] = None
    event_seq: Optional[int] = None
    review_counts: Optional[Dict[str, int]] = None


class MentionView(BaseModel):
    mention_id: str
    entity_id: str
    entity_type: str
    canonical_name: str
    surface_form: str
    locator: Dict[str, Any]
    difficulty: Optional[str] = None  # direct, alias or contextual (C-05)


class EvidenceView(BaseModel):
    evidence_id: str
    claim_id: str
    claim_type: str
    statement: Optional[str]
    truth_status: str
    excerpt: Optional[str]
    locator: Dict[str, Any]


class RelationshipView(BaseModel):
    predicate: str
    source: str
    target: str
    from_this_artifact: bool


class MarkView(BaseModel):
    mark_id: str
    status: str
    comment: Optional[str]
    reviewer: str
    created_at: str


class ScenarioContext(BaseModel):
    scenario_id: str
    scenario_type: str
    headline: str
    facts: Dict[str, Any]
    source_refs: List[Dict[str, Any]]


class ReviewBundle(BaseModel):
    preview: ArtifactPreview
    scenario: Optional[ScenarioContext]
    mentions: List[MentionView]
    evidence: List[EvidenceView]
    relationships: List[RelationshipView]
    status: str
    marks: List[MarkView]


class MarkRequest(BaseModel):
    status: str  # ACCEPTED, FLAGGED or COMMENT
    comment: Optional[str] = None


def _mark_view(mark: Any) -> "MarkView":
    return MarkView(
        mark_id=mark.mark_id,
        status=mark.status,
        comment=mark.note,
        reviewer=mark.reviewer,
        created_at=mark.created_at,
    )


def _marks(review: Any) -> List[MarkView]:
    return [_mark_view(m) for m in review.marks]


@router.get("/datasets/{dataset_id}", response_model=DatasetReview)
def review_dataset(
    dataset_id: str, service: GenerationService = Depends(get_service)
) -> DatasetReview:
    info = _info(service, dataset_id)
    scenarios = _scenario_index(service, dataset_id)
    reviews = service.reviews.for_dataset(dataset_id)
    artifacts = service.datasets.artifacts(dataset_id)
    items = []
    for record in artifacts:
        review = reviews.get(record.artifact_id)
        items.append(
            ReviewItem(
                artifact_id=record.artifact_id,
                artifact_type=record.artifact_type,
                template_id=record.template_id,
                scenario_id=record.scenario_id,
                headline=scenarios.get(record.scenario_id, (None, None))[1],
                status=review.status if review else "UNREVIEWED",
                comment_count=review.comment_count if review else 0,
                last_mark_at=review.last_mark_at if review else None,
            )
        )
    items.sort(key=lambda i: (i.headline or "", i.artifact_type, i.artifact_id))
    lifecycle = DatasetLifecycle(service.jobs.sink)
    lineage = []
    for other in lifecycle.lineage(dataset_id):
        if other != dataset_id:
            history = lifecycle.history(other)
            lineage.append(
                LineageMember(
                    dataset_id=other,
                    state=history[-1].state if history else None,
                    created_at=history[0].occurred_at if history else None,
                )
            )
    return DatasetReview(
        dataset=_summary(info),
        counts=summarize(reviews, [a.artifact_id for a in artifacts]),
        items=items,
        lineage=sorted(lineage, key=lambda m: m.created_at or "", reverse=True),
    )


@router.post("/datasets/{dataset_id}:approve", response_model=DecisionResult)
def approve_dataset(
    dataset_id: str,
    body: DecisionRequest,
    request: Request,
    service: GenerationService = Depends(get_service),
) -> DecisionResult:
    """Approve (IN_REVIEW -> READY). Blocked only by failed validation, never by flags."""
    approver = require_user(request)
    info = _info(service, dataset_id)
    try:
        store = service.open_store(info.record.manifest_locator, manifest_key(dataset_id))
        approval = DatasetLifecycle(service.jobs.sink).approve(
            dataset_id, store, approver, (body.note or "").strip() or None
        )
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValidationFailed as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "the dataset failed validation", "problems": exc.problems},
        ) from exc
    return DecisionResult(
        dataset=_summary(_info(service, dataset_id)), superseded=approval.superseded
    )


@router.post("/datasets/{dataset_id}:reject", response_model=DecisionResult)
def reject_dataset(
    dataset_id: str,
    body: DecisionRequest,
    request: Request,
    service: GenerationService = Depends(get_service),
) -> DecisionResult:
    """Reject (IN_REVIEW -> REJECTED). The reason says what to fix before regenerating."""
    actor = require_user(request)
    _info(service, dataset_id)
    if not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="a rejection needs a reason")
    try:
        DatasetLifecycle(service.jobs.sink).reject(dataset_id, actor, body.note or "")
    except InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DecisionResult(dataset=_summary(_info(service, dataset_id)))


@router.get("/datasets/{dataset_id}/history", response_model=List[AuditEventView])
def dataset_audit_history(
    dataset_id: str, service: GenerationService = Depends(get_service)
) -> List[AuditEventView]:
    """Every lifecycle event and review mark for the dataset, oldest first."""
    _info(service, dataset_id)
    return [
        AuditEventView.model_validate(e, from_attributes=True)
        for e in dataset_history(service.jobs.sink, dataset_id)
    ]


@router.get("/artifacts/{artifact_id}", response_model=ReviewBundle)
def review_artifact(
    artifact_id: str, service: GenerationService = Depends(get_service)
) -> ReviewBundle:
    record = _artifact(service, artifact_id)
    preview = preview_artifact(artifact_id, service)
    scenario = next(
        (
            s
            for s in _manifest(service, record.dataset_id).scenarios
            if s.scenario_id == record.scenario_id
        ),
        None,
    )
    truth = service.datasets.truth_for_artifact(record)

    def name(entity_id: str) -> str:
        entity = truth.entities.get(entity_id)
        return f"{entity.entity_type}: {entity.canonical_name}" if entity else entity_id

    review = service.reviews.for_artifact(artifact_id)
    return ReviewBundle(
        preview=preview,
        scenario=(
            ScenarioContext(
                scenario_id=scenario.scenario_id,
                scenario_type=scenario.scenario_type,
                headline=preview.artifact.headline or scenario.business_key,
                facts=scenario.facts,
                source_refs=scenario.source_refs,
            )
            if scenario
            else None
        ),
        mentions=[
            MentionView(
                mention_id=m.mention_id,
                entity_id=m.entity_id,
                entity_type=m.entity_type or "",
                canonical_name=truth.entities[m.entity_id].canonical_name
                if m.entity_id in truth.entities
                else m.entity_id,
                surface_form=m.surface_form,
                locator=m.locator,
                difficulty=m.difficulty,
            )
            for m in sorted(truth.mentions, key=lambda m: m.mention_id)
        ],
        evidence=[
            EvidenceView(
                evidence_id=e.evidence_id,
                claim_id=e.claim_id,
                claim_type=truth.claims[e.claim_id].claim_type
                if e.claim_id in truth.claims
                else "?",
                statement=truth.claims[e.claim_id].statement
                if e.claim_id in truth.claims
                else None,
                truth_status=truth.claims[e.claim_id].truth_status
                if e.claim_id in truth.claims
                else "?",
                excerpt=e.excerpt,
                locator=e.locator,
            )
            for e in sorted(truth.evidence, key=lambda e: e.evidence_id)
        ],
        relationships=sorted(
            (
                RelationshipView(
                    predicate=r.predicate,
                    source=name(r.source_entity_id),
                    target=name(r.target_entity_id),
                    from_this_artifact=r.artifact_id == artifact_id,
                )
                for r in truth.relationships
            ),
            key=lambda r: (r.from_this_artifact, r.predicate, r.source, r.target),
        ),
        status=review.status,
        marks=_marks(review),
    )


@router.post(
    "/artifacts/{artifact_id}/marks", status_code=status.HTTP_201_CREATED, response_model=MarkView
)
def add_mark(
    artifact_id: str,
    body: MarkRequest,
    request: Request,
    service: GenerationService = Depends(get_service),
) -> MarkView:
    reviewer = require_user(request)
    record = _artifact(service, artifact_id)
    state = DatasetLifecycle(service.jobs.sink).state(record.dataset_id)
    if state is not DatasetState.IN_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=f"dataset {record.dataset_id} is {state and state.value}, not IN_REVIEW; "
            "review marks are closed once a dataset is decided",
        )
    try:
        mark = service.reviews.add(
            record.dataset_id, artifact_id, body.status, reviewer, body.comment
        )
    except InvalidMark as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _mark_view(mark)
