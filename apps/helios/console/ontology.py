"""Ontology API endpoints: publish, query, and browse."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

import yaml
from apps.helios.graph import store
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from helios_core.index import IndexStore, impala_index_store
from helios_core.index import ontology_versions as lakehouse_versions
from helios_core.ontology.graph import OntologyGraph, OntologyGraphError
from helios_core.ontology.mapping import load_mappings
from helios_core.ontology.parser import ParseResult, parse
from pydantic import BaseModel

LOGGER = logging.getLogger(__name__)
ontology_router = APIRouter(prefix="/api/v1/ontology", tags=["ontology"])

# Published versions live in helios_index (CR-0c); the on-disk store is a cache
# the Helios Graph gateway reads. Without Impala settings (local development)
# the cache is used on its own.
_index_lock = threading.Lock()
_index: dict[str, IndexStore | None] = {}


def index_store() -> IndexStore | None:
    with _index_lock:
        if "store" not in _index:
            store_ = impala_index_store()
            if store_ is not None:
                store_.ensure_tables()
            _index["store"] = store_
        return _index["store"]


def _actor(request: Request) -> str:
    from apps.helios.console.api import principal_from_request

    principal = principal_from_request(request)
    return principal.id if principal is not None else "unknown"


def restore_cache_from_lakehouse() -> int:
    """Write published versions the on-disk cache lacks, and the active pointer if
    it has none. Returns how many versions were restored."""
    index = index_store()
    if index is None:
        return 0
    cached = {(e["version"], e["content_hash"]) for e in store.list_cached()}
    restored = 0
    for record in lakehouse_versions.missing_from_cache(index, cached):
        store.save(lakehouse_versions.graph_of(record))
        restored += 1
    active = lakehouse_versions.active(index)
    if active is not None and store.active_pointer() is None:
        store.set_active(active.version, active.content_hash)
    return restored


def start_cache_restore() -> None:
    """Restore the cache in the background at API start (Impala may be slow to reach)."""

    def run() -> None:
        try:
            count = restore_cache_from_lakehouse()
            if count:
                LOGGER.info("restored %d ontology version(s) from helios_index", count)
        except Exception:
            LOGGER.exception("could not restore the ontology cache from helios_index")

    threading.Thread(target=run, name="ontology-cache-restore", daemon=True).start()


class PublishRequest(BaseModel):
    """Publish a LinkML schema as an ontology version."""

    version: str
    schema_path: str | None = None


class OntologyVersionResponse(BaseModel):
    """Response from publishing an ontology."""

    version: str
    content_hash: str
    node_count: int
    edge_count: int
    enum_count: int
    broken_mappings: list[dict[str, str]]
    recorded_in_lakehouse: bool = False


@ontology_router.post(":publish", response_model=OntologyVersionResponse)
def publish_ontology(request: PublishRequest, http_request: Request) -> OntologyVersionResponse:
    """Publish a LinkML schema as an ontology version.

    The schema file path must be relative to the repository root, e.g.
    "ontology/customers/example-tenant/extension.yaml". The parser resolves
    all imports and validates against the Ossie model spec.

    Returns:
        OntologyVersionResponse with version, hash, counts, and broken_mappings.
    """
    # Validate schema path.
    if not request.schema_path:
        raise HTTPException(status_code=400, detail="schema_path is required")

    # Find schema file relative to repo root.
    # __file__ is .../apps/helios/console/ontology.py
    # repo root is .../helios (three levels up)
    repo_root = Path(__file__).resolve().parents[3]
    schema_file = repo_root / request.schema_path

    if not schema_file.exists():
        raise HTTPException(
            status_code=400,
            detail=f"schema file not found: {request.schema_path}",
        )

    # Mappings and the published Ossie model they are checked against (CR-0b).
    mappings = load_mappings(repo_root / "ontology" / "mappings" / "ossie")
    ossie_file = repo_root / "models" / "published" / "tpcds.ossie.yaml"
    ossie_model = yaml.safe_load(ossie_file.read_text()) if ossie_file.exists() else None

    try:
        result: ParseResult = parse(
            str(schema_file), version=request.version, ossie_model=ossie_model, mappings=mappings
        )
    except Exception as e:
        LOGGER.exception("schema parse failed")
        raise HTTPException(
            status_code=400,
            detail=f"schema parse failed: {type(e).__name__}: {e}",
        )

    # Record the version in helios_index first: it is the record, the cache is not.
    recorded = False
    index = index_store()
    if index is not None:
        try:
            lakehouse_versions.publish(
                index, result.graph, request.schema_path, _actor(http_request)
            )
            recorded = True
        except lakehouse_versions.VersionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Save the version to the content-addressed cache.
    try:
        store.save(result.graph)
    except Exception as e:
        LOGGER.exception("failed to save ontology version")
        raise HTTPException(
            status_code=500,
            detail=f"failed to save version: {type(e).__name__}: {e}",
        )

    # Count nodes by label and edges.
    nodes_by_label = result.graph.nodes_by_label()
    enum_count = len(nodes_by_label.get("Enum", []))

    return OntologyVersionResponse(
        version=result.graph.version,
        content_hash=result.graph.content_hash,
        node_count=len(result.graph.nodes),
        edge_count=len(result.graph.edges),
        enum_count=enum_count,
        broken_mappings=result.broken_mappings,
        recorded_in_lakehouse=recorded,
    )


class ActivationResponse(BaseModel):
    version: str
    content_hash: str
    activated_at: str
    activated_by: str


@ontology_router.post("/{version}:activate", response_model=ActivationResponse)
def activate_ontology(version: str, request: Request) -> ActivationResponse:
    """Make a published version the active one: recorded in helios_index, then
    pointed to in the cache. The Helios Graph gateway loads the active version
    when it starts, or on its own :activate."""
    index = index_store()
    if index is None:
        raise HTTPException(
            status_code=503, detail="helios_index is not configured (no Impala settings)"
        )
    try:
        record = lakehouse_versions.activate(index, version, _actor(request))
    except lakehouse_versions.UnknownVersion:
        raise HTTPException(status_code=404, detail=f"ontology version {version} is not published")
    cached = {(e["version"], e["content_hash"]) for e in store.list_cached()}
    if (record.version, record.content_hash) not in cached:
        restore_cache_from_lakehouse()
    store.set_active(record.version, record.content_hash)
    return ActivationResponse(**record.model_dump())


@ontology_router.get("/versions")
async def list_ontology_versions(request: Request) -> list[dict]:
    """List published ontology versions.

    Returns a list of all versions in the cache, with version, content_hash,
    node_count, edge_count, and activation status.
    """
    versions_dir = store.store_root() / "versions"
    if not versions_dir.exists():
        return []

    results = []
    active = store.active_pointer()

    # Scan versions directory: .../versions/{version}/{hash}.json
    for version_dir in sorted(versions_dir.iterdir()):
        if not version_dir.is_dir():
            continue

        version_name = version_dir.name
        for payload_file in sorted(version_dir.glob("*.json")):
            try:
                payload = json.loads(payload_file.read_text())
                graph = OntologyGraph.from_dict(payload)

                is_active = bool(
                    active
                    and active.get("version") == version_name
                    and active.get("content_hash") == graph.content_hash
                )

                nodes_by_label = graph.nodes_by_label()
                results.append({
                    "version": graph.version,
                    "content_hash": graph.content_hash,
                    "node_count": len(graph.nodes),
                    "edge_count": len(graph.edges),
                    "enum_count": len(nodes_by_label.get("Enum", [])),
                    "is_active": is_active,
                })
            except Exception as e:
                LOGGER.warning(f"failed to load version {version_name}: {e}")

    return results


@ontology_router.get("/{version}/graph")
async def get_ontology_graph(version: str, request: Request) -> dict:
    """Retrieve an ontology version's full graph.

    Returns the complete OntologyGraph payload (nodes, edges, content_hash).
    """
    versions_dir = store.store_root() / "versions" / version
    if not versions_dir.exists():
        raise HTTPException(status_code=404, detail=f"version {version} not found")

    # Load the active version or the first available one.
    payload_files = sorted(versions_dir.glob("*.json"))
    if not payload_files:
        raise HTTPException(status_code=404, detail=f"version {version} has no payloads")

    try:
        payload = json.loads(payload_files[0].read_text())
        graph = OntologyGraph.from_dict(payload)
        return graph.to_dict()
    except Exception as e:
        LOGGER.exception(f"failed to load version {version}")
        raise HTTPException(status_code=500, detail=f"failed to load version: {e}")


@ontology_router.get("/{version}/classes/{class_name}")
async def get_class_detail(version: str, class_name: str, request: Request) -> dict:
    """Retrieve a class and its attributes/relationships.

    Returns the class node, all its attributes (HAS_ATTRIBUTE edges + Attribute nodes),
    range classes (RANGE edges), and parent classes (IS_A edges).
    """
    versions_dir = store.store_root() / "versions" / version
    if not versions_dir.exists():
        raise HTTPException(status_code=404, detail=f"version {version} not found")

    payload_files = sorted(versions_dir.glob("*.json"))
    if not payload_files:
        raise HTTPException(status_code=404, detail=f"version {version} has no payloads")

    try:
        payload = json.loads(payload_files[0].read_text())
        graph = OntologyGraph.from_dict(payload)

        # Find the class node.
        class_node = None
        for node in graph.nodes:
            if node.label == "Class" and node.key == class_name:
                class_node = node
                break

        if not class_node:
            raise HTTPException(status_code=404, detail=f"class {class_name} not found")

        # Gather related nodes and edges.
        attributes = []
        ranges = []
        parents = []

        for edge in graph.edges:
            if edge.type == "IS_A" and edge.from_key == class_name:
                parents.append(edge.to_key)
            elif edge.type == "HAS_ATTRIBUTE" and edge.from_key == class_name:
                # Load the attribute node.
                for node in graph.nodes:
                    if node.label == "Attribute" and node.key == edge.to_key:
                        attributes.append({
                            "name": node.key,
                            "properties": node.properties,
                        })
                        # Find the range of this attribute.
                        for r_edge in graph.edges:
                            if r_edge.type == "RANGE" and r_edge.from_key == edge.to_key:
                                ranges.append({
                                    "attribute": edge.to_key,
                                    "range_class": r_edge.to_key,
                                })

        return {
            "class": {
                "name": class_node.key,
                "properties": class_node.properties,
            },
            "parents": parents,
            "attributes": attributes,
            "ranges": ranges,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOGGER.exception(f"failed to retrieve class {class_name}")
        raise HTTPException(status_code=500, detail=f"failed to retrieve class: {e}")
