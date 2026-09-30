"""Dataset lifecycle and publish validation (spec: "publish protocol").

States: CREATING -> VALIDATING -> IN_REVIEW -> READY -> SUPERSEDED, with FAILED
and REJECTED as exits. State is an append-only event log in
helios_ds.dataset_lifecycle; the current state is the event with the highest
event_seq. Only READY datasets are crawler-visible, and READY is reachable only
through ``approve()``, which re-validates the published dataset first.

A lineage is the set of datasets generated from the same config (same
``config_hash``). Approving a dataset supersedes every other READY dataset in
its lineage (ADR 0001, decision 4), so at most one per lineage is READY.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple

from .lakehouse import LakehouseSink
from .manifests import GenerationManifest, manifest_key
from .object_store import ObjectStore, sha256_hex
from .schemas import (
    ArtifactRecord,
    DatasetLifecycleRecord,
    DatasetRecord,
    ScenarioPlanRecord,
    TemplateVersionRecord,
    TruthClaimRecord,
    TruthEntityMentionRecord,
    TruthEntityRecord,
    TruthEvidenceRecord,
    TruthRelationshipRecord,
)


class DatasetState(str, Enum):
    CREATING = "CREATING"
    VALIDATING = "VALIDATING"
    IN_REVIEW = "IN_REVIEW"
    READY = "READY"
    SUPERSEDED = "SUPERSEDED"
    DELETED = "DELETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


TRANSITIONS: Dict[Optional[DatasetState], FrozenSet[DatasetState]] = {
    None: frozenset({DatasetState.CREATING}),
    DatasetState.CREATING: frozenset({DatasetState.VALIDATING, DatasetState.FAILED}),
    DatasetState.VALIDATING: frozenset({DatasetState.IN_REVIEW, DatasetState.FAILED}),
    DatasetState.IN_REVIEW: frozenset(
        {DatasetState.READY, DatasetState.REJECTED, DatasetState.FAILED, DatasetState.DELETED}
    ),
    # FAILED -> CREATING retries the same dataset.
    DatasetState.FAILED: frozenset({DatasetState.CREATING, DatasetState.DELETED}),
    DatasetState.READY: frozenset({DatasetState.SUPERSEDED, DatasetState.DELETED}),
    DatasetState.SUPERSEDED: frozenset({DatasetState.DELETED}),
    DatasetState.REJECTED: frozenset({DatasetState.DELETED}),
    # A deleted dataset's data is gone; generating the same config again recreates it.
    DatasetState.DELETED: frozenset({DatasetState.CREATING}),
}

# States a dataset can be deleted from. CREATING and VALIDATING are excluded:
# a generation job may still be writing the dataset.
DELETABLE = frozenset(s for s, targets in TRANSITIONS.items() if DatasetState.DELETED in targets)


class InvalidTransition(RuntimeError):
    pass


class ValidationFailed(RuntimeError):
    def __init__(self, dataset_id: str, problems: List[str]):
        super().__init__(f"dataset {dataset_id} failed validation: " + "; ".join(problems))
        self.problems = problems


@dataclass
class Approval:
    record: DatasetLifecycleRecord
    superseded: List[str] = field(default_factory=list)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_published(
    sink: LakehouseSink,
    store: ObjectStore,
    dataset_id: str,
    expected_artifact_ids: Optional[Set[str]] = None,
) -> List[str]:
    """Problems that must block a dataset from leaving VALIDATING. Empty = valid.

    ``expected_artifact_ids`` (the planned artifacts that have a renderer) makes
    the check require exactly those artifacts to be recorded.
    """
    problems: List[str] = []
    datasets = sink.read_dataset("helios_ds.datasets", dataset_id)
    if len(datasets) != 1:
        return [f"expected exactly 1 datasets row, found {len(datasets)}"]
    record = datasets[0]
    assert isinstance(record, DatasetRecord)

    # Look the manifest up by its logical key: locators hold backend-specific
    # addresses (an S3 locator's key already includes the store prefix).
    data = store.get(manifest_key(dataset_id))
    if data is None:
        return [f"manifest object {manifest_key(dataset_id)} is missing"]
    if store.locator(manifest_key(dataset_id)) != record.manifest_locator:
        problems.append("datasets.manifest_locator does not match the object store")
    if sha256_hex(data) != record.manifest_sha256:
        return ["manifest object hash does not match datasets.manifest_sha256"]
    manifest = GenerationManifest.from_bytes(data)
    if manifest.dataset_id != dataset_id:
        problems.append(f"manifest belongs to dataset {manifest.dataset_id}")

    plans = sink.read_dataset("helios_ds.scenario_plans", dataset_id)
    plan_ids = sorted(p.scenario_id for p in plans if isinstance(p, ScenarioPlanRecord))
    if plan_ids != sorted(s.scenario_id for s in manifest.scenarios):
        problems.append(
            f"scenario_plans has {len(plan_ids)} rows; "
            f"manifest has {len(manifest.scenarios)} scenarios"
        )
    if record.scenario_count != len(manifest.scenarios):
        problems.append("datasets.scenario_count does not match the manifest")
    if record.planned_artifact_count != sum(len(s.artifacts) for s in manifest.scenarios):
        problems.append("datasets.planned_artifact_count does not match the manifest")

    templates = sink.read_dataset("helios_ds.template_versions", dataset_id)
    stored = sorted(
        (t.template_id, t.content_hash) for t in templates if isinstance(t, TemplateVersionRecord)
    )
    if stored != sorted((t["template_id"], t["content_hash"]) for t in manifest.templates):
        problems.append("template_versions rows do not match the manifest")

    artifacts = [
        a
        for a in sink.read_dataset("helios_ds.artifacts", dataset_id)
        if isinstance(a, ArtifactRecord)
    ]
    ids = [a.artifact_id for a in artifacts]
    planned = {a.artifact_id for s in manifest.scenarios for a in s.artifacts}
    if len(ids) != len(set(ids)):
        problems.append("helios_ds.artifacts has duplicate artifact rows")
    if set(ids) - planned:
        problems.append(f"{len(set(ids) - planned)} recorded artifacts are not in the plan")
    if expected_artifact_ids is not None and set(ids) != expected_artifact_ids:
        missing = len(expected_artifact_ids - set(ids))
        problems.append(f"{missing} renderable planned artifacts were not recorded")
    problems += _ground_truth_problems(
        sink, dataset_id, set(ids), {s.scenario_id for s in manifest.scenarios}
    )
    return problems


def _ground_truth_problems(
    sink: LakehouseSink, dataset_id: str, artifact_ids: Set[str], scenario_ids: Set[str]
) -> List[str]:
    """Referential integrity of helios_ground_truth for one dataset (no orphans)."""
    entities = {
        e.entity_id
        for e in sink.read_dataset("helios_ground_truth.entities", dataset_id)
        if isinstance(e, TruthEntityRecord)
    }
    mentions = [
        m
        for m in sink.read_dataset("helios_ground_truth.entity_mentions", dataset_id)
        if isinstance(m, TruthEntityMentionRecord)
    ]
    edges = [
        r
        for r in sink.read_dataset("helios_ground_truth.relationships", dataset_id)
        if isinstance(r, TruthRelationshipRecord)
    ]
    claims = {
        c.claim_id: c
        for c in sink.read_dataset("helios_ground_truth.claims", dataset_id)
        if isinstance(c, TruthClaimRecord)
    }
    evidence = [
        e
        for e in sink.read_dataset("helios_ground_truth.evidence", dataset_id)
        if isinstance(e, TruthEvidenceRecord)
    ]
    checks: List[Tuple[str, List[Any]]] = [
        ("mentions of unknown entities", [m for m in mentions if m.entity_id not in entities]),
        (
            "mentions in unrecorded artifacts",
            [m for m in mentions if m.artifact_id not in artifact_ids],
        ),
        (
            "relationships with unknown endpoints",
            [
                r
                for r in edges
                if r.source_entity_id not in entities or r.target_entity_id not in entities
            ],
        ),
        (
            "claims outside the plan or about unknown entities",
            [
                c
                for c in claims.values()
                if c.scenario_id not in scenario_ids
                or c.subject not in entities
                or c.obj not in entities
            ],
        ),
        ("evidence for unknown claims", [e for e in evidence if e.claim_id not in claims]),
        (
            "evidence in unrecorded artifacts",
            [e for e in evidence if e.artifact_id not in artifact_ids],
        ),
        (
            "claims without evidence",
            [c for c in claims.values() if c.claim_id not in {e.claim_id for e in evidence}],
        ),
    ]
    return [f"ground truth: {len(rows)} {label}" for label, rows in checks if rows]


class DatasetLifecycle:
    TABLE = "helios_ds.dataset_lifecycle"

    def __init__(self, sink: LakehouseSink, clock: Callable[[], str] = utc_now):
        self.sink = sink
        self.clock = clock

    def history(self, dataset_id: str) -> List[DatasetLifecycleRecord]:
        events = [
            e
            for e in self.sink.read_dataset(self.TABLE, dataset_id)
            if isinstance(e, DatasetLifecycleRecord)
        ]
        return sorted(events, key=lambda e: e.event_seq)

    def state(self, dataset_id: str) -> Optional[DatasetState]:
        events = self.history(dataset_id)
        return DatasetState(events[-1].state) if events else None

    def _append(
        self,
        dataset_id: str,
        to: DatasetState,
        actor: str,
        run_id: Optional[str],
        reason: Optional[str],
        related_dataset_id: Optional[str] = None,
    ) -> DatasetLifecycleRecord:
        events = self.history(dataset_id)
        current = DatasetState(events[-1].state) if events else None
        if to not in TRANSITIONS[current]:
            raise InvalidTransition(
                f"{dataset_id}: {current and current.value} -> {to.value} not allowed"
            )
        record = DatasetLifecycleRecord(
            dataset_id=dataset_id,
            event_seq=len(events) + 1,
            state=to.value,
            run_id=run_id,
            actor=actor,
            occurred_at=self.clock(),
            reason=reason,
            related_dataset_id=related_dataset_id,
        )
        self.sink.append(self.TABLE, [record])
        return record

    def transition(
        self,
        dataset_id: str,
        to: DatasetState,
        actor: str,
        run_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> DatasetLifecycleRecord:
        if to in (DatasetState.READY, DatasetState.SUPERSEDED, DatasetState.REJECTED):
            raise InvalidTransition(f"{to.value} is reachable only through approve() or reject()")
        return self._append(dataset_id, to, actor, run_id, reason)

    def approve(
        self, dataset_id: str, store: ObjectStore, approver: str, reason: Optional[str] = None
    ) -> Approval:
        """IN_REVIEW -> READY, only if the published dataset still validates, then
        supersede the other READY datasets in its lineage. Review flags don't block."""
        _require_actor(approver)
        if self.state(dataset_id) is not DatasetState.IN_REVIEW:
            raise InvalidTransition(f"{dataset_id} is not IN_REVIEW")
        problems = validate_published(self.sink, store, dataset_id)
        if problems:
            raise ValidationFailed(dataset_id, problems)
        record = self._append(dataset_id, DatasetState.READY, approver, None, reason)
        return Approval(record, self.supersede_lineage(dataset_id, approver))

    def supersede_lineage(self, dataset_id: str, actor: str) -> List[str]:
        """READY -> SUPERSEDED for every other READY dataset in ``dataset_id``'s
        lineage. Idempotent, so an interrupted approval can be completed by
        calling it again."""
        if self.state(dataset_id) is not DatasetState.READY:
            raise InvalidTransition(f"{dataset_id} is not READY")
        superseded = []
        for other in self.lineage(dataset_id):
            if other != dataset_id and self.state(other) is DatasetState.READY:
                self._append(
                    other,
                    DatasetState.SUPERSEDED,
                    actor,
                    None,
                    f"superseded by {dataset_id}",
                    related_dataset_id=dataset_id,
                )
                superseded.append(other)
        return superseded

    def reject(self, dataset_id: str, actor: str, reason: str) -> DatasetLifecycleRecord:
        """IN_REVIEW -> REJECTED (terminal). The reason is required: it is the
        record of what to fix in the config or templates before regenerating."""
        _require_actor(actor)
        reason = (reason or "").strip()
        if not reason:
            raise InvalidTransition("a rejection needs a reason")
        if self.state(dataset_id) is not DatasetState.IN_REVIEW:
            raise InvalidTransition(f"{dataset_id} is not IN_REVIEW")
        return self._append(dataset_id, DatasetState.REJECTED, actor, None, reason[:4000])

    def mark_deleted(
        self, dataset_id: str, actor: str, reason: Optional[str] = None
    ) -> Optional[DatasetLifecycleRecord]:
        """Record that a dataset is being deleted (hiding it from the crawler at
        once). Returns None if it is already DELETED, so an interrupted deletion
        can be completed by deleting again."""
        _require_actor(actor)
        state = self.state(dataset_id)
        if state is DatasetState.DELETED:
            return None
        if state not in DELETABLE:
            raise InvalidTransition(
                f"{dataset_id} is {state and state.value}; only a dataset that is not being "
                "generated can be deleted"
            )
        return self._append(dataset_id, DatasetState.DELETED, actor, None, reason)

    def lineage(self, dataset_id: str) -> List[str]:
        """Dataset IDs generated from the same config as ``dataset_id`` (itself included)."""
        records = self.sink.read_dataset("helios_ds.datasets", dataset_id)
        if not records:
            return []
        record = records[0]
        assert isinstance(record, DatasetRecord)
        return sorted(
            r.dataset_id
            for r in self.sink.read("helios_ds.datasets", where={"config_hash": record.config_hash})
            if isinstance(r, DatasetRecord)
        )

    def visible_datasets(self) -> List[str]:
        """Dataset IDs whose current state is READY (what the crawler may see)."""
        latest: Dict[str, DatasetLifecycleRecord] = {}
        for event in self.sink.read(self.TABLE):
            assert isinstance(event, DatasetLifecycleRecord)
            if (
                event.dataset_id not in latest
                or event.event_seq > latest[event.dataset_id].event_seq
            ):
                latest[event.dataset_id] = event
        return sorted(d for d, e in latest.items() if e.state == DatasetState.READY.value)


def _require_actor(actor: str) -> None:
    if not (actor or "").strip():
        raise InvalidTransition("approval, rejection and deletion need an authenticated actor")
