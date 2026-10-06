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

Model source: published Apache Ossie JSON if available, otherwise the latest
run's proposal converted to the same canonical in-memory semantic model.

Environment:
  HELIOS_MCP_TOKEN          required bearer token clients must send
  HELIOS_MCP_ALLOWED_HOSTS  comma-separated public hostnames this app is served on (DNS-rebinding allowlist)
  HELIOS_ROOT, HELIOS_RUNS_DIR, IMPALA_*, ATLAS_*   as for the rest of helios
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from helios_core import __version__
from helios_core import audit, authz
from helios_core import runs as runstore
from helios_core.artifacts import ArtifactStore
from helios_core.config import atlas_config, impala_config
from helios_core.compiler import (
    CompileError,
    Compiler,
    Filter,
    FilterValueTypeError,
    SemanticRequest,
)
from helios_core.data_authorization import (
    DataAction,
    DataPolicy,
    DataResource,
    ImpalaProxyDataAuthorizer,
)
from helios_core.delegation import (
    DelegatedContext,
    DelegationError,
    verify_assertion,
)
from helios_core.identity import Principal, PrincipalKind
from helios_core.metadata import SQLiteMetadataRepository, TraceSpan
from helios_core.ossie import SemanticModel, build as build_ossie
from helios_core.tracing import sanitized, utcnow

LOGGER = logging.getLogger(__name__)
ROOT = runstore.ROOT
artifact_store = ArtifactStore(ROOT)
_initial_impala_config = impala_config()
data_policy = DataPolicy(
    ImpalaProxyDataAuthorizer(
        bool(
            _initial_impala_config
            and _initial_impala_config.proxy_delegation
        )
    )
)
metadata_repository = SQLiteMetadataRepository()
_metadata_findings = metadata_repository.integrity_check()
if _metadata_findings != ("ok",):
    LOGGER.error(
        "metadata is unavailable; start the Helios API to initialize or "
        "repair it before MCP findings=%s",
        _metadata_findings,
    )


@dataclass(frozen=True)
class MCPCaller:
    principal: Principal
    model_id: str | None
    organization_id: str | None
    request_id: str | None = None
    session_id: str | None = None
    trace_run_id: str | None = None
    semantic_revision_id: str | None = None


_caller_context: ContextVar[MCPCaller | None] = ContextVar(
    "helios_mcp_caller", default=None
)

ENGINE_DIALECT = {"impala": "hive", "hive": "hive", "spark": "spark"}   # sqlglot write dialects

# ---------------------------------------------------------------------------- model store
class ModelStore:
    """Load published or proposed artifacts into one semantic model shape."""

    def __init__(self):
        self._cache: dict[str, tuple[float, SemanticModel]] = {}

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

    def load(
        self, name: str | None = None
    ) -> tuple[dict, SemanticModel]:
        srcs = self.sources()
        if not srcs:
            raise ValueError("no model available: publish a model or run the propose job")
        if name:
            src = next((s for s in srcs if s["name"] == name), None)
            if src is None:
                raise ValueError(f"model {name!r} is not available")
        else:
            src = srcs[0]
        caller = _caller_context.get()
        if (
            caller
            and caller.semantic_revision_id
            and caller.model_id == src["name"]
        ):
            artifact_store.revision_ossie_path(
                src["name"], caller.semantic_revision_id, "yaml"
            )
            src = {
                "name": src["name"],
                "path": str(
                    artifact_store.revision_ossie_path(
                        src["name"],
                        caller.semantic_revision_id,
                        "json",
                    )
                ),
                "status": "published revision",
                "revision_id": caller.semantic_revision_id,
            }
        mtime = os.path.getmtime(src["path"])
        cached = self._cache.get(src["path"])
        if not cached or cached[0] != mtime:
            with open(src["path"]) as f:
                document = json.load(f)
            if any(
                "table" in dataset
                for dataset in document.get("datasets", [])
            ):
                document, _ = build_ossie(
                    document,
                    document.get("name") or src["name"],
                    document.get("description") or "",
                    model_id=src["name"],
                )
            self._cache[src["path"]] = (
                mtime,
                SemanticModel(document),
            )
        return src, self._cache[src["path"]][1]


store = ModelStore()


# ---------------------------------------------------------------------------- search
def _tokens(s: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2}


def search(
    model: SemanticModel, question: str, limit: int = 10
) -> dict:
    q = _tokens(question)
    hits: list[tuple[int, dict]] = []
    for dataset in model.datasets.values():
        dataset_terms = (
            _tokens(dataset.name)
            | _tokens(dataset.source)
            | _tokens(dataset.description)
        )
        for synonym in dataset.synonyms:
            dataset_terms |= _tokens(synonym)
        score = len(q & dataset_terms)
        if score:
            hits.append(
                (
                    score * 2,
                    {
                        "kind": "dataset",
                        "name": dataset.name,
                        "table": dataset.source,
                        "type": dataset.kind,
                        "description": dataset.description,
                    },
                )
            )
        for field in dataset.fields.values():
            if field.role in ("identifier", "foreign_key"):
                continue
            field_terms = (
                _tokens(field.name)
                | _tokens(field.label)
                | _tokens(field.description)
            )
            for synonym in field.synonyms:
                field_terms |= _tokens(synonym)
            score = len(q & field_terms)
            if score:
                hits.append(
                    (
                        score,
                        {
                            "kind": "field",
                            "role": field.role,
                            "name": field.label,
                            "datatype": field.datatype,
                            "column": (
                                f"{dataset.source}.{field.name}"
                            ),
                            "description": field.description,
                        },
                    )
                )
    for metric in model.metrics.values():
        metric_terms = (
            _tokens(metric.name) | _tokens(metric.description)
        )
        for synonym in metric.synonyms:
            metric_terms |= _tokens(synonym)
        score = len(q & metric_terms)
        if score:
            hits.append(
                (
                    score * 2,
                    {
                        "kind": "metric",
                        "name": metric.name,
                        "dataset": metric.dataset,
                        "datatype": metric.datatype,
                        "expression": metric.expression,
                        "description": metric.description,
                    },
                )
            )
    hits.sort(key=lambda h: -h[0])
    return {"matches": [h for _, h in hits[:limit]]}


def _error(code: str, message: str, *, retryable: bool = False) -> dict:
    return {"error": code, "message": message, "retryable": retryable}


def _semantic_error_message(exc: BaseException) -> str:
    detail = (
        str(exc.args[0])
        if isinstance(exc, KeyError) and exc.args
        else str(exc)
    )
    return (
        f"{detail}. Use an exact metric name from search_semantics and use "
        "database.table.column identifiers for dimensions and filters."
    )


def _impala_filter_type_error(exc: BaseException) -> str | None:
    detail = str(exc)
    if type(exc).__name__ != "HiveServer2Error":
        return None
    if "AnalysisException:" not in detail or not any(
        marker in detail
        for marker in (
            "are not comparable",
            "Incompatible return types",
            "incompatible types",
        )
    ):
        return None
    return (
        "Impala rejected a generated filter because its value type did not "
        "match the semantic field type. Search for the field again, preserve "
        "the datatype returned by search_semantics, and use JSON numbers "
        "without quotes for numeric fields."
    )


def _diagnostic_error(
    code: str,
    message: str,
    exc: BaseException,
    *,
    stage: str,
    retryable: bool = False,
    excluded_values: tuple[str, ...] = (),
) -> dict:
    result = _error(code, message, retryable=retryable)
    result["_audit_diagnostics"] = audit.exception_diagnostics(
        exc,
        stage=stage,
        excluded_values=excluded_values,
    )
    return result


def _semantic_request(
    metrics: list[str],
    dimensions: list[str] | None,
    filters: list[dict] | None,
    limit: int,
) -> SemanticRequest:
    semantic_filters: list[Filter] = []
    for item in filters or []:
        if not isinstance(item, dict):
            raise CompileError("each filter must be an object")
        field = item.get("field") or item.get("column")
        operator = item.get("op")
        if not isinstance(field, str) or not field:
            raise CompileError("each filter requires column or field")
        if not isinstance(operator, str) or not operator:
            raise CompileError("each filter requires op")
        semantic_filters.append(
            Filter(field, operator, item.get("value"))
        )
    return SemanticRequest(
        metrics=list(metrics),
        dimensions=list(dimensions or []),
        filters=semantic_filters,
        limit=min(max(limit, 1), 1000),
    )


def _compile_semantic(
    model: SemanticModel,
    metrics: list[str],
    dimensions: list[str] | None,
    filters: list[dict] | None,
    limit: int,
    engine: str,
) -> tuple[dict, set[str]]:
    dialect = ENGINE_DIALECT.get(engine.lower())
    if dialect is None:
        raise CompileError(
            f"unsupported engine {engine!r}; use impala, hive, or spark"
        )
    compiled = Compiler(model).compile(
        _semantic_request(metrics, dimensions, filters, limit),
        dialect=dialect,
    )
    dataset_names = {compiled.fact}
    for relationship_name in compiled.joins:
        relationship = model.relationships[relationship_name]
        dataset_names.update(
            {
                relationship.from_dataset,
                relationship.to_dataset,
            }
        )
    assets = {
        model.datasets[dataset_name].source
        for dataset_name in dataset_names
    }
    fact_source = model.datasets[compiled.fact].source
    return (
        {
            "sql": compiled.sql,
            "dataset": fact_source,
            "joins": sorted(assets - {fact_source}),
            "columns": compiled.columns,
            "dialect": compiled.dialect,
        },
        assets,
    )


def _resource(model, action: authz.Action) -> authz.Resource:
    prefix = action.value.split(".", 1)[0]
    if prefix in {"model", "query"}:
        return authz.Resource("model", model.id, model.organization_id)
    if prefix == "datasource":
        return authz.Resource(
            "datasource", "referenced", model.organization_id, model.id
        )
    resource_id = getattr(model, f"{prefix}_id", None) or prefix
    return authz.Resource(
        prefix, resource_id, model.organization_id, model.id
    )


def _authorize(
    action: authz.Action,
    requested_model: str | None = None,
):
    caller = _caller_context.get()
    if caller is None:
        raise PermissionError("authenticated MCP caller context is required")
    model_id = requested_model or caller.model_id
    if not model_id:
        raise PermissionError("an authorized model context is required")
    if caller.model_id and model_id != caller.model_id:
        raise PermissionError("the requested model does not match caller context")
    model = metadata_repository.model(model_id)
    if model is None:
        raise LookupError("model not found")
    if caller.organization_id and model.organization_id != caller.organization_id:
        raise PermissionError("the requested model is outside caller context")
    policy = authz.Policy(
        metadata_repository.grants_for_principal(caller.principal.id)
    )
    policy.require(caller.principal, action, _resource(model, action))
    return caller, model


def _authorized_tool(
    action: authz.Action,
    requested_model: str | None,
) -> tuple[MCPCaller | None, Any | None, dict | None]:
    try:
        caller, model = _authorize(action, requested_model)
        return caller, model, None
    except LookupError as exc:
        return None, None, _error("model_not_found", str(exc))
    except (PermissionError, authz.AuthorizationDenied) as exc:
        return None, None, _error("authorization_denied", str(exc))


def _audited_tool(name: str):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                context = audit.current_context()
                LOGGER.exception(
                    "MCP tool %s failed request_id=%s error_type=%s",
                    name,
                    context.request_id if context else None,
                    type(exc).__name__,
                )
                audit.emit(
                    metadata_repository,
                    component="mcp",
                    event_type="mcp.tool",
                    action=name,
                    outcome="error",
                    severity="error",
                    resource_type="model",
                    resource_id=kwargs.get("model"),
                    duration_ms=(time.perf_counter() - started) * 1000,
                    summary=f"MCP tool {name} failed",
                    details={"error_type": type(exc).__name__},
                )
                raise
            error_code = (
                result.get("error")
                if isinstance(result, dict)
                else None
            )
            details: dict[str, Any] = {}
            diagnostics = (
                result.pop("_audit_diagnostics", None)
                if isinstance(result, dict)
                else None
            )
            if error_code:
                details["error_code"] = error_code
            if isinstance(diagnostics, dict):
                details["diagnostics"] = diagnostics
            if (
                name == "run_query"
                and isinstance(result, dict)
                and isinstance(result.get("rows"), list)
            ):
                details["row_count"] = len(result["rows"])
                if isinstance(result.get("sql"), str):
                    details["compiled_fingerprint"] = audit.sql_fingerprint(
                        result["sql"]
                    )
            audit.emit(
                metadata_repository,
                component="mcp",
                event_type="mcp.tool",
                action=name,
                outcome=(
                    "denied"
                    if error_code
                    in {
                        "authorization_denied",
                        "authentication_required",
                        "data_authorization_denied",
                        "proxy_delegation_denied",
                        "query_denied",
                    }
                    else "error"
                    if error_code
                    else "success"
                ),
                severity="warning" if error_code else "info",
                resource_type="model",
                resource_id=kwargs.get("model"),
                model_id=kwargs.get("model"),
                duration_ms=(time.perf_counter() - started) * 1000,
                summary=f"MCP tool {name} completed",
                details=details,
            )
            return result

        return wrapped

    return decorate


class MCPTraceMiddleware:
    """Persist the raw tools/call request independently of the agent loop."""

    async def __call__(self, ctx, call_next):
        if ctx.method != "tools/call":
            return await call_next(ctx)
        params = ctx.params if isinstance(ctx.params, dict) else {}
        meta = params.get("_meta") or params.get("meta") or {}
        caller = _caller_context.get()
        run_id = (
            meta.get("helios_trace_run_id")
            if isinstance(meta, dict)
            else None
        ) or (caller.trace_run_id if caller else None)
        if not run_id:
            return await call_next(ctx)
        parent_span_id = (
            meta.get("helios_parent_span_id")
            if isinstance(meta, dict)
            else None
        )
        try:
            sequence = int(meta.get("helios_sequence") or 0)
        except (TypeError, ValueError):
            sequence = 0
        if sequence <= 0:
            sequence = int(time.time_ns() % 1_000_000_000)
        started = utcnow()
        result = None
        error = None
        try:
            result = await call_next(ctx)
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            completed = utcnow()
            try:
                metadata_repository.append_trace_span(
                    TraceSpan(
                        id=str(uuid.uuid4()),
                        run_id=run_id,
                        parent_span_id=parent_span_id,
                        sequence=sequence,
                        component="mcp-server",
                        kind="tool",
                        name=str(params.get("name") or "unknown"),
                        status="error" if error else "success",
                        started_at=started,
                        completed_at=completed,
                        latency_ms=(
                            completed - started
                        ).total_seconds() * 1000,
                        input=sanitized({
                            "raw_params": params,
                            "arguments": params.get("arguments"),
                        }),
                        output=sanitized(result),
                        attributes={
                            "request_id": str(ctx.request_id),
                            "ground_truth": True,
                            "semantic_revision_id": (
                                caller.semantic_revision_id
                                if caller
                                else None
                            ),
                        },
                        error=str(error) if error else None,
                    )
                )
            except Exception:
                LOGGER.exception(
                    "failed to persist MCP server trace run_id=%s",
                    run_id,
                )


# ---------------------------------------------------------------------------- server + tools
server = MCPServer(
    name="helios",
    version=__version__,
    instructions=(
        "helios exposes a governed semantic layer over a Cloudera data "
        "warehouse. Always call search_semantics before compile_query or "
        "run_query. Copy metric names exactly from search results; never "
        "invent or convert them to physical column names. Dimensions and "
        "filter columns must use the exact database.table.column identifier "
        "returned by search_semantics or describe. Use describe for "
        "definitions. If a semantic query is invalid, search again and "
        "correct the identifiers; do not describe it as an Impala failure."
    ),
    middleware=[MCPTraceMiddleware()],
)


@server.tool(description="List the semantic models this server can answer from, and whether they are published or an unreviewed proposal.")
@_audited_tool("list_models")
def list_models() -> list[dict] | dict:
    caller = _caller_context.get()
    if caller is None:
        return _error("authentication_required", "authenticated MCP caller context is required")
    visible = []
    for source in store.sources():
        _, _, denied = _authorized_tool(authz.Action.MODEL_READ, source["name"])
        if denied is None:
            visible.append({"name": source["name"], "status": source["status"]})
    return visible


@server.tool(description="Datasets, fields, metrics and relationships of a model. Omit model to use the default.")
@_audited_tool("describe_model")
def describe_model(model: str | None = None) -> dict:
    _, authorized_model, denied = _authorized_tool(
        authz.Action.SEMANTIC_READ, model
    )
    if denied:
        return denied
    src, semantic_model = store.load(authorized_model.id)
    description = semantic_model.describe()
    for item in description["datasets"]:
        dataset = semantic_model.datasets[item["name"]]
        item["table"] = dataset.source
        for field in item["fields"]:
            field["column"] = field["name"]
    return {
        "model": src["name"],
        "status": src["status"],
        **description,
    }


@server.tool(description="Resolve a natural-language question to the datasets, fields, metrics and glossary terms it involves.")
@_audited_tool("search_semantics")
def search_semantics(question: str, limit: int = 10, model: str | None = None) -> dict:
    _, authorized_model, denied = _authorized_tool(
        authz.Action.SEMANTIC_READ, model
    )
    if denied:
        return denied
    _, semantic_model = store.load(authorized_model.id)
    return search(semantic_model, question, min(max(limit, 1), 100))


@server.tool(description="Definition and context for one thing by name: a dataset, a column (database.table.column), a metric or a glossary term.")
@_audited_tool("describe")
def describe(name: str, model: str | None = None) -> dict:
    _, authorized_model, denied = _authorized_tool(
        authz.Action.SEMANTIC_READ, model
    )
    if denied:
        return denied
    _, semantic_model = store.load(authorized_model.id)
    n = name.lower()
    for dataset in semantic_model.datasets.values():
        dataset_names = {
            dataset.name.lower(),
            dataset.source.lower(),
            *(synonym.lower() for synonym in dataset.synonyms),
        }
        if n in dataset_names:
            return {
                "kind": "dataset",
                "table": dataset.source,
                "name": dataset.name,
                "type": dataset.kind,
                "description": dataset.description,
                "primary_key": dataset.primary_key,
                "fields": [
                    {
                        "column": field.name,
                        "name": field.label,
                        "role": field.role,
                        "datatype": field.datatype,
                        "description": field.description,
                    }
                    for field in dataset.fields.values()
                ],
            }
    try:
        metric = semantic_model.metric(name)
        return {
            "kind": "metric",
            "name": metric.name,
            "dataset": metric.dataset,
            "expression": metric.expression,
            "description": metric.description,
            "datatype": metric.datatype,
        }
    except KeyError:
        pass
    try:
        field = semantic_model.field(name)
        dataset = semantic_model.datasets[field.dataset]
        return {
            "kind": "field",
            "dataset": dataset.source,
            "column": field.name,
            "name": field.label,
            "role": field.role,
            "datatype": field.datatype,
            "description": field.description,
        }
    except KeyError:
        pass
    return _error(
        "semantic_not_found",
        f"nothing named {name!r} is available in the authorized model",
    )


@server.tool(description="Compile a semantic request into SQL. Call search_semantics first and copy metric names exactly from its results. "
                         "Dimensions and filter columns must be exact database.table.column identifiers returned by search_semantics or describe; "
                         "never invent or shorten identifiers. Preserve each returned field datatype in filter JSON: use JSON numbers without quotes "
                         "for numeric fields, booleans for Boolean fields, and ISO strings for Date/Timestamp fields. "
                         "filters: [{column, op, value}] with op in = != < <= > >= IN LIKE BETWEEN; "
                         "engine: impala | hive | spark.")
@_audited_tool("compile_query")
def compile_query(metrics: list[str], dimensions: list[str] | None = None, filters: list[dict] | None = None,
                  limit: int = 100, engine: str = "impala", model: str | None = None) -> dict:
    _, authorized_model, denied = _authorized_tool(
        authz.Action.QUERY_COMPILE, model
    )
    if denied:
        return denied
    _, semantic_model = store.load(authorized_model.id)
    try:
        compiled, _ = _compile_semantic(
            semantic_model,
            metrics,
            dimensions,
            filters,
            limit,
            engine,
        )
        return compiled
    except FilterValueTypeError as exc:
        return _error(
            "invalid_filter_value_type",
            _semantic_error_message(exc),
        )
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        return _error(
            "invalid_semantic_query",
            _semantic_error_message(exc),
        )


@server.tool(description="Compile a semantic request and run it on the warehouse. Call search_semantics first, copy metric names exactly, and use "
                         "full database.table.column identifiers for dimensions and filters. Semantic validation happens before Impala execution. "
                         "Filter JSON values must preserve the datatype returned by search_semantics; numeric values must not be quoted. "
                         "Same arguments as compile_query. Returns columns and rows.")
@_audited_tool("run_query")
def run_query(metrics: list[str], dimensions: list[str] | None = None, filters: list[dict] | None = None,
              limit: int = 100, model: str | None = None) -> dict:
    from helios_core.engines import (
        ImpalaAuthenticationError,
        ImpalaEngine,
        ImpalaProxyDelegationError,
    )
    caller, authorized_model, denied = _authorized_tool(
        authz.Action.QUERY_EXECUTE, model
    )
    if denied:
        return denied
    _, semantic_model = store.load(authorized_model.id)
    cfg = impala_config()
    if cfg is None:
        return _error(
            "impala_unavailable",
            "Impala proxy credentials are not configured on the server",
            retryable=True,
        )
    safe_limit = min(max(limit, 1), 1000)
    compiled_sql = ""
    try:
        compiled, assets = _compile_semantic(
            semantic_model,
            metrics,
            dimensions,
            filters,
            safe_limit,
            "impala",
        )
        compiled_sql = compiled["sql"]
        resources: dict[tuple[str, str], DataResource] = {}
        for asset in assets:
            references = [
                reference
                for reference in authorized_model.data_sources
                if not reference.selected_assets
                or asset in reference.selected_assets
            ]
            if len(references) != 1:
                return _error(
                    "data_authorization_denied",
                    f"dataset {asset!r} is not uniquely authorized by the model",
                )
            resource = DataResource(references[0].data_source_id, asset)
            resources[(resource.data_source_id, asset)] = resource
        for resource in resources.values():
            access = data_policy.can_access(
                caller.principal,
                resource,
                DataAction.QUERY_EXECUTE,
            )
            if not access.allowed:
                return _error("data_authorization_denied", access.reason)
        res = ImpalaEngine(cfg).query(
            compiled["sql"],
            safe_limit,
            delegated_user=caller.principal.subject,
        )
    except ImpalaAuthenticationError as exc:
        LOGGER.warning(
            "delegated Impala authentication failed request_id=%s",
            caller.request_id,
        )
        try:
            service_authenticated = ImpalaEngine(cfg).ping()
        except ImpalaAuthenticationError as service_exc:
            return _diagnostic_error(
                "workload_authentication_failed",
                "CDW rejected the configured workload username or password",
                service_exc,
                stage="impala_service_authentication",
            )
        except Exception as diagnostic_exc:
            LOGGER.exception(
                "Impala service-account diagnostic failed request_id=%s",
                caller.request_id,
            )
            return _diagnostic_error(
                "impala_authentication_failed",
                "CDW rejected the delegated query and service-account "
                "authentication could not be confirmed",
                diagnostic_exc,
                stage="impala_authentication_diagnostic",
                retryable=True,
            )
        if service_authenticated:
            return _diagnostic_error(
                "proxy_delegation_denied",
                "The workload account connected to CDW, but CDW rejected "
                "delegation to the authenticated Helios user",
                exc,
                stage="impala_proxy_delegation",
            )
        return _diagnostic_error(
            "impala_authentication_failed",
            "CDW authentication could not be confirmed",
            exc,
            stage="impala_authentication",
            retryable=True,
        )
    except ImpalaProxyDelegationError as exc:
        return _diagnostic_error(
            "proxy_delegation_denied",
            str(exc),
            exc,
            stage="impala_effective_user",
        )
    except FilterValueTypeError as exc:
        return _error(
            "invalid_filter_value_type",
            _semantic_error_message(exc),
        )
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        return _error(
            "invalid_semantic_query",
            _semantic_error_message(exc),
        )
    except PermissionError as exc:
        return _error("query_denied", str(exc))
    except Exception as exc:
        context = audit.current_context()
        filter_error = _impala_filter_type_error(exc)
        if filter_error:
            LOGGER.warning(
                "Impala rejected generated filter types request_id=%s",
                context.request_id if context else None,
            )
            return _diagnostic_error(
                "invalid_filter_value_type",
                filter_error,
                exc,
                stage="impala_query_analysis",
                excluded_values=(compiled_sql,),
            )
        LOGGER.exception(
            "delegated Impala query failed request_id=%s error_type=%s",
            context.request_id if context else None,
            type(exc).__name__,
        )
        return _diagnostic_error(
            "query_unavailable",
            "The delegated Impala query could not be completed",
            exc,
            stage="impala_query",
            retryable=True,
            excluded_values=(compiled_sql,),
        )
    return {
        "sql": compiled["sql"],
        "dataset": compiled["dataset"],
        "joins": compiled["joins"],
        "data_source_ids": sorted({
            resource.data_source_id for resource in resources.values()
        }),
        "columns": res.columns,
        "rows": [[_json(v) for v in r] for r in res.rows],
    }


@server.tool(description="Atlas lineage for a column given as database.table.column: the processes and datasets upstream and downstream.")
@_audited_tool("explain_lineage")
def explain_lineage(column: str, depth: int = 3, model: str | None = None) -> dict:
    _, _, denied = _authorized_tool(authz.Action.DATASOURCE_READ, model)
    if denied:
        return denied
    from helios_core.atlas import AtlasClient
    cfg = atlas_config()
    if cfg is None:
        return _error(
            "atlas_unavailable",
            "Atlas is not configured on the server",
            retryable=True,
        )
    parts = column.split(".")
    if len(parts) != 3:
        return _error(
            "invalid_semantic_reference",
            "column must be database.table.column",
        )
    a = AtlasClient(cfg)
    hit = a.find_column(*parts)
    if not hit:
        return _error(
            "semantic_not_found",
            f"{column} was not found in Atlas",
        )
    lin = a.lineage(hit[0], depth)
    ents = {g: {"type": e.get("typeName"), "name": e.get("displayText")} for g, e in (lin.get("guidEntityMap") or {}).items()}
    return {"column": column, "entities": ents,
            "edges": [{"from": r["fromEntityId"], "to": r["toEntityId"]} for r in lin.get("relations", [])]}


def _json(v):
    return v if isinstance(v, (int, float, str, bool)) or v is None else str(v)


@server.custom_route("/healthz", methods=["GET"])
async def healthz(_: Request) -> JSONResponse:
    try:
        metadata_findings = metadata_repository.ping()
        if metadata_findings != ("ok",):
            return JSONResponse(
                {
                    "status": "unavailable",
                    "version": __version__,
                    "components": {
                        "metadata_repository": {
                            "status": "unavailable",
                            "message": "metadata repository is not readable",
                        }
                    },
                },
                status_code=503,
            )
        srcs = store.sources()
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "model_count": len(srcs),
                "components": {
                    "metadata_repository": {"status": "healthy"}
                },
            }
        )
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "error": str(e)}, status_code=503)


@server.custom_route("/", methods=["GET"])
async def service_root(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "service": "helios-mcp",
            "status": "ok",
            "health": "/healthz",
            "endpoint": "/mcp",
        }
    )


# ---------------------------------------------------------------------------- ASGI app
_allowed = [h.strip() for h in os.environ.get("HELIOS_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed + ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"],
    allowed_origins=[f"https://{h}" for h in _allowed] + ["http://127.0.0.1", "http://localhost"],
)
_mcp_app = server.streamable_http_app(transport_security=_security, stateless_http=True, json_response=True)

# Set by the Application launcher (app.py), so importing this module elsewhere loads nothing.
if os.environ.get("HELIOS_MCP_WARM_EMBEDDINGS") == "1":
    from helios_core.index import evidence as _evidence

    _evidence.warm_up()

_TOKEN = os.environ.get("HELIOS_MCP_TOKEN")
_DELEGATION_SECRET = os.environ.get("HELIOS_MCP_DELEGATION_SECRET")
_DEFAULT_PRINCIPAL_ID = os.environ.get("HELIOS_MCP_DEFAULT_PRINCIPAL")


def _request_caller(headers: dict[str, str]) -> MCPCaller | None:
    assertion = headers.get("x-helios-principal-assertion")
    if assertion:
        if not _DELEGATION_SECRET:
            raise DelegationError("MCP delegation is not configured")
        delegated: DelegatedContext = verify_assertion(
            _DELEGATION_SECRET, assertion
        )
        return MCPCaller(
            delegated.principal,
            delegated.model_id,
            delegated.organization_id,
            delegated.request_id,
            delegated.session_id,
            delegated.trace_run_id,
            delegated.semantic_revision_id,
        )
    if _DEFAULT_PRINCIPAL_ID:
        issuer, separator, subject = _DEFAULT_PRINCIPAL_ID.partition(":")
        if not separator or not issuer or not subject:
            raise DelegationError("invalid HELIOS_MCP_DEFAULT_PRINCIPAL")
        return MCPCaller(
            Principal(
                issuer=issuer,
                subject=subject,
                kind=PrincipalKind.AGENT,
                display_name=subject,
            ),
            headers.get("x-helios-model-id"),
            None,
        )
    return None


# Evidence from crawled documents (CR-11). Both tools need query.execute on the
# caller's model, like run_query. The crawl index is read as this server's
# workload user, so Ranger does not filter document text per caller.

MAX_EVIDENCE_TEXT = 1500


@server.tool(description=(
    "Search crawled documents (emails, chats, PDF reports) for passages about a topic, by meaning. "
    "Use it for what people wrote or said: complaints, reasons, requests, approvals. "
    "Pass the user's question in their own words as the query; do not shorten it to keywords. "
    "Returns one passage per document: a whole email, an excerpt of a chat, or a PDF page. "
    "Each passage lists the entities its document is linked to, with warehouse keys such as "
    "tpcds.customer:c_customer_sk=12345 that can be used as filters in run_query; "
    "that key means field tpcds.customer.c_customer_sk with value 12345."
))
@_audited_tool("search_evidence")
def search_evidence(query: str, limit: int = 8, model: str | None = None) -> dict:
    _, _, denied = _authorized_tool(authz.Action.QUERY_EXECUTE, model)
    if denied:
        return denied
    from helios_core.index import evidence

    try:
        found = evidence.search(query, min(max(limit, 1), 20))
    except evidence.EvidenceUnavailable as exc:
        return _error(
            "evidence_unavailable",
            f"Crawled documents cannot be searched: {exc}",
        )
    for segment in found:
        if len(segment["text"]) > MAX_EVIDENCE_TEXT:
            segment["text"] = segment["text"][:MAX_EVIDENCE_TEXT] + "…"
    return {"query": query, "count": len(found), "segments": found}


@server.tool(description=(
    "What crawled documents claim about an entity (a customer, item, store, sale or return): "
    "the claims extracted from them, such as PACKAGING_DAMAGED or REFUND_REQUESTED, with the "
    "supporting passages. Give a name, part of a name, or a warehouse key from search_evidence. "
    "Optionally keep one predicate."
))
@_audited_tool("entity_claims")
def entity_claims(entity: str, predicate: str | None = None, model: str | None = None) -> dict:
    _, _, denied = _authorized_tool(authz.Action.QUERY_EXECUTE, model)
    if denied:
        return denied
    from helios_core.index import evidence, impala_index_store

    index = impala_index_store()
    if index is None:
        return _error(
            "evidence_unavailable",
            "The crawl index is not configured on the server",
            retryable=True,
        )
    return evidence.entity_claims(index, entity, predicate)


# One failed-authentication audit event per client per minute; the rest are counted.
_auth_failure_limiter = audit.RepeatLimiter(interval=60.0)


async def app(scope, receive, send):
    """Bearer-token gate around the MCP app; /healthz stays open."""
    context_token = None
    audit_token = None
    if scope["type"] == "http" and scope.get("path") not in {
        "/",
        "/healthz",
    }:
        headers = {
            k.decode().lower(): v.decode()
            for k, v in scope.get("headers", [])
        }
        client = (scope.get("client") or ("unknown",))[0]
        if not _TOKEN:
            repeats = _auth_failure_limiter.allow(f"error:{client}")
            if repeats is not None:
                audit.emit(
                    metadata_repository,
                    component="mcp",
                    event_type="mcp.transport",
                    action="authenticate",
                    outcome="error",
                    severity="error",
                    http_status=503,
                    summary="MCP authentication is not configured",
                    details={"repeats_not_recorded": repeats},
                )
            resp = JSONResponse(
                {"error": "mcp_authentication_not_configured"},
                status_code=503,
            )
            await resp(scope, receive, send)
            return
        if headers.get("authorization") != f"Bearer {_TOKEN}":
            repeats = _auth_failure_limiter.allow(f"denied:{client}")
            if repeats is not None:
                audit.emit(
                    metadata_repository,
                    component="mcp",
                    event_type="mcp.transport",
                    action="authenticate",
                    outcome="denied",
                    severity="warning",
                    http_status=401,
                    summary="MCP bearer authentication failed",
                    details={"repeats_not_recorded": repeats},
                )
            resp = JSONResponse({"error": "unauthorized"}, status_code=401)
            await resp(scope, receive, send)
            return
        try:
            caller = _request_caller(headers)
            context_token = _caller_context.set(caller)
            audit_token = audit.set_context(
                audit.AuditContext(
                    request_id=(
                        caller.request_id
                        if caller and caller.request_id
                        else audit.new_request_id()
                    ),
                    session_id=caller.session_id if caller else None,
                    principal_id=caller.principal.id if caller else None,
                    organization_id=(
                        caller.organization_id if caller else None
                    ),
                    model_id=caller.model_id if caller else None,
                )
            )
        except DelegationError:
            audit.emit(
                metadata_repository,
                component="mcp",
                event_type="mcp.transport",
                action="delegate_principal",
                outcome="denied",
                severity="warning",
                http_status=401,
                summary="MCP Principal delegation failed",
            )
            resp = JSONResponse(
                {"error": "invalid_principal_delegation"}, status_code=401
            )
            await resp(scope, receive, send)
            return
    try:
        await _mcp_app(scope, receive, send)
    finally:
        if audit_token is not None:
            audit.reset_context(audit_token)
        if context_token is not None:
            _caller_context.reset(context_token)