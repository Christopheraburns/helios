"""
helios MCP server: lets an agent discover a published semantic model, express questions as semantic requests, and
get SQL or rows back. The agent never sees the physical schema and never writes SQL.

Tools
  list_models()                          published models
  describe_model(model)                  datasets, dimensions, metrics, joins — the menu an agent chooses from
  search_model(model, query)             find metrics/dimensions by words in their names, labels, descriptions
  compile_query(model, request)          semantic request -> SQL (no execution)
  run_query(model, request, limit)       semantic request -> SQL -> rows from the warehouse
  explain_request(model, request)        which fact, joins and columns a request resolves to, without SQL

Semantic request (JSON):
  {"metrics": ["Store Sales Revenue"],                         # by name or business name
   "measures": [{"field": "ss_quantity", "agg": "avg"}],       # ad-hoc aggregate when no metric fits
   "dimensions": ["State", "d_year"],                          # column, label, or dataset.column
   "filters": [["d_year", "=", 2001], ["Category", "in", ["Music", "Books"]]],
   "via": {"date_dim": "catalog_sales__cs_ship_date_sk__date_dim"},   # pin a join when several exist
   "order_by": ["-Store Sales Revenue"], "limit": 100}

Works with mcp 1.x (FastMCP) and 2.x (MCPServer). Run under uvicorn via apps/mcp/app.py.
"""
from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    _V2 = False
except ImportError:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True

from helios_core import __version__
from helios_core.compiler import CompileError, Compiler, SemanticRequest
from helios_core.ossie import SemanticModel

ROOT = os.environ.get("HELIOS_ROOT") or os.path.join(os.environ.get("CDSW_PROJECT_DIR", "/home/cdsw"), "helios")
DIALECT = os.environ.get("HELIOS_SQL_DIALECT", "hive")   # what the compiler renders for the warehouse engine
MAX_ROWS = int(os.environ.get("HELIOS_MCP_MAX_ROWS", "500"))

INSTRUCTIONS = (
    "helios exposes governed semantic models over a data warehouse. To answer a question: call describe_model (or "
    "search_model) to find the metrics and dimensions that match the user's words, build a semantic request from "
    "them, then call run_query. Use the business names and descriptions to choose; never guess column names. "
    "If a request fails, the error names the problem (ambiguous field, unknown metric, metrics from different "
    "facts) — fix the request and retry. Show the user the SQL from the result when they ask how a number was computed."
)

# The SDK's transport rejects any Host header other than localhost by default (DNS-rebinding protection for
# servers that run on a developer's machine). Behind the Workbench ingress the Host is the public subdomain, so
# that check returns "421 Invalid Host header" for every client. Bearer-token auth is what protects this server;
# HELIOS_MCP_ALLOWED_HOSTS (comma-separated) re-enables the check for a known host list if wanted.
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

_allowed = [h.strip() for h in os.environ.get("HELIOS_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_security = TransportSecuritySettings(enable_dns_rebinding_protection=bool(_allowed), allowed_hosts=_allowed)
_kwargs = dict(name="helios", instructions=INSTRUCTIONS, transport_security=_security)
if not _V2:
    _kwargs.update(stateless_http=True, json_response=True)
server = _Server(**_kwargs)


# ---------------------------------------------------------------- model access
@lru_cache(maxsize=8)
def _model(name: str, _mtime: float) -> SemanticModel:
    return SemanticModel.load_published(name, ROOT)


def model(name: str) -> SemanticModel:
    path = os.path.join(ROOT, "models", "published", f"{name}.ossie.yaml")
    if not os.path.exists(path):
        raise ValueError(f"no published model named {name!r}; available: {SemanticModel.list_published(ROOT)}")
    return _model(name, os.path.getmtime(path))   # mtime in the key -> republishing invalidates the cache


def _request(d: dict | str) -> SemanticRequest:
    if isinstance(d, str):
        d = json.loads(d)
    return SemanticRequest.from_dict(d)


@lru_cache(maxsize=1)
def _engine():
    from helios_core.config import impala_config
    from helios_core.engines import ImpalaEngine
    cfg = impala_config()
    if cfg is None:
        raise RuntimeError("warehouse connection is not configured (IMPALA_HOST and credentials)")
    return ImpalaEngine(cfg)


# ---------------------------------------------------------------- tools
@server.tool()
def list_models() -> list[dict]:
    """List the published semantic models available to query."""
    out = []
    for name in SemanticModel.list_published(ROOT):
        m = model(name)
        out.append({"name": m.name, "description": m.description, "datasets": len(m.datasets),
                    "metrics": len(m.metrics), "relationships": len(m.relationships)})
    return out


@server.tool()
def describe_model(model_name: str, include_fields: bool = True) -> dict:
    """Describe a model: its datasets, the metrics that can be computed, the dimensions available to group and
    filter by, and how datasets join. Read this before building a request. Set include_fields=false for a short
    overview (datasets and metrics only)."""
    m = model(model_name)
    desc = m.describe()
    if not include_fields:
        for d in desc["datasets"]:
            d.pop("fields", None)
        desc.pop("relationships", None)
    else:
        for d in desc["datasets"]:
            d["fields"] = [f for f in d["fields"] if f["role"] not in ("identifier", "foreign_key")]
    return desc


@server.tool()
def search_model(model_name: str, query: str, limit: int = 15) -> dict:
    """Find metrics, dimensions and raw measures whose name, label, synonyms or description mention the words in
    `query`. Use this instead of describe_model when the model is large or the question is specific. Prefer a
    metric over a raw measure when one matches — metrics are the governed definitions."""
    m = model(model_name)
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2]
    if not words:
        return {"metrics": [], "dimensions": []}

    def score(*texts: str) -> int:
        blob = " ".join(t.lower() for t in texts if t)
        return sum(2 if re.search(rf"\b{re.escape(w)}\b", blob) else (1 if w in blob else 0) for w in words)

    metrics = sorted(((score(mt.name, mt.description, *mt.synonyms), mt) for mt in m.metrics.values()), key=lambda x: -x[0])
    fields = sorted(((score(f.name, f.label, f.description, *f.synonyms), f) for d in m.datasets.values()
                     for f in d.fields.values() if f.role not in ("identifier", "foreign_key")), key=lambda x: -x[0])

    def fld(f):
        return {"field": f.qualified, "label": f.label, "dataset": f.dataset, "role": f.role,
                "datatype": f.datatype, "description": f.description}

    return {
        "metrics": [{"name": mt.name, "business_name": (mt.synonyms or [mt.name])[0], "dataset": mt.dataset,
                     "description": mt.description} for s, mt in metrics[:limit] if s > 0],
        "dimensions": [fld(f) for s, f in fields if s > 0 and f.is_dimension][:limit],
        "measures": [fld(f) for s, f in fields if s > 0 and f.is_measure][:limit],   # for ad-hoc `measures` in a request
    }


@server.tool()
def explain_request(model_name: str, request: dict) -> dict:
    """Show how a semantic request resolves — the fact dataset, the joins it needs and the columns it selects —
    without generating SQL. Useful to check a request before running it."""
    m = model(model_name)
    try:
        fact, joins, metrics, measures, dims, filters = Compiler(m).plan(_request(request))
    except (CompileError, KeyError) as e:
        return {"error": str(e)}
    return {"fact": fact, "joins": [{"relationship": r.name, "from": r.from_dataset, "to": r.to_dataset,
                                     "on": list(zip(r.from_columns, r.to_columns))} for r in joins],
            "metrics": [{"name": mt.name, "expression": mt.expression} for mt in metrics],
            "measures": [f.qualified for f in measures], "dimensions": [f.qualified for f in dims],
            "filters": [{"field": f.qualified, "op": flt.op, "value": flt.value} for f, flt in filters]}


@server.tool()
def compile_query(model_name: str, request: dict) -> dict:
    """Compile a semantic request to SQL for the warehouse without running it."""
    m = model(model_name)
    try:
        c = Compiler(m).compile(_request(request), dialect=DIALECT)
    except (CompileError, KeyError) as e:
        return {"error": str(e)}
    return {"sql": c.sql, "dialect": c.dialect, "fact": c.fact, "joins": c.joins, "columns": c.columns}


@server.tool()
def run_query(model_name: str, request: dict, limit: int = 100) -> dict:
    """Compile a semantic request and run it against the warehouse. Returns the SQL that was run, the columns and
    the rows (capped at `limit`, at most the server maximum). Ask for a small limit unless the user needs everything."""
    m = model(model_name)
    req = _request(request)
    limit = max(1, min(limit, MAX_ROWS))
    if not req.limit or req.limit > limit:
        req.limit = limit
    try:
        c = Compiler(m).compile(req, dialect=DIALECT)
    except (CompileError, KeyError) as e:
        return {"error": str(e)}
    t0 = time.time()
    try:
        res = _engine().query(c.sql, limit)
    except Exception as e:  # noqa: BLE001 - surface the engine's message to the agent
        return {"error": f"warehouse error: {str(e)[:500]}", "sql": c.sql}
    return {"sql": c.sql, "columns": res.columns, "rows": [[_json(v) for v in r] for r in res.rows],
            "row_count": len(res.rows), "truncated": len(res.rows) >= limit, "elapsed_ms": int((time.time() - t0) * 1000)}


def _json(v):
    import datetime as dt
    import decimal
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


# ---------------------------------------------------------------- ASGI app
def build_app():
    """Streamable-HTTP ASGI app at /mcp plus a /healthz, with optional bearer-token check (HELIOS_MCP_TOKEN)."""
    from starlette.applications import Starlette
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Mount, Route

    mcp_app = server.streamable_http_app()
    token = os.environ.get("HELIOS_MCP_TOKEN")

    async def healthz(_):
        return JSONResponse({"ok": True, "helios": __version__, "models": SemanticModel.list_published(ROOT)})

    class Bearer(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if token and request.url.path.startswith("/mcp") and request.headers.get("authorization") != f"Bearer {token}":
                return Response("unauthorized", status_code=401)
            return await call_next(request)

    app = Starlette(routes=[Route("/healthz", healthz), Mount("/", app=mcp_app)],
                    lifespan=getattr(mcp_app, "lifespan", None) or (lambda a: mcp_app.router.lifespan_context(a)))
    app.add_middleware(Bearer)
    return app


app = build_app()