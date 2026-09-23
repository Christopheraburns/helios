"""
helios MCP server (MCP Python SDK 2.x, Streamable HTTP).

Serves the semantic layer to agents:
  list_models        what models are available and where they came from
  describe_model     datasets, dimensions, measures, metrics and relationships of one model
  search_semantics   resolve a natural-language question to datasets, fields, metrics and glossary terms
  describe           definition and context of one dataset, field, metric or glossary term
  compile_query      metrics + dimensions + filters -> SQL for a target engine
  run_query          compile and execute against the warehouse
  explain_lineage    Atlas lineage for a column

Model source: models/published/*.json (helios draft format) if any exist, otherwise the latest run's
propose.json — so the server is usable before the review/publish step is built.

Environment:
  HELIOS_MCP_TOKEN          bearer token clients must send (unset = no auth; only for local testing)
  HELIOS_MCP_ALLOWED_HOSTS  comma-separated public hostnames this app is served on (DNS-rebinding allowlist)
  HELIOS_ROOT, HELIOS_RUNS_DIR, IMPALA_*, ATLAS_*   as for the rest of helios
"""
from __future__ import annotations

import json
import os
import re
from collections import deque
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from helios_core import __version__
from helios_core import runs as runstore
from helios_core.artifacts import ArtifactStore
from helios_core.config import atlas_config, impala_config
from helios_core.data_authorization import (
    DataAction,
    DataPolicy,
    DataResource,
)
from helios_core.identity import Principal, PrincipalKind

ROOT = runstore.ROOT
artifact_store = ArtifactStore(ROOT)
data_policy = DataPolicy()
_UNPROPAGATED_MCP_PRINCIPAL = Principal(
    issuer="helios-mcp",
    subject="unpropagated-caller",
    kind=PrincipalKind.AGENT,
    display_name="MCP caller without platform identity propagation",
)

ENGINE_DIALECT = {"impala": "hive", "hive": "hive", "spark": "spark"}   # sqlglot write dialects

# ---------------------------------------------------------------------------- model store
class ModelStore:
    """Loads helios draft models and answers questions about them. Reloads when files change."""

    def __init__(self):
        self._cache: dict[str, tuple[float, dict]] = {}

    def sources(self) -> list[dict]:
        out = [
            {
                "name": model_id,
                "path": str(artifact_store.published_ossie_path(model_id, "json")),
                "status": "published",
            }
            for model_id in artifact_store.published_model_ids()
        ]
        if not out:
            for r in runstore.list_runs():
                if r["stages"].get("propose"):
                    proposal_path = os.path.join(runstore.RUNS_DIR, r["id"], "propose.json")
                    with open(proposal_path) as handle:
                        proposal = json.load(handle)
                    model_id = proposal.get("model_id") or f"run-{r['id']}"
                    out.append({"name": model_id, "path": proposal_path,
                                "status": "proposed (unreviewed)"})
                    break
        return out

    def load(self, name: str | None = None) -> tuple[dict, dict]:
        srcs = self.sources()
        if not srcs:
            raise ValueError("no model available: publish a model or run the propose job")
        src = next((s for s in srcs if s["name"] == name), srcs[0]) if name else srcs[0]
        mtime = os.path.getmtime(src["path"])
        cached = self._cache.get(src["path"])
        if not cached or cached[0] != mtime:
            with open(src["path"]) as f:
                self._cache[src["path"]] = (mtime, json.load(f))
        return src, self._cache[src["path"]][1]


store = ModelStore()


def _datasets(model: dict) -> dict[str, dict]:
    return {d["table"]: d for d in model.get("datasets", [])}


def _relationships(model: dict) -> list[dict]:
    return [r for r in model.get("relationships", []) if r.get("accepted", True)]


def _find_field(ds: dict, name: str) -> dict | None:
    n = name.lower()
    for f in ds.get("fields", []):
        if f["column"].lower() == n or (f.get("name") or "").lower() == n:
            return f
    return None


def _resolve_column(model: dict, ref: str) -> tuple[str, dict]:
    """'db.table.column' or 'table.column' -> (dataset key, field)."""
    parts = ref.split(".")
    dss = _datasets(model)
    for key, ds in dss.items():
        tbl = key.split(".", 1)[1]
        if (len(parts) == 3 and key == ".".join(parts[:2])) or (len(parts) == 2 and tbl == parts[0]):
            f = _find_field(ds, parts[-1])
            if f:
                return key, f
    raise ValueError(f"unknown column {ref}")


# ---------------------------------------------------------------------------- compiler
def _join_path(model: dict, start: str, target: str) -> list[dict] | None:
    """BFS over accepted relationships (either direction), up to 3 hops."""
    if start == target:
        return []
    rels = _relationships(model)
    adj: dict[str, list[tuple[str, dict]]] = {}
    for r in rels:
        adj.setdefault(r["from"], []).append((r["to"], r))
        adj.setdefault(r["to"], []).append((r["from"], r))
    q, seen = deque([(start, [])]), {start}
    while q:
        node, path = q.popleft()
        if len(path) >= 3:
            continue
        for nxt, r in adj.get(node, []):
            if nxt in seen:
                continue
            if nxt == target:
                return path + [r]
            seen.add(nxt)
            q.append((nxt, path + [r]))
    return None


def compile_sql(model: dict, metrics: list[str], dimensions: list[str], filters: list[dict],
                limit: int, engine: str) -> dict:
    ms = {m["name"].lower(): m for m in model.get("metrics", [])}
    chosen = []
    for name in metrics:
        m = ms.get(name.lower())
        if not m:
            raise ValueError(f"unknown metric {name!r}; known: {sorted(mm['name'] for mm in ms.values())}")
        chosen.append(m)
    facts = {m["dataset"] for m in chosen}
    if len(facts) != 1:
        raise ValueError("all metrics in one query must come from the same dataset; got " + ", ".join(sorted(facts)))
    fact = facts.pop()

    alias = {fact: "f"}
    joins: list[str] = []

    def ensure_joined(table: str) -> str:
        if table in alias:
            return alias[table]
        path = _join_path(model, fact, table)
        if path is None:
            raise ValueError(f"no join path from {fact} to {table}")
        cur = fact
        for r in path:
            nxt = r["to"] if r["from"] == cur else r["from"]
            if nxt not in alias:
                alias[nxt] = f"t{len(alias)}"
                l, lc = (r["from"], r["from_column"]) if r["from"] == cur else (r["to"], r["to_column"])
                rt, rc = (r["to"], r["to_column"]) if r["from"] == cur else (r["from"], r["from_column"])
                joins.append(f"LEFT JOIN {rt} {alias[rt]} ON {alias[l]}.{lc} = {alias[rt]}.{rc}")
            cur = nxt
        return alias[table]

    select, group = [], []
    for d in dimensions:
        key, f = _resolve_column(model, d)
        a = ensure_joined(key)
        expr = f"{a}.{f['column']}"
        select.append(f"{expr} AS {f['column']}")
        group.append(expr)
    for m in chosen:
        expr = re.sub(r"\b([a-z][a-z0-9_]*)\b",
                      lambda mo: f"f.{mo.group(1)}" if _find_field(_datasets(model)[fact], mo.group(1)) else mo.group(1),
                      m["expression"])
        select.append(f"{expr} AS {re.sub(r'[^a-z0-9]+', '_', m['name'].lower()).strip('_')}")

    where = []
    for flt in filters or []:
        key, f = _resolve_column(model, flt["column"])
        a = ensure_joined(key)
        op = flt.get("op", "=").upper()
        if op not in ("=", "!=", "<", "<=", ">", ">=", "IN", "LIKE", "BETWEEN"):
            raise ValueError(f"unsupported operator {op}")
        v = flt["value"]
        if op == "IN":
            vals = ", ".join(_lit(x) for x in (v if isinstance(v, list) else [v]))
            where.append(f"{a}.{f['column']} IN ({vals})")
        elif op == "BETWEEN":
            where.append(f"{a}.{f['column']} BETWEEN {_lit(v[0])} AND {_lit(v[1])}")
        else:
            where.append(f"{a}.{f['column']} {op} {_lit(v)}")

    sql = f"SELECT {', '.join(select)}\nFROM {fact} f\n" + ("\n".join(joins) + "\n" if joins else "")
    if where:
        sql += "WHERE " + " AND ".join(where) + "\n"
    if group:
        sql += "GROUP BY " + ", ".join(group) + "\n"
    if chosen:
        sql += f"ORDER BY {len(select)} DESC\n"
    sql += f"LIMIT {int(limit)}"

    dialect = ENGINE_DIALECT.get(engine.lower())
    if dialect is None:
        raise ValueError(f"unknown engine {engine}; choose one of {sorted(ENGINE_DIALECT)}")
    if dialect != "hive":
        import sqlglot
        sql = sqlglot.transpile(sql, read="hive", write=dialect, pretty=True)[0]
    return {"sql": sql, "engine": engine, "dataset": fact, "joins": list(alias)}


def _lit(v: Any) -> str:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


# ---------------------------------------------------------------------------- search
def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2}


def search(model: dict, question: str, limit: int = 10) -> dict:
    q = _tokens(question)
    hits: list[tuple[int, dict]] = []
    for d in model.get("datasets", []):
        score = len(q & (_tokens(d["name"]) | _tokens(d["description"]) | _tokens(d["table"])))
        if score:
            hits.append((score * 2, {"kind": "dataset", "name": d["name"], "table": d["table"], "type": d["kind"],
                                     "description": d["description"]}))
        for f in d.get("fields", []):
            if f["role"] in ("identifier", "foreign_key"):
                continue
            score = len(q & (_tokens(f["name"]) | _tokens(f["description"]) | _tokens(f["column"])))
            if score:
                hits.append((score, {"kind": "field", "role": f["role"], "name": f["name"], "column": f"{d['table']}.{f['column']}",
                                     "description": f["description"]}))
    for m in model.get("metrics", []):
        score = len(q & (_tokens(m["name"]) | _tokens(m["description"])))
        if score:
            hits.append((score * 2, {"kind": "metric", "name": m["name"], "dataset": m["dataset"],
                                     "expression": m["expression"], "description": m["description"]}))
    for t in model.get("glossary_terms", []):
        score = len(q & (_tokens(t["name"]) | _tokens(t.get("definition", ""))))
        if score:
            hits.append((score, {"kind": "glossary_term", "name": t["name"], "definition": t.get("definition", ""),
                                 "columns": t.get("columns", [])}))
    hits.sort(key=lambda h: -h[0])
    return {"question": question, "matches": [h for _, h in hits[:limit]]}


# ---------------------------------------------------------------------------- server + tools
server = MCPServer(
    name="helios",
    version=__version__,
    instructions=("helios exposes a governed semantic layer over a Cloudera data warehouse. Start with "
                  "search_semantics to find the right metrics and dimensions for a question, then compile_query "
                  "or run_query. Use describe for definitions. Metrics are named; dimensions and filters use "
                  "database.table.column."),
)


@server.tool(description="List the semantic models this server can answer from, and whether they are published or an unreviewed proposal.")
def list_models() -> list[dict]:
    return [{"name": s["name"], "status": s["status"]} for s in store.sources()]


@server.tool(description="Datasets, fields, metrics and relationships of a model. Omit model to use the default.")
def describe_model(model: str | None = None) -> dict:
    src, m = store.load(model)
    return {"model": src["name"], "status": src["status"],
            "datasets": [{"table": d["table"], "name": d["name"], "kind": d["kind"], "description": d["description"],
                          "fields": [{"column": f["column"], "name": f["name"], "role": f["role"]} for f in d["fields"]]}
                         for d in m.get("datasets", [])],
            "metrics": [{"name": x["name"], "dataset": x["dataset"], "expression": x["expression"]} for x in m.get("metrics", [])],
            "relationships": [f"{r['from']}.{r['from_column']} -> {r['to']}.{r['to_column']}" for r in _relationships(m)]}


@server.tool(description="Resolve a natural-language question to the datasets, fields, metrics and glossary terms it involves.")
def search_semantics(question: str, limit: int = 10, model: str | None = None) -> dict:
    _, m = store.load(model)
    return search(m, question, limit)


@server.tool(description="Definition and context for one thing by name: a dataset, a column (database.table.column), a metric or a glossary term.")
def describe(name: str, model: str | None = None) -> dict:
    _, m = store.load(model)
    n = name.lower()
    for d in m.get("datasets", []):
        if n in (d["table"].lower(), d["name"].lower()):
            return {"kind": "dataset", **{k: d[k] for k in ("table", "name", "kind", "description", "primary_key", "confidence")},
                    "fields": [{"column": f["column"], "name": f["name"], "role": f["role"], "description": f["description"]} for f in d["fields"]]}
    for x in m.get("metrics", []):
        if x["name"].lower() == n:
            return {"kind": "metric", **x}
    for t in m.get("glossary_terms", []):
        if t["name"].lower() == n:
            return {"kind": "glossary_term", **t}
    try:
        key, f = _resolve_column(m, name)
        return {"kind": "field", "dataset": key, **f}
    except ValueError:
        pass
    return {"error": f"nothing named {name!r}"}


@server.tool(description="Compile a semantic request into SQL. metrics: metric names; dimensions: database.table.column to group by; "
                         "filters: [{column, op, value}] with op in = != < <= > >= IN LIKE BETWEEN; engine: impala | hive | spark.")
def compile_query(metrics: list[str], dimensions: list[str] | None = None, filters: list[dict] | None = None,
                  limit: int = 100, engine: str = "impala", model: str | None = None) -> dict:
    _, m = store.load(model)
    return compile_sql(m, metrics, dimensions or [], filters or [], limit, engine)


@server.tool(description="Compile a semantic request and run it on the warehouse. Same arguments as compile_query. Returns columns and rows.")
def run_query(metrics: list[str], dimensions: list[str] | None = None, filters: list[dict] | None = None,
              limit: int = 100, model: str | None = None) -> dict:
    from helios_core.engines import ImpalaEngine
    src, m = store.load(model)
    data_resource = DataResource(
        data_source_id="unresolved",
        asset=f"model:{src['name']}",
    )
    access = data_policy.can_access(
        _UNPROPAGATED_MCP_PRINCIPAL,
        data_resource,
        DataAction.QUERY_EXECUTE,
    )
    if not access.allowed:
        return {
            "error": "data_authorization_denied",
            "reason": access.reason,
        }
    cfg = impala_config()
    if cfg is None:
        return {"error": "IMPALA_HOST / credentials not configured on the server"}
    c = compile_sql(m, metrics, dimensions or [], filters or [], limit, "impala")
    res = ImpalaEngine(cfg).query(c["sql"], limit)
    return {"sql": c["sql"], "columns": res.columns, "rows": [[_json(v) for v in r] for r in res.rows]}


@server.tool(description="Atlas lineage for a column given as database.table.column: the processes and datasets upstream and downstream.")
def explain_lineage(column: str, depth: int = 3) -> dict:
    from helios_core.atlas import AtlasClient
    cfg = atlas_config()
    if cfg is None:
        return {"error": "ATLAS_* not configured on the server"}
    parts = column.split(".")
    if len(parts) != 3:
        return {"error": "column must be database.table.column"}
    a = AtlasClient(cfg)
    hit = a.find_column(*parts)
    if not hit:
        return {"error": f"{column} not found in Atlas"}
    lin = a.lineage(hit[0], depth)
    ents = {g: {"type": e.get("typeName"), "name": e.get("displayText")} for g, e in (lin.get("guidEntityMap") or {}).items()}
    return {"column": column, "entities": ents,
            "edges": [{"from": r["fromEntityId"], "to": r["toEntityId"]} for r in lin.get("relations", [])]}


def _json(v):
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


@server.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    try:
        srcs = store.sources()
        return JSONResponse({"status": "ok", "version": __version__, "models": [s["name"] for s in srcs]})
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "error": str(e)}, status_code=503)


# ---------------------------------------------------------------------------- ASGI app
_allowed = [h.strip() for h in os.environ.get("HELIOS_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed + ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
    allowed_origins=[f"https://{h}" for h in _allowed] + ["http://127.0.0.1", "http://localhost"],
)
_mcp_app = server.streamable_http_app(transport_security=_security, stateless_http=True, json_response=True)

_TOKEN = os.environ.get("HELIOS_MCP_TOKEN")


async def app(scope, receive, send):
    """Bearer-token gate around the MCP app; /healthz stays open."""
    if scope["type"] == "http" and _TOKEN and scope.get("path") != "/healthz":
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        if headers.get("authorization") != f"Bearer {_TOKEN}":
            resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            await resp(scope, receive, send)
            return
    await _mcp_app(scope, receive, send)