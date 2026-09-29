"""Dataset and manifest endpoints, plus placeholders for later tasks.

The manifest is served in pieces: an overview without the scenario list, a
paginated scenario list with one-line descriptions, single scenarios, and the
exact bytes for download. The browser never has to load the whole file.

The manifest includes each scenario's type and source facts, which are close to
the answer key: this API must stay inside the generation project, never
reachable by the crawler or Helios query users.
"""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from helios_ds.catalog import DatasetInfo, DatasetNotFound, ManifestUnavailable
from helios_ds.manifests import GenerationManifest
from helios_ds.scenarios import SCENARIOS, ArtifactPlan, ScenarioPlan

from .service import GenerationService, get_service

router = APIRouter()


class LifecycleEvent(BaseModel):
    event_seq: int
    state: str
    actor: str
    occurred_at: str
    reason: Optional[str] = None


class DatasetSummary(BaseModel):
    dataset_id: str
    state: Optional[str]
    created_at: Optional[str]
    scenario_count: int
    planned_artifact_count: int
    manifest_sha256: str
    manifest_uri: Optional[str]
    config_hash: str
    template_bundle_hash: str
    source_fingerprint_hash: str
    generator_version: str
    generator_schema_version: str
    lifecycle: List[LifecycleEvent]


class ManifestOverview(BaseModel):
    """The manifest without its scenario list."""

    manifest_schema_version: str
    dataset_id: str
    manifest_sha256: str
    size_bytes: int
    identity: Dict[str, Any]
    config: Dict[str, Any]
    source_fingerprint: Dict[str, Any]
    templates: List[Dict[str, str]]
    artifact_counts: Dict[str, Dict[str, int]]
    scenario_counts: Dict[str, Dict[str, int]]


class ScenarioSummary(BaseModel):
    scenario_id: str
    scenario_type: str
    business_key: str
    headline: str
    artifact_types: List[str]


class ScenarioPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: List[ScenarioSummary]


class ScenarioDetail(BaseModel):
    scenario_id: str
    scenario_type: str
    business_key: str
    headline: str
    rank_score: str
    scenario_seed: str
    source_refs: List[Dict[str, Any]]
    facts: Dict[str, Any]
    artifacts: List[ArtifactPlan]


def _summary(info: DatasetInfo) -> DatasetSummary:
    record = info.record
    return DatasetSummary(
        dataset_id=record.dataset_id,
        state=info.state,
        created_at=info.created_at,
        scenario_count=record.scenario_count,
        planned_artifact_count=record.planned_artifact_count,
        manifest_sha256=record.manifest_sha256,
        manifest_uri=record.manifest_locator.get("uri") or record.manifest_locator.get("key"),
        config_hash=record.config_hash,
        template_bundle_hash=record.template_bundle_hash,
        source_fingerprint_hash=record.source_fingerprint_hash,
        generator_version=record.generator_version,
        generator_schema_version=record.generator_schema_version,
        lifecycle=[LifecycleEvent.model_validate(e.model_dump()) for e in info.lifecycle],
    )


def _headline(scenario: ScenarioPlan) -> str:
    definition = SCENARIOS.get(scenario.scenario_type)
    return definition.describe(scenario.facts) if definition else scenario.business_key


def _info(service: GenerationService, dataset_id: str) -> DatasetInfo:
    try:
        return service.datasets.get(dataset_id)
    except DatasetNotFound:
        raise HTTPException(status_code=404, detail=f"dataset {dataset_id} not found") from None


def _load(
    service: GenerationService, dataset_id: str
) -> Tuple[DatasetInfo, bytes, GenerationManifest]:
    """One lakehouse lookup per request; the manifest itself is cached by hash."""
    info = _info(service, dataset_id)
    try:
        data, manifest = service.datasets.load(info.record)
    except ManifestUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return info, data, manifest


def _manifest(service: GenerationService, dataset_id: str) -> GenerationManifest:
    return _load(service, dataset_id)[2]


def _search_text(scenario: ScenarioPlan, headline: str) -> str:
    values = " ".join(str(v) for v in scenario.facts.values() if v is not None)
    return f"{headline} {scenario.business_key} {values}".lower()


@router.get("/datasets", response_model=List[DatasetSummary])
def list_datasets(service: GenerationService = Depends(get_service)) -> List[DatasetSummary]:
    """Published datasets, newest first. Not in the spec contract; used by the dashboard."""
    return [_summary(info) for info in service.datasets.list()]


@router.get("/datasets/{dataset_id}", response_model=DatasetSummary)
def get_dataset(
    dataset_id: str, service: GenerationService = Depends(get_service)
) -> DatasetSummary:
    """Dataset metadata and lifecycle."""
    return _summary(_info(service, dataset_id))


@router.get("/datasets/{dataset_id}/manifest", response_model=ManifestOverview)
def get_dataset_manifest(
    dataset_id: str, service: GenerationService = Depends(get_service)
) -> ManifestOverview:
    """The generation manifest without its scenario list (see .../scenarios)."""
    info, data, manifest = _load(service, dataset_id)
    return ManifestOverview(
        manifest_schema_version=manifest.manifest_schema_version,
        dataset_id=manifest.dataset_id,
        manifest_sha256=info.record.manifest_sha256,
        size_bytes=len(data),
        identity=manifest.identity.model_dump(),
        config=manifest.config,
        source_fingerprint=manifest.source_fingerprint,
        templates=manifest.templates,
        artifact_counts=manifest.artifact_counts,
        scenario_counts=manifest.scenario_counts,
    )


@router.get("/datasets/{dataset_id}/manifest/raw")
def download_manifest(
    dataset_id: str, service: GenerationService = Depends(get_service)
) -> Response:
    """The exact manifest bytes (canonical JSON), as a download."""
    _, data, _ = _load(service, dataset_id)
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{dataset_id}-generation-manifest.json"'
        },
    )


@router.get("/datasets/{dataset_id}/scenarios", response_model=ScenarioPage)
def list_scenarios(
    dataset_id: str,
    scenario_type: Optional[str] = Query(None),
    q: Optional[str] = Query(
        None, description="Case-insensitive search over the story, key and all facts"
    ),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    service: GenerationService = Depends(get_service),
) -> ScenarioPage:
    manifest = _manifest(service, dataset_id)
    items = []
    needle = q.lower() if q else None
    for scenario in manifest.scenarios:
        if scenario_type and scenario.scenario_type != scenario_type:
            continue
        headline = _headline(scenario)
        if needle and needle not in _search_text(scenario, headline):
            continue
        items.append(
            ScenarioSummary(
                scenario_id=scenario.scenario_id,
                scenario_type=scenario.scenario_type,
                business_key=scenario.business_key,
                headline=headline,
                artifact_types=[a.artifact_type for a in scenario.artifacts],
            )
        )
    return ScenarioPage(
        total=len(items), offset=offset, limit=limit, items=items[offset : offset + limit]
    )


@router.get("/datasets/{dataset_id}/scenarios/{scenario_id}", response_model=ScenarioDetail)
def get_scenario(
    dataset_id: str, scenario_id: str, service: GenerationService = Depends(get_service)
) -> ScenarioDetail:
    for scenario in _manifest(service, dataset_id).scenarios:
        if scenario.scenario_id == scenario_id:
            return ScenarioDetail(headline=_headline(scenario), **scenario.model_dump())
    raise HTTPException(status_code=404, detail=f"scenario {scenario_id} not found")


@router.get("/datasets/{dataset_id}/artifacts")
def list_dataset_artifacts(
    dataset_id: str, service: GenerationService = Depends(get_service)
) -> list:
    """Artifact inventory. Empty until artifacts are rendered (phase 3)."""
    _info(service, dataset_id)
    return []


@router.get("/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> dict:
    """Artifact metadata and retrievable locator (phase 3)."""
    raise HTTPException(status_code=404, detail=f"artifact {artifact_id} not found")


@router.post("/datasets/{dataset_id}:crawl")
def request_crawl(dataset_id: str) -> dict:
    """Integration hook to request a Helios crawl."""
    raise HTTPException(status_code=501, detail="crawl hook not implemented yet")


@router.post("/evaluations")
def create_evaluation() -> dict:
    """Run golden evaluation (Helios-DS-Evaluation project)."""
    raise HTTPException(status_code=501, detail="evaluation not implemented yet")


@router.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str) -> dict:
    raise HTTPException(status_code=404, detail=f"evaluation {evaluation_id} not found")
