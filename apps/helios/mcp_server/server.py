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

The tools are helios-serve's (burndown 6.2); this server is the adapter that keeps the
console's Principals, organizations and grants, the turn's pinned revision, tracing,
the audit log and the bearer gate in front of them. Models are the published ones only:
an unreviewed proposal is no longer served.

Environment:
  HELIOS_MCP_TOKEN          required bearer token clients must send
  HELIOS_MCP_ALLOWED_HOSTS  comma-separated public hostnames this app is served on (DNS-rebinding allowlist)
  HELIOS_ROOT, HELIOS_RUNS_DIR, IMPALA_*, ATLAS_*   as for the rest of helios
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from contextvars import ContextVar
import dataclasses
from dataclasses import dataclass
from functools import wraps
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from helios_cortex import __version__
from apps.helios.console import audit, authz
from apps.helios import runtime
from helios_cloudera.atlas import atlas_config
from helios_cortex.data_authorization import DataPolicy
from apps.helios.console.delegation import (
    DelegatedContext,
    DelegationError,
    verify_assertion,
)
from apps.helios.audit_sink import RepositoryAuditSink
from helios_cortex import Caller
from helios_cortex.audit_sink import AuditRecord, AuditSink, record_safely, sql_fingerprint
from helios_cortex.engines import dialect_named
from helios_cortex.visibility import AllModels, ModelNotVisible, ModelVisibility, require_visible
from apps.helios.console.identity import Principal, PrincipalKind
from helios_cloudera.impala import impala_config
from apps.helios.console.metadata import SQLiteMetadataRepository, TraceSpan
from helios_cortex.ossie import SemanticModel
from helios_cloudera.workbench import WorkbenchSettings as Settings
import helios_serve
from helios_serve import tools
from apps.helios.console.tracing import sanitized, utcnow

LOGGER = logging.getLogger(__name__)
artifact_store = runtime.artifacts
# Allows run_query only on connections whose identity mode runs it as the caller.
data_policy = DataPolicy()
# Which models a caller may list and use; Helios grants below are checked as well until 2.6.
model_visibility: ModelVisibility = AllModels()
# Where tool audit records go; None is the console's SQLite audit log (see _audit_sink).
audit_sink: AuditSink | None = None
metadata_repository = SQLiteMetadataRepository.from_settings(runtime.settings)
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
    # The end user's warehouse token, for connections in token identity mode. This
    # server's transport does not carry one; helios-mcp will (burndown 6.x).
    token: str | None = dataclasses.field(default=None, repr=False)

    @property
    def caller(self) -> Caller:
        """The library-facing caller: the principal's subject is the username."""
        return Caller(
            username=self.principal.subject,
            token=self.token,
            attributes={"principal_id": self.principal.id},
        )


_caller_context: ContextVar[MCPCaller | None] = ContextVar(
    "helios_mcp_caller", default=None
)

# The engine names compile_query takes here. "hive" kept the Impala rendering before
# dialect hooks existed (3.6); helios-serve takes any dialect name.
ENGINE_DIALECT = {"impala": "impala", "hive": "impala", "spark": "spark"}


# ---------------------------------------------------------------------------- model store
class ModelStore:
    """helios-serve's published models (``artifact_store``), with the turn's pinned
    revision: a delegated caller may name the revision its conversation started on."""

    def __init__(self):
        self._published: helios_serve.ModelStore | None = None

    def _store(self) -> helios_serve.ModelStore:
        if self._published is None or self._published.artifacts is not artifact_store:
            self._published = helios_serve.ModelStore(artifact_store)
        return self._published

    def sources(self) -> list[dict]:
        return self._store().sources()

    def load(self, name: str | None = None) -> tuple[dict, SemanticModel]:
        published = self._store()
        if name is None:
            sources = published.sources()
            name = sources[0]["name"] if sources else None
        caller = _caller_context.get()
        pinned = (caller.semantic_revision_id
                  if caller and caller.semantic_revision_id and caller.model_id == name else None)
        return published.load(name, revision=pinned)


store = ModelStore()

# The library's pieces, under the names this module and its tests have always used.
search = tools.search
_error = tools.error
_semantic_error_message = tools.semantic_error_message


def _impala_filter_type_error(exc: BaseException) -> str | None:
    """The message for Impala refusing a filter value's type; None for anything else."""
    from helios_cloudera.impala import ImpalaEngine

    return tools.VALUE_TYPE_REJECTED if ImpalaEngine.rejected_value_type(None, exc) else None


def _compile_semantic(model, metrics, dimensions, filters, limit, engine):
    dialect = ENGINE_DIALECT.get(engine.lower())
    if dialect is None:
        raise tools.CompileError(f"unsupported engine {engine!r}; use impala, hive, or spark")
    return tools.compile_semantic(model, metrics, dimensions, filters, limit, dialect_named(dialect))


def _model_connection(settings: Settings, model) -> tuple[str | None, str | None]:
    return helios_serve.model_connection(settings, metadata_repository, model)


def _stores() -> helios_serve.Stores:
    """helios-serve's view of this server: the console's repository as the registry,
    its policy and visibility hooks, Impala through ``impala_config`` and Atlas through
    ``atlas_config``. Auditing stays with ``_audited_tool``, which also sees the console's
    denials."""
    settings = Settings()

    def engines(connection: str | None):
        from helios_cloudera.impala import ImpalaEngine

        cfg = impala_config(settings, connection)
        return ImpalaEngine(cfg) if cfg is not None else None

    def catalogs():
        from helios_cloudera.atlas import AtlasClient

        cfg = atlas_config(settings)
        return AtlasClient(cfg) if cfg is not None else None

    return helios_serve.Stores(settings, metadata_repository, store, visibility=model_visibility,
                               data_policy=data_policy, audit=None, engines=engines, catalogs=catalogs)


def _library(tool):
    """The helios-serve tool without its own audit record (``_audited_tool`` keeps one)."""
    return tool.__wrapped__


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
    require_visible(model_visibility, caller.caller, model_id)
    model = metadata_repository.model(model_id)
    if model is None:
        raise ModelNotVisible(model_id)  # the same error as a model the caller may not see
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


_DENIED_ERRORS = frozenset(
    {
        "authorization_denied",
        "authentication_required",
        "data_authorization_denied",
        "proxy_delegation_denied",
        "query_denied",
    }
)


def _audit_sink() -> AuditSink:
    return audit_sink or RepositoryAuditSink(lambda: metadata_repository, "mcp")


def _audited_tool(name: str):
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            started = time.perf_counter()
            mcp_caller = _caller_context.get()
            base = {
                "action": name,
                "model": kwargs.get("model"),
                "caller": mcp_caller.caller.username if mcp_caller else None,
            }
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
                record_safely(_audit_sink(), AuditRecord(
                    **base,
                    outcome="error",
                    exception=type(exc).__name__,
                    duration_ms=(time.perf_counter() - started) * 1000,
                ))
                raise
            error_code = (
                result.get("error")
                if isinstance(result, dict)
                else None
            )
            diagnostics = (
                result.pop("_audit_diagnostics", None)
                if isinstance(result, dict)
                else None
            )
            rows = result.get("rows") if isinstance(result, dict) else None
            sql = result.get("sql") if isinstance(result, dict) else None
            ran_query = name == "run_query" and isinstance(rows, list)
            record_safely(_audit_sink(), AuditRecord(
                **base,
                outcome=(
                    "denied" if error_code in _DENIED_ERRORS
                    else "error" if error_code
                    else "success"
                ),
                error_code=error_code,
                row_count=len(rows) if ran_query else None,
                sql_fingerprint=(
                    sql_fingerprint(sql) if ran_query and isinstance(sql, str) else None
                ),
                duration_ms=(time.perf_counter() - started) * 1000,
                details=(
                    {"diagnostics": diagnostics} if isinstance(diagnostics, dict) else {}
                ),
            ))
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


@server.tool(description="List the semantic models this server can answer from.")
@_audited_tool("list_models")
def list_models() -> list[dict] | dict:
    caller = _caller_context.get()
    if caller is None:
        return _error("authentication_required", "authenticated MCP caller context is required")
    listed = _library(tools.list_models)(caller.caller, _stores())["models"]
    return [m for m in listed if _authorized_tool(authz.Action.MODEL_READ, m["name"])[2] is None]


def _call(tool, action: authz.Action, model: str | None, **arguments):
    """Check the console's grants for ``model``, then run the helios-serve tool on it."""
    caller, authorized_model, denied = _authorized_tool(action, model)
    if denied:
        return denied
    return _library(tool)(caller.caller, _stores(), model=authorized_model.id, **arguments)


@server.tool(description="Datasets, fields, metrics and relationships of a model. Omit model to use the default.")
@_audited_tool("describe_model")
def describe_model(model: str | None = None) -> dict:
    return _call(tools.describe_model, authz.Action.SEMANTIC_READ, model)


@server.tool(description="Resolve a natural-language question to the datasets, fields, metrics and glossary terms it involves.")
@_audited_tool("search_semantics")
def search_semantics(question: str, limit: int = 10, model: str | None = None) -> dict:
    return _call(tools.search_semantics, authz.Action.SEMANTIC_READ, model, question=question, limit=limit)


@server.tool(description="Definition and context for one thing by name: a dataset, a column (database.table.column), a metric or a glossary term.")
@_audited_tool("describe")
def describe(name: str, model: str | None = None) -> dict:
    return _call(tools.describe, authz.Action.SEMANTIC_READ, model, name=name)


@server.tool(description="Compile a semantic request into SQL. Call search_semantics first and copy metric names exactly from its results. "
                         "Dimensions and filter columns must be exact database.table.column identifiers returned by search_semantics or describe; "
                         "never invent or shorten identifiers. Preserve each returned field datatype in filter JSON: use JSON numbers without quotes "
                         "for numeric fields, booleans for Boolean fields, and ISO strings for Date/Timestamp fields. "
                         "filters: [{column, op, value}] with op in = != < <= > >= IN LIKE BETWEEN; "
                         "engine: impala | hive | spark.")
@_audited_tool("compile_query")
def compile_query(metrics: list[str], dimensions: list[str] | None = None, filters: list[dict] | None = None,
                  limit: int = 100, engine: str = "impala", model: str | None = None) -> dict:
    dialect = ENGINE_DIALECT.get(engine.lower())
    if dialect is None:
        return _error("invalid_semantic_query", _semantic_error_message(
            ValueError(f"unsupported engine {engine!r}; use impala, hive, or spark")))
    return _call(tools.compile_query, authz.Action.QUERY_COMPILE, model, metrics=metrics, dimensions=dimensions,
                 filters=filters, limit=limit, dialect=dialect)


@server.tool(description="Compile a semantic request and run it on the warehouse. Call search_semantics first, copy metric names exactly, and use "
                         "full database.table.column identifiers for dimensions and filters. Semantic validation happens before Impala execution. "
                         "Filter JSON values must preserve the datatype returned by search_semantics; numeric values must not be quoted. "
                         "Same arguments as compile_query. Returns columns and rows.")
@_audited_tool("run_query")
def run_query(metrics: list[str], dimensions: list[str] | None = None, filters: list[dict] | None = None,
              limit: int = 100, model: str | None = None) -> dict:
    return _call(tools.run_query, authz.Action.QUERY_EXECUTE, model, metrics=metrics, dimensions=dimensions,
                 filters=filters, limit=limit)


@server.tool(description="Atlas lineage for a column given as database.table.column: the processes and datasets upstream and downstream.")
@_audited_tool("explain_lineage")
def explain_lineage(column: str, depth: int = 3, model: str | None = None) -> dict:
    return _call(tools.explain_lineage, authz.Action.DATASOURCE_READ, model, column=column, depth=depth)


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
    from helios_cortex._pending import evidence as _evidence

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
    from helios_cortex._pending import evidence

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
    from helios_cloudera.index import impala_index_store
    from helios_cortex._pending import evidence

    index = impala_index_store(Settings())
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