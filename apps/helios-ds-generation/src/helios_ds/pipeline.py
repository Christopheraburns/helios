"""Generation pipeline:

    TPC-DS -> scenario planner -> deterministic plan -> generation manifest
           -> rendered artifacts (phase 3+) -> validation -> IN_REVIEW

``plan_and_publish`` is idempotent. Re-running it for the same inputs recomputes
the plan, manifest and artifacts and checks them byte-for-byte against what was
already published; any difference is a determinism failure, never an overwrite.
Rendering happens while the dataset is CREATING (spec: publish protocol), so a
dataset only reaches VALIDATING and IN_REVIEW once its artifacts exist.
"""

import platform
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

from .config import DatasetConfig
from .golden import build_golden, check_golden, write_golden
from .lakehouse import LakehouseSink
from .lakehouse.sink import canonical_json
from .lifecycle import DatasetLifecycle, DatasetState, ValidationFailed, utc_now, validate_published
from .manifests import GenerationIdentity, GenerationManifest, manifest_key
from .object_store import DeterminismIntegrityError, ObjectStore
from .render.dataset import RenderSummary, render_dataset, renderable_artifact_ids
from .scenarios import ScenarioPlanner, renderable, tables_for
from .schemas import DatasetRecord, GenerationRunRecord, ScenarioPlanRecord, TemplateVersionRecord
from .templates import TemplateRegistry
from .tpcds import TpcdsRepository, fingerprint_hash

ACTOR = "service:helios-ds-generator"
PUBLISHED_STATES = {
    DatasetState.IN_REVIEW,
    DatasetState.READY,
    DatasetState.SUPERSEDED,
    DatasetState.REJECTED,
}


@dataclass(frozen=True)
class PublishResult:
    dataset_id: str
    run_id: str
    manifest_sha256: str
    state: DatasetState
    newly_published: bool
    artifact_counts: Dict[str, Dict[str, int]]
    scenario_counts: Dict[str, Dict[str, int]]
    rendered: Dict[str, int]  # artifact_type -> artifacts rendered
    pending: Dict[str, int]  # artifact_type -> planned artifacts with no renderer yet
    ground_truth: Dict[str, int]  # helios_ground_truth table -> rows
    locators_unchecked: int  # locators not verifiable here (e.g. PDFs without pypdf)
    golden: Dict[str, int] = field(default_factory=dict)  # golden questions per kind

    @property
    def total_rendered(self) -> int:
        return sum(self.rendered.values())


def build_manifest(
    config: DatasetConfig, repository: TpcdsRepository, templates: TemplateRegistry
) -> GenerationManifest:
    fingerprint = repository.fingerprint(tables_for(config, renderable(templates)))
    identity = GenerationIdentity(
        config_hash=config.config_hash(),
        template_bundle_hash=templates.bundle_hash(),
        source_fingerprint_hash=fingerprint_hash(fingerprint),
    )
    dataset_id = identity.dataset_id()
    plan = ScenarioPlanner(config, templates).plan(repository, dataset_id)
    return GenerationManifest(
        dataset_id=dataset_id,
        identity=identity,
        config=config.model_dump(mode="json"),
        source_fingerprint=fingerprint,
        templates=templates.describe(),
        artifact_counts=plan.artifact_counts,
        scenario_counts=plan.scenario_counts,
        scenarios=plan.scenarios,
    )


def _publish_rows(
    sink: LakehouseSink, manifest: GenerationManifest, manifest_sha: str, locator: Dict[str, str]
) -> bool:
    """Write helios_ds rows once. The datasets row is written last, as the commit marker."""
    dataset_id = manifest.dataset_id
    existing = sink.read_dataset("helios_ds.datasets", dataset_id)
    if existing:
        if any(
            isinstance(r, DatasetRecord) and r.manifest_sha256 != manifest_sha for r in existing
        ):
            raise DeterminismIntegrityError(
                f"dataset {dataset_id} was published with a different manifest"
            )
        return False

    # A previous attempt may have died after writing some child rows.
    for table in ("helios_ds.scenario_plans", "helios_ds.template_versions"):
        sink.delete_dataset_rows(table, dataset_id)
    sink.append(
        "helios_ds.template_versions",
        [TemplateVersionRecord(dataset_id=dataset_id, **t) for t in manifest.templates],
    )
    sink.append(
        "helios_ds.scenario_plans",
        [
            ScenarioPlanRecord(
                dataset_id=dataset_id,
                scenario_id=s.scenario_id,
                scenario_type=s.scenario_type,
                business_key=s.business_key,
                rank_score=s.rank_score,
                scenario_seed=s.scenario_seed,
                source_refs=s.source_refs,
                facts=s.facts,
                artifact_plan=[a.model_dump() for a in s.artifacts],
            )
            for s in manifest.scenarios
        ],
    )
    identity = manifest.identity
    sink.append(
        "helios_ds.datasets",
        [
            DatasetRecord(
                dataset_id=dataset_id,
                config_hash=identity.config_hash,
                config_json=DatasetConfig.model_validate(manifest.config).canonical_json(),
                template_bundle_hash=identity.template_bundle_hash,
                source_fingerprint_hash=identity.source_fingerprint_hash,
                source_fingerprint_json=canonical_json(manifest.source_fingerprint),
                generator_version=identity.generator_version,
                generator_schema_version=identity.generator_schema_version,
                python_version=platform.python_version(),
                container_digest=None,
                manifest_locator=locator,
                manifest_sha256=manifest_sha,
                scenario_count=len(manifest.scenarios),
                planned_artifact_count=sum(len(s.artifacts) for s in manifest.scenarios),
            )
        ],
    )
    return True


def plan_and_publish(
    config: DatasetConfig,
    repository: TpcdsRepository,
    templates: TemplateRegistry,
    sink: LakehouseSink,
    store: ObjectStore,
    job_id: Optional[str] = None,
    clock: Callable[[], str] = utc_now,
    render: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
) -> PublishResult:
    """Plan, publish and (unless ``render`` is False) render a dataset.
    ``progress(done, total)`` is called as artifacts are rendered."""
    run_id = f"run_{uuid.uuid4().hex}"
    started_at = clock()
    lifecycle = DatasetLifecycle(sink, clock)
    dataset_id = "unknown"
    try:
        manifest = build_manifest(config, repository, templates)
        dataset_id = manifest.dataset_id
        data = manifest.to_bytes()
        state = lifecycle.state(dataset_id)

        def render_all(new: bool) -> RenderSummary:
            if not render:
                return RenderSummary()
            summary = render_dataset(manifest, templates, store, sink, progress)
            if summary.truth is not None:
                # Golden questions (C-08) are generated with the dataset, from its
                # truth. A published dataset's questions are only compared: they
                # never change (or appear) after the dataset leaves CREATING.
                golden = build_golden(manifest, summary.truth, repository)
                if new:
                    write_golden(sink, dataset_id, golden)
                else:
                    check_golden(sink, dataset_id, golden)
                summary.golden = golden.counts()
            return summary

        if state in PUBLISHED_STATES:
            # Already published: this run is a reproducibility check only
            # (re-rendered artifacts must match the stored bytes exactly).
            put = store.put(manifest_key(dataset_id), data)
            _publish_rows(sink, manifest, put.sha256, put.locator)
            summary = render_all(new=False)
            newly_published = False
        else:
            if state in (None, DatasetState.FAILED, DatasetState.DELETED):
                lifecycle.transition(dataset_id, DatasetState.CREATING, ACTOR, run_id)
            put = store.put(manifest_key(dataset_id), data)
            newly_published = _publish_rows(sink, manifest, put.sha256, put.locator)
            summary = render_all(new=True)
            if lifecycle.state(dataset_id) is DatasetState.CREATING:
                lifecycle.transition(dataset_id, DatasetState.VALIDATING, ACTOR, run_id)
            expected = renderable_artifact_ids(manifest, templates) if render else None
            problems = validate_published(sink, store, dataset_id, expected)
            if problems:
                raise ValidationFailed(dataset_id, problems)
            lifecycle.transition(dataset_id, DatasetState.IN_REVIEW, ACTOR, run_id)
            state = DatasetState.IN_REVIEW

        _record_run(sink, run_id, dataset_id, job_id, started_at, clock(), "SUCCEEDED", None)
        assert state is not None
        return PublishResult(
            dataset_id=dataset_id,
            run_id=run_id,
            manifest_sha256=put.sha256,
            state=state,
            newly_published=newly_published,
            artifact_counts=manifest.artifact_counts,
            scenario_counts=manifest.scenario_counts,
            rendered=summary.rendered,
            pending=summary.pending,
            ground_truth=summary.ground_truth,
            locators_unchecked=summary.locators_unchecked,
            golden=summary.golden,
        )
    except Exception as exc:
        if dataset_id != "unknown" and lifecycle.state(dataset_id) in (
            DatasetState.CREATING,
            DatasetState.VALIDATING,
        ):
            lifecycle.transition(
                dataset_id, DatasetState.FAILED, ACTOR, run_id, reason=str(exc)[:1000]
            )
        _record_run(
            sink, run_id, dataset_id, job_id, started_at, clock(), "FAILED", str(exc)[:4000]
        )
        raise


def _record_run(
    sink: LakehouseSink,
    run_id: str,
    dataset_id: str,
    job_id: Optional[str],
    started_at: str,
    finished_at: str,
    outcome: str,
    error: Optional[str],
) -> None:
    sink.append(
        "helios_ds.generation_runs",
        [
            GenerationRunRecord(
                run_id=run_id,
                dataset_id=dataset_id,
                job_id=job_id,
                started_at=started_at,
                finished_at=finished_at,
                outcome=outcome,
                error=error,
                python_version=platform.python_version(),
                platform=platform.platform(),
                container_digest=None,
            )
        ],
    )
