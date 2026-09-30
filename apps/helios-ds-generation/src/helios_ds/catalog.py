"""Read side for published datasets: summaries, lifecycle and manifests.

Used by the API's dataset and manifest views (and the review screens later).
Manifests are immutable, so each is fetched once by its recorded locator,
checked against ``datasets.manifest_sha256`` and cached by that hash.
"""

import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .lakehouse import LakehouseSink
from .lifecycle import DatasetLifecycle
from .manifests import GenerationManifest
from .object_store import read_locator, sha256_hex
from .schemas import (
    ArtifactRecord,
    DatasetLifecycleRecord,
    DatasetRecord,
    TruthClaimRecord,
    TruthEntityMentionRecord,
    TruthEntityRecord,
    TruthEvidenceRecord,
    TruthRelationshipRecord,
)


class DatasetNotFound(KeyError):
    pass


class ManifestUnavailable(RuntimeError):
    """The manifest object is missing or does not match its recorded hash."""


class ArtifactNotFound(KeyError):
    pass


class ArtifactUnavailable(RuntimeError):
    """The artifact object is missing or does not match its recorded hash."""


@dataclass
class DatasetInfo:
    record: DatasetRecord
    lifecycle: List[DatasetLifecycleRecord]
    rendered_by_type: Dict[str, int] = field(default_factory=dict)

    @property
    def rendered_artifact_count(self) -> int:
        return sum(self.rendered_by_type.values())

    @property
    def dataset_id(self) -> str:
        return self.record.dataset_id

    @property
    def state(self) -> Optional[str]:
        return self.lifecycle[-1].state if self.lifecycle else None

    @property
    def created_at(self) -> Optional[str]:
        return self.lifecycle[0].occurred_at if self.lifecycle else None


class DatasetCatalog:
    MANIFEST_CACHE_SIZE = 8

    def __init__(
        self,
        sink: LakehouseSink,
        read_object: Callable[[Dict[str, Any]], Optional[bytes]] = read_locator,
    ):
        self.sink = sink
        self.read_object = read_object
        self._manifests: "OrderedDict[str, tuple[bytes, GenerationManifest]]" = OrderedDict()
        self._lock = threading.Lock()  # the API calls this from several threads
        self._entity_cache: "OrderedDict[str, List[TruthEntityRecord]]" = OrderedDict()

    def list(self) -> List[DatasetInfo]:
        """Every published dataset, newest first."""
        events: Dict[str, List[DatasetLifecycleRecord]] = {}
        for event in self.sink.read(DatasetLifecycle.TABLE):
            assert isinstance(event, DatasetLifecycleRecord)
            events.setdefault(event.dataset_id, []).append(event)
        rendered: Dict[str, Dict[str, int]] = {}
        counts = self.sink.count_by("helios_ds.artifacts", ["dataset_id", "artifact_type"])
        for (dataset_id, artifact_type), count in sorted(counts.items()):
            rendered.setdefault(dataset_id, {})[artifact_type] = count
        infos = [
            DatasetInfo(
                record,
                sorted(events.get(record.dataset_id, []), key=lambda e: e.event_seq),
                rendered.get(record.dataset_id, {}),
            )
            for record in self.sink.read("helios_ds.datasets")
            if isinstance(record, DatasetRecord)
        ]
        return sorted(infos, key=lambda i: i.created_at or "", reverse=True)

    def get(self, dataset_id: str) -> DatasetInfo:
        records = self.sink.read_dataset("helios_ds.datasets", dataset_id)
        if not records:
            raise DatasetNotFound(dataset_id)
        record = records[0]
        assert isinstance(record, DatasetRecord)
        rendered = Counter(a.artifact_type for a in self.artifacts(dataset_id))
        return DatasetInfo(
            record, DatasetLifecycle(self.sink).history(dataset_id), dict(sorted(rendered.items()))
        )

    def artifacts(self, dataset_id: str) -> List[ArtifactRecord]:
        return [
            a
            for a in self.sink.read_dataset("helios_ds.artifacts", dataset_id)
            if isinstance(a, ArtifactRecord)
        ]

    def artifact(self, artifact_id: str) -> ArtifactRecord:
        rows = self.sink.read("helios_ds.artifacts", where={"artifact_id": artifact_id})
        if not rows:
            raise ArtifactNotFound(artifact_id)
        record = rows[0]
        assert isinstance(record, ArtifactRecord)
        return record

    def artifact_bytes(self, record: ArtifactRecord) -> bytes:
        """The artifact's stored bytes, verified against its recorded SHA-256."""
        data = self.read_object(record.source_locator)
        if data is None:
            raise ArtifactUnavailable(f"artifact {record.artifact_id} is missing from storage")
        if sha256_hex(data) != record.sha256:
            raise ArtifactUnavailable(
                f"artifact {record.artifact_id} does not match its recorded SHA-256"
            )
        return data

    def manifest_bytes(self, dataset_id: str) -> bytes:
        return self.load(self.get(dataset_id).record)[0]

    def manifest(self, dataset_id: str) -> GenerationManifest:
        return self.load(self.get(dataset_id).record)[1]

    def load(self, record: DatasetRecord) -> "tuple[bytes, GenerationManifest]":
        """(exact bytes, parsed manifest) for a dataset record, verified and cached."""
        digest = record.manifest_sha256
        with self._lock:
            if digest in self._manifests:
                self._manifests.move_to_end(digest)
                return self._manifests[digest]
        data = self.read_object(record.manifest_locator)
        if data is None:
            raise ManifestUnavailable(f"manifest for {record.dataset_id} is missing from storage")
        if sha256_hex(data) != digest:
            raise ManifestUnavailable(
                f"manifest for {record.dataset_id} does not match its recorded SHA-256"
            )
        entry = (data, GenerationManifest.from_bytes(data))
        with self._lock:
            self._manifests[digest] = entry
            while len(self._manifests) > self.MANIFEST_CACHE_SIZE:
                self._manifests.popitem(last=False)
        return entry

    # --- ground truth (read side; rows are fixed once a dataset leaves CREATING) ---

    def truth_for_artifact(self, record: ArtifactRecord) -> "ArtifactTruth":
        """The hidden ground truth that concerns one artifact."""
        mentions = [
            m
            for m in self.sink.read(
                "helios_ground_truth.entity_mentions", where={"artifact_id": record.artifact_id}
            )
            if isinstance(m, TruthEntityMentionRecord)
        ]
        evidence = [
            e
            for e in self.sink.read(
                "helios_ground_truth.evidence", where={"artifact_id": record.artifact_id}
            )
            if isinstance(e, TruthEvidenceRecord)
        ]
        edges = [
            r
            for r in self.sink.read(
                "helios_ground_truth.relationships",
                where={"dataset_id": record.dataset_id, "scenario_id": record.scenario_id},
            )
            if isinstance(r, TruthRelationshipRecord)
            and (r.artifact_id is None or r.artifact_id == record.artifact_id)
        ]
        claims = {
            c.claim_id: c
            for c in self.sink.read(
                "helios_ground_truth.claims",
                where={"dataset_id": record.dataset_id, "scenario_id": record.scenario_id},
            )
            if isinstance(c, TruthClaimRecord)
        }
        wanted = (
            {m.entity_id for m in mentions}
            | {r.source_entity_id for r in edges}
            | {r.target_entity_id for r in edges}
            | {c.subject for c in claims.values()}
            | {c.obj for c in claims.values()}
        )
        entities = {
            e.entity_id: e for e in self._entities(record.dataset_id) if e.entity_id in wanted
        }
        return ArtifactTruth(mentions, evidence, edges, claims, entities)

    def _entities(self, dataset_id: str) -> List[TruthEntityRecord]:
        with self._lock:
            cached = self._entity_cache.get(dataset_id)
        if cached is None:
            cached = [
                e
                for e in self.sink.read_dataset("helios_ground_truth.entities", dataset_id)
                if isinstance(e, TruthEntityRecord)
            ]
            with self._lock:
                self._entity_cache[dataset_id] = cached
                while len(self._entity_cache) > self.MANIFEST_CACHE_SIZE:
                    self._entity_cache.popitem(last=False)
        return cached


@dataclass
class ArtifactTruth:
    mentions: List[TruthEntityMentionRecord]
    evidence: List[TruthEvidenceRecord]
    relationships: List[TruthRelationshipRecord]
    claims: Dict[str, TruthClaimRecord]
    entities: Dict[str, TruthEntityRecord]
