"""Helios Graph gateway: the only way in to Memgraph.

The Workbench Application exposes this FastAPI app on CDSW_APP_PORT. Memgraph
itself listens on loopback Bolt and is never reachable from outside the pod, so
every caller goes through the fixed, parameterised endpoints defined here.

Memgraph holds no durable truth: it is rebuilt from the payload cache on
startup, and published versions are snapshotted to the lakehouse by the
publisher (O-2). Nothing here is a system of record.

Crawl-run projections (CR-7, `/v1/index/...`) are pushed by the Helios API from
helios_index and are deliberately *not* rebuilt on startup: the lakehouse is
canonical and the API re-projects a run on demand.
"""

from __future__ import annotations

import hmac
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from helios_core.ontology import OntologyGraph, OntologyGraphError

from . import index, memgraph_process, ontology, store
from .bolt import connect

_TOKEN = os.environ.get("HELIOS_GRAPH_TOKEN")

_memgraph = memgraph_process.MemgraphProcess()
_state: dict[str, Any] = {
    "client": None,
    "started_at": None,
    "startup_seconds": None,
    "startup_error": None,
    "rebuild": None,
}


def _rebuild_active(client: Any) -> dict[str, Any]:
    """Reload the active version after a restart, since Memgraph starts empty."""
    graph = store.load_active()
    if graph is None:
        return {"status": "nothing_cached"}
    result = ontology.materialise(client, graph)
    print(
        f"rebuilt ontology {graph.version} ({graph.content_hash[:12]}): {result['status']}",
        flush=True,
    )
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    started = time.monotonic()
    try:
        _memgraph.start()
        _memgraph.wait_until_listening()
        _state["client"] = connect()
        ontology.ensure_indexes(_state["client"])
        index.ensure_indexes(_state["client"])
        _state["started_at"] = time.time()
        _state["startup_seconds"] = round(time.monotonic() - started, 2)
        print(f"memgraph ready in {_state['startup_seconds']}s (pid {_memgraph.pid})", flush=True)
        try:
            _state["rebuild"] = _rebuild_active(_state["client"])
        except Exception as exc:
            # A bad cache entry must not cost us a running gateway; the publisher
            # can always materialise again.
            _state["rebuild"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            print(f"ontology rebuild failed: {exc}", flush=True)
    except Exception as exc:
        # Serve anyway: /health and /v1/diagnostics are how an operator finds out
        # why Memgraph is down, and a pod that refuses to boot answers nothing.
        _state["startup_error"] = f"{type(exc).__name__}: {exc}"
        print(f"MEMGRAPH STARTUP FAILED -- serving degraded\n{exc}", flush=True)
    try:
        yield
    finally:
        client = _state["client"]
        if client is not None:
            client.close()
            _state["client"] = None
        _memgraph.stop()


app = FastAPI(
    title="Helios Graph",
    description="Gateway in front of Memgraph; fixed parameterised Cypher only",
    version="0.1.0",
    lifespan=lifespan,
)


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    """Service-token gate. The Helios API holds the token; browsers never see it."""
    if not _TOKEN:
        raise HTTPException(status_code=503, detail="graph_authentication_not_configured")
    expected = f"Bearer {_TOKEN}"
    if authorization is None or not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


def _client() -> Any:
    """The Bolt client, or a 503 explaining that Memgraph is not usable."""
    client = _state["client"]
    if client is None or not _memgraph.running:
        raise HTTPException(status_code=503, detail="memgraph_unavailable")
    return client


class MaterialiseRequest(BaseModel):
    """A normalised ontology graph, as `helios_core.ontology` defines it."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    content_hash: str | None = Field(
        default=None, description="If given, the gateway verifies it before loading"
    )
    activate: bool = Field(
        default=True,
        description="Point the active marker at this version once loaded. "
        "Pass false to stage a blue/green swap, then call :activate.",
    )
    force: bool = Field(default=False, description="Reload even if the hash already matches")


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness: is the Memgraph child process still up?"""
    return {
        "status": "ok" if _memgraph.running else "degraded",
        "service": "helios-graph",
        "memgraph_running": _memgraph.running,
        "memgraph_pid": _memgraph.pid,
        "startup_error": _state["startup_error"],
    }


@app.get("/ready")
async def ready() -> dict[str, Any]:
    """Readiness: does Bolt answer a query right now?"""
    client = _state["client"]
    if client is None or not _memgraph.running:
        raise HTTPException(status_code=503, detail="memgraph_unavailable")
    rows = client.query("RETURN 1 AS ok")
    if rows != [{"ok": 1}]:
        raise HTTPException(status_code=503, detail="memgraph_unhealthy")
    return {"status": "ready", "driver": client.driver_name}


@app.post("/v1/probe", dependencies=[Depends(require_token)])
async def probe() -> dict[str, Any]:
    """O-0 acceptance check: RETURN 1 through the gateway, over Bolt."""
    client = _state["client"]
    if client is None:
        raise HTTPException(status_code=503, detail="memgraph_unavailable")
    return {"rows": client.query("RETURN 1 AS ok")}


@app.get("/v1/diagnostics", dependencies=[Depends(require_token)])
async def diagnostics() -> dict[str, Any]:
    """What the O-0 spike set out to learn about this pod."""
    client = _state["client"]
    storage: list[dict[str, Any]] | str
    if client is None:
        storage = "unavailable"
    else:
        storage = client.query("SHOW STORAGE INFO")
    try:
        binary = memgraph_process.binary_path()
    except RuntimeError as exc:
        binary = str(exc)
    return {
        "memgraph": {
            "binary": binary,
            "command": _memgraph.command,
            "pid": _memgraph.pid,
            "running": _memgraph.running,
            "data_directory": str(memgraph_process.data_directory()),
            "config_file": str(memgraph_process.isolated_config_path()),
            "startup_seconds": _state["startup_seconds"],
            "started_at": _state["started_at"],
            "startup_error": _state["startup_error"],
            "output": _memgraph.diagnostic_output(),
        },
        "memory": {
            "pod_limit_mib": memgraph_process.pod_memory_limit_mib(),
            "memgraph_limit_mib": memgraph_process.memgraph_memory_limit_mib(),
        },
        "process": {"uid": os.getuid(), "user": os.environ.get("USER")},
        "bolt": {
            "port": memgraph_process.BOLT_PORT,
            "driver": client.driver_name if client else None,
            "listening": memgraph_process.bolt_is_listening(),
        },
        "ontology": {
            "rebuild_on_startup": _state["rebuild"],
            "store_dir": str(store.store_root()),
            "active": store.active_pointer(),
            "cached": store.list_cached(),
        },
        "storage_info": storage,
    }


@app.post("/v1/ontology/{version}:materialise", dependencies=[Depends(require_token)])
async def materialise_version(version: str, request: MaterialiseRequest) -> dict[str, Any]:
    """Load a published version into Memgraph. Idempotent on (version, content_hash)."""
    client = _client()
    try:
        graph = OntologyGraph.from_dict(
            {
                "version": version,
                "nodes": request.nodes,
                "edges": request.edges,
                "content_hash": request.content_hash,
            }
        )
    except OntologyGraphError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    result = ontology.materialise(client, graph, force=request.force)
    # Cache only after Memgraph accepted it, so the cache never holds a version
    # that failed to load.
    store.save(graph)
    if request.activate:
        store.set_active(graph.version, graph.content_hash)
    result["active"] = request.activate
    return result


@app.post("/v1/ontology/{version}:activate", dependencies=[Depends(require_token)])
async def activate_version(version: str) -> dict[str, Any]:
    """Swap the active marker to an already-materialised version (blue/green)."""
    client = _client()
    state = ontology.version_state(client, version)
    if state is None:
        raise HTTPException(status_code=404, detail="version_not_materialised")
    if not state.get("complete"):
        raise HTTPException(status_code=409, detail="version_incomplete")
    try:
        store.set_active(version, state["content_hash"])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="version_not_in_store") from exc
    return {"status": "active", "version": version, "content_hash": state["content_hash"]}


@app.get("/v1/ontology/versions", dependencies=[Depends(require_token)])
async def list_versions() -> dict[str, Any]:
    return {"active": store.active_pointer(), "versions": ontology.list_versions(_client())}


@app.get("/v1/ontology/{version}/graph", dependencies=[Depends(require_token)])
async def version_graph(
    version: str,
    layer: Annotated[str | None, Query(description="core, pack or customer")] = None,
    kind: Annotated[str | None, Query(description="entity, event, asset, concept, relationship")] = None,
    focus: Annotated[str | None, Query(description="Centre the view on this class")] = None,
    depth: Annotated[int, Query(ge=1, le=6)] = 1,
) -> dict[str, Any]:
    """Classes plus the edges the viewer draws between them."""
    client = _client()
    payload: dict[str, Any] = {
        "version": version,
        "classes": ontology.version_classes(client, version, layer=layer, kind=kind),
        "hierarchy": ontology.version_hierarchy(client, version),
        "relationships": ontology.version_relationships(client, version),
        "broken_mappings": ontology.broken_mappings(client, version),
    }
    if focus:
        payload["neighbourhood"] = ontology.neighbourhood(client, version, focus, depth)
    return payload


@app.get("/v1/ontology/{version}/classes/{name}", dependencies=[Depends(require_token)])
async def class_detail(version: str, name: str) -> dict[str, Any]:
    """Inspector payload: description, attributes, mappings, glossary terms."""
    detail = ontology.class_detail(_client(), version, name)
    if detail is None:
        raise HTTPException(status_code=404, detail="class_not_found")
    return detail


# --- crawl-run projections (CR-7) ---------------------------------------------------


class BeginIndexRequest(BaseModel):
    ontology_version: str = Field(description="The ontology version the run was made under")
    force: bool = Field(default=False, description="Drop a complete load of this run first")


class IndexRowsRequest(BaseModel):
    """One batch of helios_index rows, as the record models dump them."""

    rows: list[dict[str, Any]] = Field(default_factory=list)


def _index_run(client: Any, crawl_run_id: str) -> dict[str, Any]:
    state = index.run_state(client, crawl_run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="run_not_loaded")
    return state


@app.post("/v1/index/{crawl_run_id}:begin", dependencies=[Depends(require_token)])
async def begin_index_run(crawl_run_id: str, request: BeginIndexRequest) -> dict[str, Any]:
    """Start (or restart) loading one crawl run. An incomplete load is always
    replaced; a complete one only with `force`."""
    client = _client()
    existing = index.run_state(client, crawl_run_id)
    if existing is not None:
        if existing.get("complete") and not request.force:
            raise HTTPException(status_code=409, detail="run_already_loaded")
        index.drop_run(client, crawl_run_id)
    result = index.begin_run(client, crawl_run_id, request.ontology_version)
    result["status"] = "replaced" if existing is not None else "created"
    return result


@app.post("/v1/index/{crawl_run_id}/{table}", dependencies=[Depends(require_token)])
async def load_index_rows(crawl_run_id: str, table: str, request: IndexRowsRequest) -> dict[str, Any]:
    """Load one table's rows for a run begun with :begin. Returns how many rows
    were merged and how many edges were skipped for want of their end nodes."""
    if table not in index.TABLES:
        raise HTTPException(status_code=404, detail="unknown_table")
    client = _client()
    state = _index_run(client, crawl_run_id)
    try:
        result = index.load_batch(
            client, crawl_run_id, table, request.rows, ontology_version=state["ontology_version"]
        )
    except index.IndexGraphError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"crawl_run_id": crawl_run_id, "table": table, "rows": len(request.rows), **result}


@app.post("/v1/index/{crawl_run_id}:finish", dependencies=[Depends(require_token)])
async def finish_index_run(crawl_run_id: str) -> dict[str, Any]:
    client = _client()
    _index_run(client, crawl_run_id)
    return index.finish_run(client, crawl_run_id)


@app.get("/v1/index", dependencies=[Depends(require_token)])
async def list_index_runs() -> dict[str, Any]:
    return {"runs": index.list_runs(_client())}


@app.get("/v1/index/{crawl_run_id}", dependencies=[Depends(require_token)])
async def index_run_state(crawl_run_id: str) -> dict[str, Any]:
    """The run's load state with live counts (the stored counts are from :finish)."""
    client = _client()
    state = _index_run(client, crawl_run_id)
    state["live_counts"] = index.counts(client, crawl_run_id)
    return state


@app.delete("/v1/index/{crawl_run_id}", dependencies=[Depends(require_token)])
async def drop_index_run(crawl_run_id: str) -> dict[str, Any]:
    client = _client()
    _index_run(client, crawl_run_id)
    index.drop_run(client, crawl_run_id)
    return {"status": "dropped", "crawl_run_id": crawl_run_id}
