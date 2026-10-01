"""Ontology API endpoints: publish, query, and browse."""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

import yaml
from apps.helios.graph import store
from fastapi import APIRouter, HTTPException, Request
from helios_core.index import IndexStore, impala_index_store
from helios_core.index import ontology_versions as lakehouse_versions
from helios_core.ontology.graph import OntologyGraph
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


REPO_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY_DIR = REPO_ROOT / "ontology"


def require_ontology_edit(request: Request, organization_id: str | None) -> str:
    """The caller's principal ID if they hold ontology.edit in ``organization_id``
    (401 without an identity, 400 without an organization, 403 without the role)."""
    from apps.helios.console.api import principal_from_request
    from helios_core import authz

    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="no authenticated principal")
    if not organization_id:
        raise HTTPException(status_code=400, detail="organization_id is required")
    policy = getattr(request.app.state, "authorization_policy", None)
    if policy is None:
        repository = request.app.state.metadata_repository
        policy = authz.Policy(repository.grants_for_principal(principal.id))
    decision = policy.can(
        principal,
        authz.Action.ONTOLOGY_EDIT,
        authz.Resource("organization", organization_id, organization_id=organization_id),
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    return principal.id


@ontology_router.get("/schemas")
def list_schemas() -> list[dict[str, Any]]:
    """Root LinkML schemas that can be published: core, domain packs, customer extensions."""
    schemas = []
    for path in sorted(ONTOLOGY_DIR.rglob("*.yaml")):
        relative = path.relative_to(REPO_ROOT)
        if "mappings" in relative.parts:
            continue
        try:
            document = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict) or "classes" not in document:
            continue
        layer = (
            "customer"
            if "customers" in relative.parts
            else "pack"
            if "packs" in relative.parts
            else "core"
        )
        schemas.append(
            {
                "schema_path": str(relative),
                "name": document.get("name"),
                "title": document.get("title"),
                "version": str(document.get("version", "")),
                "layer": layer,
            }
        )
    order = {"customer": 0, "pack": 1, "core": 2}  # most complete first
    return sorted(schemas, key=lambda s: (order[s["layer"]], s["schema_path"]))


def _parse(version: str, schema_path: str | None) -> ParseResult:
    if not schema_path:
        raise HTTPException(status_code=400, detail="schema_path is required")
    schema_file = (REPO_ROOT / schema_path).resolve()
    if ONTOLOGY_DIR.resolve() not in schema_file.parents or not schema_file.is_file():
        raise HTTPException(status_code=400, detail=f"schema file not found: {schema_path}")
    mappings = load_mappings(ONTOLOGY_DIR / "mappings" / "ossie")
    ossie_file = REPO_ROOT / "models" / "published" / "tpcds.ossie.yaml"
    ossie_model = yaml.safe_load(ossie_file.read_text()) if ossie_file.exists() else None
    try:
        return parse(str(schema_file), version=version, ossie_model=ossie_model, mappings=mappings)
    except Exception as exc:
        LOGGER.exception("schema parse failed")
        raise HTTPException(
            status_code=400, detail=f"schema parse failed: {type(exc).__name__}: {exc}"
        ) from exc


def _shape(graph: OntologyGraph) -> dict[str, set[str]]:
    return {
        "classes": {n.key for n in graph.nodes if n.label == "Class"},
        "mappings": {
            f"{e.from_key} → {e.to_key}"
            for e in graph.edges
            if e.from_label == "OssieElement" and e.to_label == "Class"
        },
        "attributes": {n.key for n in graph.nodes if n.label == "Attribute"},
    }


@ontology_router.post(":check")
def check_ontology(
    request: PublishRequest, http_request: Request, organization_id: str | None = None
) -> dict[str, Any]:
    """Dry run of a publish: what would be published, what is broken, and how it
    differs from the active version. Nothing is written."""
    require_ontology_edit(http_request, organization_id)
    result = _parse(request.version, request.schema_path)
    graph = result.graph
    status, existing_hash = "new", None
    index = index_store()
    active_graph = None
    if index is not None:
        for record in lakehouse_versions.versions(index):
            if record.version == graph.version:
                existing_hash = record.content_hash
                status = "identical" if record.content_hash == graph.content_hash else "conflict"
        active = lakehouse_versions.active(index)
        if active is not None:
            for record in lakehouse_versions.versions(index):
                if (record.version, record.content_hash) == (active.version, active.content_hash):
                    active_graph = lakehouse_versions.graph_of(record)
    changes = None
    if active_graph is not None:
        new, old = _shape(graph), _shape(active_graph)
        changes = {
            "compared_with": active_graph.version,
            **{f"{kind}_added": sorted(new[kind] - old[kind]) for kind in ("classes", "mappings")},
            **{
                f"{kind}_removed": sorted(old[kind] - new[kind]) for kind in ("classes", "mappings")
            },
            "attributes_added": len(new["attributes"] - old["attributes"]),
            "attributes_removed": len(old["attributes"] - new["attributes"]),
        }
    return {
        **_summary(graph),
        "schema_path": request.schema_path,
        "broken_mappings": result.broken_mappings,
        "status": status,  # new, identical (publishing is a no-op), conflict (refused)
        "existing_content_hash": existing_hash,
        "changes": changes,
        "lakehouse": index is not None,
    }


@ontology_router.post(":publish", response_model=OntologyVersionResponse)
def publish_ontology(
    request: PublishRequest, http_request: Request, organization_id: str | None = None
) -> OntologyVersionResponse:
    """Publish a LinkML schema as an immutable ontology version (needs ontology.edit).

    The schema path is relative to the repository root, e.g.
    "ontology/customers/example-tenant/extension.yaml". The parser resolves its
    imports, applies the mappings and checks them against the published Ossie
    model; broken mappings are reported in the response.
    """
    actor = require_ontology_edit(http_request, organization_id)
    result = _parse(request.version, request.schema_path)

    # Record the version in helios_index first: it is the record, the cache is not.
    recorded = False
    index = index_store()
    if index is not None:
        try:
            lakehouse_versions.publish(index, result.graph, str(request.schema_path), actor)
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
def activate_ontology(
    version: str, request: Request, organization_id: str | None = None
) -> ActivationResponse:
    """Make a published version the active one: recorded in helios_index, then
    pointed to in the cache. The Helios Graph gateway loads the active version
    when it starts, or on its own :activate."""
    actor = require_ontology_edit(request, organization_id)
    index = index_store()
    if index is None:
        raise HTTPException(
            status_code=503, detail="helios_index is not configured (no Impala settings)"
        )
    try:
        record = lakehouse_versions.activate(index, version, actor)
    except lakehouse_versions.UnknownVersion:
        raise HTTPException(status_code=404, detail=f"ontology version {version} is not published")
    cached = {(e["version"], e["content_hash"]) for e in store.list_cached()}
    if (record.version, record.content_hash) not in cached:
        restore_cache_from_lakehouse()
    store.set_active(record.version, record.content_hash)
    return ActivationResponse(**record.model_dump())


def _cached_versions() -> dict[tuple[str, str], OntologyGraph]:
    """(version, content_hash) -> graph for every version in the on-disk cache."""
    graphs: dict[tuple[str, str], OntologyGraph] = {}
    versions_dir = store.store_root() / "versions"
    if not versions_dir.exists():
        return graphs
    for version_dir in sorted(versions_dir.iterdir()):
        for payload_file in sorted(version_dir.glob("*.json")) if version_dir.is_dir() else []:
            try:
                graph = OntologyGraph.from_dict(json.loads(payload_file.read_text()))
                graphs[(graph.version, graph.content_hash)] = graph
            except Exception as exc:  # noqa: BLE001 - a broken cache file is skipped, not fatal
                LOGGER.warning("failed to load cached ontology %s: %s", payload_file, exc)
    return graphs


def _summary(graph: OntologyGraph) -> dict[str, Any]:
    nodes_by_label = graph.nodes_by_label()
    return {
        "version": graph.version,
        "content_hash": graph.content_hash,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "enum_count": len(nodes_by_label.get("Enum", [])),
        "class_count": len(nodes_by_label.get("Class", [])),
    }


@ontology_router.get("/versions")
def list_ontology_versions(request: Request) -> list[dict]:
    """Published ontology versions, newest first.

    With helios_index configured, the lakehouse is the record: each version
    shows who published it and whether it is active. Versions found only in
    the on-disk cache (published before CR-0c) are listed with
    ``in_lakehouse: false``; they cannot be activated until republished.
    """
    cached = _cached_versions()
    index = index_store()
    results: list[dict[str, Any]] = []
    recorded: set[tuple[str, str]] = set()
    if index is not None:
        active = lakehouse_versions.active(index)
        for record in reversed(lakehouse_versions.versions(index)):
            key = (record.version, record.content_hash)
            recorded.add(key)
            results.append(
                {
                    "version": record.version,
                    "content_hash": record.content_hash,
                    "node_count": record.node_count,
                    "edge_count": record.edge_count,
                    "enum_count": _summary(lakehouse_versions.graph_of(record))["enum_count"],
                    "is_active": bool(active and (active.version, active.content_hash) == key),
                    "in_lakehouse": True,
                    "published_at": record.published_at,
                    "published_by": record.published_by,
                    "schema_path": record.schema_path,
                }
            )
    pointer = store.active_pointer() or {}
    for key, graph in cached.items():
        if key in recorded:
            continue
        results.append(
            {
                **_summary(graph),
                "is_active": index is None
                and (pointer.get("version"), pointer.get("content_hash")) == key,
                "in_lakehouse": False,
                "published_at": None,
                "published_by": None,
                "schema_path": None,
            }
        )
    return results


def _load_version(version: str) -> OntologyGraph:
    """The graph for ``version``: the content the lakehouse records for it (restoring
    the cache if needed), else the only cached content for it."""
    index = index_store()
    if index is not None:
        for record in lakehouse_versions.versions(index):
            if record.version == version:
                cached = _cached_versions().get((record.version, record.content_hash))
                if cached is None:
                    store.save(lakehouse_versions.graph_of(record))
                    return lakehouse_versions.graph_of(record)
                return cached
    candidates = [g for (v, _), g in _cached_versions().items() if v == version]
    if not candidates:
        raise HTTPException(status_code=404, detail=f"version {version} not found")
    if len(candidates) > 1:
        raise HTTPException(
            status_code=409,
            detail=f"version {version} has {len(candidates)} different cached contents; "
            "republish it under a new version",
        )
    return candidates[0]


@ontology_router.get("/{version}/graph")
def get_ontology_graph(version: str, request: Request) -> dict:
    """An ontology version's full graph (nodes, edges, content_hash)."""
    return _load_version(version).to_dict()


@ontology_router.get("/{version}/classes/{class_name}")
def get_class_detail(version: str, class_name: str, request: Request) -> dict:
    """Retrieve a class and its attributes/relationships.

    Returns the class node, all its attributes (HAS_ATTRIBUTE edges + Attribute nodes),
    range classes (RANGE edges), and parent classes (IS_A edges).
    """
    graph = _load_version(version)
    try:
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
                        attributes.append(
                            {
                                "name": node.key,
                                "properties": node.properties,
                            }
                        )
                        # Find the range of this attribute.
                        for r_edge in graph.edges:
                            if r_edge.type == "RANGE" and r_edge.from_key == edge.to_key:
                                ranges.append(
                                    {
                                        "attribute": edge.to_key,
                                        "range_class": r_edge.to_key,
                                    }
                                )

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
