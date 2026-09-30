"""Render a planned dataset's artifacts into the object store and record them.

Idempotent and safe to repeat:
- rendering is deterministic, and the object store's write-if-hash-matches
  ``put`` makes a re-render a no-op, or a DeterminismIntegrityError if the bytes
  changed; nothing is ever overwritten;
- lakehouse rows are appended only for artifacts not already recorded.

Rendering runs in the calling thread (fast, and the rendering libraries are not
guaranteed thread-safe); uploads, which are I/O-bound, run in a thread pool.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set

from ..lakehouse import LakehouseSink
from ..manifests import GenerationManifest
from ..object_store import ObjectStore, PutResult
from ..schemas import ArtifactRecord, ArtifactSourceRecord
from ..templates import TemplateRegistry
from .registry import has_renderer, render_artifact

UPLOAD_WORKERS = 8


def artifact_key(dataset_id: str, artifact_id: str, extension: str) -> str:
    """Neutral object keys: nothing in the path hints at the scenario or its answers."""
    return f"datasets/{dataset_id}/artifacts/{artifact_id}.{extension}"


@dataclass
class RenderSummary:
    rendered: Dict[str, int] = field(default_factory=dict)  # artifact_type -> count
    pending: Dict[str, int] = field(default_factory=dict)  # planned, but no renderer yet
    newly_recorded: int = 0

    @property
    def total_rendered(self) -> int:
        return sum(self.rendered.values())

    @property
    def total_pending(self) -> int:
        return sum(self.pending.values())


def renderable_artifact_ids(manifest: GenerationManifest, templates: TemplateRegistry) -> Set[str]:
    return {
        a.artifact_id
        for s in manifest.scenarios
        for a in s.artifacts
        if has_renderer(templates.get(a.template_id))
    }


def render_dataset(
    manifest: GenerationManifest,
    templates: TemplateRegistry,
    store: ObjectStore,
    sink: LakehouseSink,
    progress: Optional[Callable[[int, int], None]] = None,
) -> RenderSummary:
    dataset_id = manifest.dataset_id
    summary = RenderSummary()
    work = []
    pending: Counter[str] = Counter()
    for scenario in manifest.scenarios:
        for artifact in scenario.artifacts:
            if has_renderer(templates.get(artifact.template_id)):
                work.append((scenario, artifact))
            else:
                pending[artifact.artifact_type] += 1
    summary.pending = dict(sorted(pending.items()))

    records: List[ArtifactRecord] = []
    sources: List[ArtifactSourceRecord] = []
    rendered: Counter[str] = Counter()
    with ThreadPoolExecutor(UPLOAD_WORKERS) as uploads:
        futures = []
        for scenario, artifact in work:
            out = render_artifact(templates, scenario, artifact)
            assert out is not None
            key = artifact_key(dataset_id, artifact.artifact_id, out.extension)
            futures.append((scenario, artifact, out, uploads.submit(store.put, key, out.data)))
        for done, (scenario, artifact, out, future) in enumerate(futures, start=1):
            put: PutResult = future.result()  # raises on any integrity problem
            rendered[artifact.artifact_type] += 1
            records.append(
                ArtifactRecord(
                    dataset_id=dataset_id,
                    artifact_id=artifact.artifact_id,
                    scenario_id=scenario.scenario_id,
                    artifact_type=artifact.artifact_type,
                    mime_type=out.mime_type,
                    source_locator=put.locator,
                    sha256=put.sha256,
                    size_bytes=put.size_bytes,
                    semantic_timestamp=out.semantic_timestamp,
                    template_id=artifact.template_id,
                    template_version=artifact.template_version,
                    acl_policy_id=None,  # simulated ACLs arrive with task C-06
                )
            )
            sources += [
                ArtifactSourceRecord(
                    dataset_id=dataset_id,
                    artifact_id=artifact.artifact_id,
                    source_table=ref["table"],
                    source_key=ref["key"],
                )
                for ref in scenario.source_refs
            ]
            if progress:
                progress(done, len(work))
    summary.rendered = dict(sorted(rendered.items()))

    recorded = {
        r.artifact_id
        for r in sink.read_dataset("helios_ds.artifacts", dataset_id)
        if isinstance(r, ArtifactRecord)
    }
    with_sources = {
        r.artifact_id
        for r in sink.read_dataset("helios_ds.artifact_sources", dataset_id)
        if isinstance(r, ArtifactSourceRecord)
    }
    new_sources = [s for s in sources if s.artifact_id not in with_sources]
    new_records = [r for r in records if r.artifact_id not in recorded]
    # Sources first: an artifacts row marks an artifact as fully recorded.
    sink.append("helios_ds.artifact_sources", new_sources)
    sink.append("helios_ds.artifacts", new_records)
    summary.newly_recorded = len(new_records)
    return summary
