"""Helios Graph gateway: the only way in to Memgraph.

The Workbench Application exposes this FastAPI app on CDSW_APP_PORT. Memgraph
itself listens on loopback Bolt and is never reachable from outside the pod, so
every caller goes through the fixed, parameterised endpoints defined here.

O-0 scope: prove Memgraph starts under the pod's constraints and answers
`RETURN 1`. The ontology materialise and read endpoints arrive with O-1.
"""

from __future__ import annotations

import hmac
import os
import time
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException

from . import memgraph_process
from .bolt import connect

_TOKEN = os.environ.get("HELIOS_GRAPH_TOKEN")

_memgraph = memgraph_process.MemgraphProcess()
_state: dict[str, Any] = {"client": None, "started_at": None, "startup_seconds": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    started = time.monotonic()
    _memgraph.start()
    _memgraph.wait_until_listening()
    _state["client"] = connect()
    _state["started_at"] = time.time()
    _state["startup_seconds"] = round(time.monotonic() - started, 2)
    print(f"memgraph ready in {_state['startup_seconds']}s (pid {_memgraph.pid})", flush=True)
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


@app.get("/health")
async def health() -> dict[str, Any]:
    """Liveness: is the Memgraph child process still up?"""
    return {
        "status": "ok" if _memgraph.running else "degraded",
        "service": "helios-graph",
        "memgraph_running": _memgraph.running,
        "memgraph_pid": _memgraph.pid,
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
    return {
        "memgraph": {
            "binary": memgraph_process.binary_path(),
            "command": _memgraph.command,
            "pid": _memgraph.pid,
            "running": _memgraph.running,
            "data_directory": str(memgraph_process.data_directory()),
            "startup_seconds": _state["startup_seconds"],
            "started_at": _state["started_at"],
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
        "storage_info": storage,
    }
