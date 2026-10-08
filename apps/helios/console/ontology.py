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
from helios_core.index import mappings as stored_mappings
from helios_core.ontology.mapping import (
    SourceMapping,
    load_mappings,
    mapping_problems,
    resolution_config,
)
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


def shipped_mappings() -> list[SourceMapping]:
    """The example mappings in the repository, for importing."""
    return load_mappings(ONTOLOGY_DIR / "mappings" / "ossie")


def semantic_model(model: str) -> dict[str, Any] | None:
    """The published semantic model a mapping names (its file under
    models/published), or None if it is not there."""
    name = Path(model).name
    candidates = [name] if name.endswith((".yaml", ".yml")) else [f"{name}.ossie.yaml", f"{name}.yaml"]
    for candidate in candidates:
        path = REPO_ROOT / "models" / "published" / candidate
        if path.is_file():
            return yaml.safe_load(path.read_text())
    return None


def _parse(version: str, schema_path: str | None) -> ParseResult:
    if not schema_path:
        raise HTTPException(status_code=400, detail="schema_path is required")
    schema_file = (REPO_ROOT / schema_path).resolve()
    if ONTOLOGY_DIR.resolve() not in schema_file.parents or not schema_file.is_file():
        raise HTTPException(status_code=400, detail=f"schema file not found: {schema_path}")
    # The active mappings in helios_index (CG-6); the shipped files only until one is stored.
    index = index_store()
    mappings = stored_mappings.active_mappings(index) if index is not None else []
    if not mappings:
        mappings = shipped_mappings()
    ossie_model = semantic_model(mappings[0].model) if mappings else None
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


# --- source mappings (CG-6) -----------------------------------------------------------


class MappingRequest(BaseModel):
    mapping: dict[str, Any]
    note: str = ""


def _mapping_index() -> IndexStore:
    index = index_store()
    if index is None:
        raise HTTPException(
            status_code=503, detail="helios_index is not configured (no Impala settings)"
        )
    return index


def _active_classes() -> set[str] | None:
    """Class names of the active ontology version, or None if none is active."""
    index = index_store()
    active = lakehouse_versions.active(index) if index is not None else None
    if active is None:
        return None
    for record in lakehouse_versions.versions(index):
        if (record.version, record.content_hash) == (active.version, active.content_hash):
            graph = lakehouse_versions.graph_of(record)
            return {n.key for n in graph.nodes if n.label == "Class"}
    return None


def _checked(document: dict[str, Any]) -> tuple[SourceMapping | None, list[str], list[str]]:
    """(mapping, problems, checks that could not be run)."""
    from pydantic import ValidationError

    try:
        mapping = SourceMapping.model_validate(document)
    except ValidationError as exc:
        return (
            None,
            [f"{'.'.join(str(p) for p in e['loc']) or 'mapping'}: {e['msg']}" for e in exc.errors()],
            [],
        )
    model = semantic_model(mapping.model)
    classes = _active_classes()
    skipped = []
    if model is None:
        skipped.append(f"tables and columns: no published semantic model named {mapping.model!r}")
    if classes is None:
        skipped.append("classes: no ontology version is active")
    return mapping, mapping_problems(mapping, model, classes), skipped


def _mapping_meta(record: Any, active: dict[str, Any]) -> dict[str, Any]:
    current = active.get(record.model)
    return {
        "version": record.version,
        "model": record.model,
        "ontology_version": record.ontology_version,
        "content_hash": record.content_hash,
        "created_at": record.created_at,
        "created_by": record.created_by,
        "note": record.note,
        "is_active": current is not None and current.version == record.version,
    }


@ontology_router.get("/mappings")
def list_mappings() -> dict[str, Any]:
    """Mapping versions, newest first, and which one is active for each semantic model."""
    index = _mapping_index()
    active = stored_mappings.active(index)
    return {
        "active": {model: record.version for model, record in active.items()},
        "versions": [
            _mapping_meta(r, active) for r in reversed(stored_mappings.versions(index))
        ],
    }


@ontology_router.get("/mappings/shipped")
def list_shipped_mappings() -> list[dict[str, Any]]:
    """The example mappings shipped with Helios, to save as a first version."""
    return [m.model_dump(mode="json", by_alias=True) for m in shipped_mappings()]


def _published_models() -> list[str]:
    folder = REPO_ROOT / "models" / "published"
    return sorted(p.name for p in folder.glob("*.yaml")) if folder.is_dir() else []


def _field_role(field: dict[str, Any]) -> str:
    """The role Helios DS gave a column when it profiled the table ("identifier",
    "attribute", ...), or "" when the model does not say."""
    for extension in field.get("custom_extensions") or []:
        try:
            role = json.loads(extension.get("data") or "{}").get("role")
        except (TypeError, ValueError):
            continue
        if role:
            return str(role)
    return ""


@ontology_router.get("/mappings/models")
def list_mapping_models() -> dict[str, Any]:
    """The published semantic models a mapping can be written against, and the
    class names of the active ontology (None when no version is active)."""
    classes = _active_classes()
    return {"models": _published_models(), "classes": sorted(classes) if classes is not None else None}


@ontology_router.get("/mappings/models/{model}")
def get_mapping_model(model: str) -> dict[str, Any]:
    """The tables, columns and table relationships of one published semantic
    model: what a mapping editor offers to pick from."""
    document = semantic_model(model)
    if document is None:
        raise HTTPException(status_code=404, detail=f"no published semantic model named {model!r}")
    tables = []
    databases: dict[str, int] = {}
    for dataset in document.get("datasets", []):
        source = str(dataset.get("source") or "")
        if "." in source:
            database = source.rsplit(".", 1)[0]
            databases[database] = databases.get(database, 0) + 1
        tables.append(
            {
                "name": dataset["name"],
                "source": source,
                "label": dataset.get("label") or "",
                "description": dataset.get("description") or "",
                "columns": [
                    {
                        "name": f["name"],
                        "label": f.get("label") or "",
                        "datatype": f.get("datatype") or "",
                        "role": _field_role(f),
                    }
                    for f in dataset.get("fields", [])
                ],
            }
        )
    return {
        "model": Path(model).name,
        "name": document.get("name") or "",
        # Where most of its tables live: the default for a new mapping's database.
        "database": max(databases, key=lambda d: databases[d]) if databases else "",
        "tables": tables,
        "relationships": [
            {
                "name": r.get("name") or "",
                "from": r.get("from"),
                "to": r.get("to"),
                "from_columns": r.get("from_columns") or [],
                "to_columns": r.get("to_columns") or [],
            }
            for r in document.get("relationships", [])
        ],
    }


@ontology_router.get("/mappings/{version}")
def get_mapping(version: int) -> dict[str, Any]:
    index = _mapping_index()
    try:
        record = stored_mappings.get(index, version)
    except stored_mappings.UnknownMappingVersion as exc:
        raise HTTPException(status_code=404, detail=f"mapping version {version} not found") from exc
    return {
        **_mapping_meta(record, stored_mappings.active(index)),
        "mapping": json.loads(record.mapping_json),
    }


@ontology_router.post("/mappings:validate")
def validate_mapping(body: MappingRequest) -> dict[str, Any]:
    """Check a mapping against the published semantic model and the active
    ontology. Nothing is written."""
    _, problems, skipped = _checked(body.mapping)
    return {"valid": not problems, "problems": problems, "not_checked": skipped}


class MappingProbeRequest(BaseModel):
    mapping: dict[str, Any]
    # Probe one mapped class's table, or one anchor's query; exactly one of the two.
    entity: str | None = None
    anchor: str | None = None


PROBE_SAMPLE = 5


def warehouse_connection(principal_id: str) -> Any:
    """A warehouse connection as the signed-in principal (the probe must not
    show a user rows they could not query themselves)."""
    from .crawler import _truth_connection

    return _truth_connection(principal_id)[0]


def _cell(value: Any) -> Any:
    return value if value is None or isinstance(value, (int, float, str, bool)) else str(value)


def _probe_entity(cursor: Any, mapping: SourceMapping, class_name: str) -> dict[str, Any]:
    entity = next((e for e in mapping.entities if e.class_name == class_name), None)
    if entity is None:
        raise HTTPException(status_code=404, detail=f"class {class_name} has no entity mapping")
    ids = entity.identifiers
    table = f"{resolution_config(mapping).database}.{entity.ossie_element}"
    key = ", ".join(ids.primary)
    # Distinct keys are counted through a grouped subquery: it works for a
    # key of several columns, which COUNT(DISTINCT a, b) does not everywhere.
    count_sql = f"SELECT COUNT(*) FROM {table}"
    distinct_sql = f"SELECT COUNT(*) FROM (SELECT {key} FROM {table} GROUP BY {key}) k"
    cursor.execute(count_sql)
    rows = int(cursor.fetchone()[0])
    cursor.execute(distinct_sql)
    distinct = int(cursor.fetchone()[0])
    shown = list(dict.fromkeys([*ids.primary, *ids.secondary, *ids.display, *ids.aliases]))
    sample_sql = f"SELECT {', '.join(shown)} FROM {table} LIMIT {PROBE_SAMPLE}"
    cursor.execute(sample_sql)
    sample = [[_cell(v) for v in row] for row in cursor.fetchall()]
    findings = []
    if rows == 0:
        findings.append("The table is empty: nothing can be resolved to this class.")
    elif distinct < rows:
        findings.append(
            f"The key is not unique: {rows:,} rows but {distinct:,} different keys. "
            "Two records would be treated as the same one."
        )
    if not ids.secondary and not ids.display and not ids.aliases:
        findings.append("No identifier or name column is mapped: documents can only name this class by its key.")
    return {
        "kind": "entity",
        "class": class_name,
        "table": table,
        "rows": rows,
        "distinct_keys": distinct,
        "key_is_unique": rows == distinct,
        "columns": shown,
        "sample": sample,
        "findings": findings,
        "sql": [count_sql, distinct_sql, sample_sql],
    }


def _probe_anchor(cursor: Any, mapping: SourceMapping, class_name: str) -> dict[str, Any]:
    from apps.helios.crawler import anchors

    config = resolution_config(mapping)
    plan = next((p for p in anchors.plans(config) if p.anchor.identifiers.class_name == class_name), None)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"class {class_name} is not an anchor in this mapping")
    # The query a crawl would run, without a document's values to narrow it:
    # it proves the tables join and the columns exist.
    sql, params = anchors.build_query(plan, anchors.Constraints())
    cursor.execute(sql, params)
    columns = [d[0] for d in cursor.description or []]
    rows = [[_cell(v) for v in row] for row in cursor.fetchall()][:PROBE_SAMPLE]
    findings = [] if rows else ["The query returned no rows: no case could be tied to a record."]
    return {
        "kind": "anchor",
        "class": class_name,
        "columns": columns,
        "sample": rows,
        "findings": findings,
        "sql": [sql],
    }


@ontology_router.post("/mappings:probe")
def probe_mapping(body: MappingProbeRequest, request: Request) -> dict[str, Any]:
    """Ask the warehouse about a mapping being edited, as the signed-in user:
    for a class, whether its key is unique and what its identifiers look like;
    for an anchor, whether its query runs. Nothing is written."""
    from helios_core.engines.impala import ImpalaProxyDelegationError
    from helios_core.ontology.mapping import _PLAIN

    if (body.entity is None) == (body.anchor is None):
        raise HTTPException(status_code=400, detail="name one class to probe: entity or anchor")
    mapping, problems, _ = _checked(body.mapping)
    if mapping is None or problems:
        # Names go into SQL as written, so only a mapping that validates is probed.
        raise HTTPException(status_code=422, detail={"message": "invalid mapping", "problems": problems})
    if not _PLAIN.match(resolution_config(mapping).database) or not all(
        _PLAIN.match(e.ossie_element) for e in mapping.entities
    ):
        raise HTTPException(status_code=422, detail={"message": "invalid mapping", "problems": ["the database and table names must be plain names"]})
    actor = _actor(request)
    if actor == "unknown":
        raise HTTPException(status_code=401, detail="authenticated principal is required")
    try:
        connection = warehouse_connection(actor)
    except ImpalaProxyDelegationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        cursor = connection.cursor()
        if body.entity is not None:
            return _probe_entity(cursor, mapping, body.entity)
        return _probe_anchor(cursor, mapping, body.anchor or "")
    except HTTPException:
        raise
    except Exception as exc:  # the warehouse's own words are the useful part
        raise HTTPException(status_code=422, detail={"message": "the warehouse refused the query", "problems": [str(exc).splitlines()[0][:500]]}) from exc
    finally:
        connection.close()


@ontology_router.post("/mappings", status_code=201)
def save_mapping(
    body: MappingRequest, http_request: Request, organization_id: str | None = None
) -> dict[str, Any]:
    """Save a mapping as a new immutable version (needs ontology.edit). Refused
    with the list of problems if it does not validate. Saving does not activate."""
    actor = require_ontology_edit(http_request, organization_id)
    mapping, problems, skipped = _checked(body.mapping)
    if mapping is None or problems:
        raise HTTPException(
            status_code=422, detail={"message": "invalid mapping", "problems": problems}
        )
    index = _mapping_index()
    record, created = stored_mappings.save(index, mapping, actor, body.note)
    return {
        **_mapping_meta(record, stored_mappings.active(index)),
        "created": created,
        "not_checked": skipped,
    }


@ontology_router.post("/mappings/{version}:activate")
def activate_mapping(
    version: int, http_request: Request, organization_id: str | None = None
) -> dict[str, Any]:
    """Make a saved version the one crawls use for its semantic model (needs
    ontology.edit). It is checked again first: the semantic model or the active
    ontology may have changed since it was saved."""
    actor = require_ontology_edit(http_request, organization_id)
    index = _mapping_index()
    try:
        record = stored_mappings.get(index, version)
    except stored_mappings.UnknownMappingVersion as exc:
        raise HTTPException(status_code=404, detail=f"mapping version {version} not found") from exc
    _, problems, _ = _checked(json.loads(record.mapping_json))
    if problems:
        raise HTTPException(
            status_code=422,
            detail={"message": "this mapping no longer validates", "problems": problems},
        )
    activation = stored_mappings.activate(index, version, actor)
    return {
        "version": activation.version,
        "model": activation.model,
        "activated_at": activation.activated_at,
        "activated_by": activation.activated_by,
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
