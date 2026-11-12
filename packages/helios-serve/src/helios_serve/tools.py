"""The structured tools, as plain functions ``tool(caller, stores, **arguments) -> dict``.

    list_models        the published models the caller may see
    describe_model     datasets, fields, metrics and relationships of one model
    search_semantics   a natural-language question to the datasets, fields and metrics it involves
    describe           one dataset, field, metric or glossary term by name
    compile_query      metrics + dimensions + filters to SQL
    run_query          compile and run it on the warehouse, as the caller
    explain_lineage    a column's lineage, from a catalog that has it

Each is marked with ``@tool`` (its effect and the description an agent reads) and
registered under the ``helios.tools`` entry point; ``helios_serve.load_registry()``
lists them with input schemas built from the type hints below.

``caller`` is the host's ``helios_cortex.Caller`` (None: the connection's service
identity); ``stores`` is a ``helios_serve.Stores``. Arguments are keywords. A tool
never raises for something the caller can fix: it returns ``{"error": code,
"message": ..., "retryable": ...}``. Every call is recorded to ``stores.audit`` when the
host sets one, with diagnostics for errors that need an administrator; the result the
caller sees never carries them.

Which models a caller may use is ``stores.visibility``; whether a query may run is
``stores.data_policy``. The warehouse enforces the rest, since queries run as the caller.
"""
from __future__ import annotations

import functools
import logging
import re
import time
import traceback
from typing import Annotated, Any, Required, TypedDict

from helios_cortex.audit_sink import AuditRecord, record_safely, sql_fingerprint
from helios_cortex.caller import (
    Caller,
    EngineAuthenticationError,
    IdentityMode,
    ImpersonationError,
)
from helios_cortex.catalog import LINEAGE, capabilities
from helios_cortex.compiler import CompileError, Compiler, Filter, FilterValueTypeError, SemanticRequest
from helios_cortex.data_authorization import DataAction, DataResource
from helios_cortex.engines import SqlDialect, dialect_named
from helios_cortex.ossie import SemanticModel
from helios_cortex.visibility import ModelNotVisible, require_visible

from helios_serve.registry import Effect, tool
from helios_serve.stores import ModelNotAvailable, Stores, model_connection

LOGGER = logging.getLogger(__name__)

MAX_ROWS = 1000
DIAGNOSTICS = "_audit_diagnostics"      # carried from a tool to its audit record, then removed

# Error codes that mean "not allowed" rather than "failed", for the audit outcome.
DENIED = frozenset({
    "authorization_denied",
    "authentication_required",
    "data_authorization_denied",
    "proxy_delegation_denied",
    "query_denied",
})


# ---------------------------------------------------------------------------- arguments
# The argument types, with the descriptions an agent reads in each tool's input schema.
class QueryFilter(TypedDict, total=False):
    column: Annotated[str, "A database.table.column identifier returned by search_semantics or describe."]
    field: Annotated[str, "Another name for column."]
    op: Required[Annotated[str, "One of =, !=, <>, <, <=, >, >=, IN, NOT IN, BETWEEN, LIKE, IS NULL, IS NOT NULL."]]
    value: Annotated[Any, "The value, typed as the field's datatype: a JSON number (unquoted) for a numeric field, "
                          "a boolean for a Boolean one, an ISO string for a date or timestamp; a list for IN and "
                          "NOT IN, two values for BETWEEN, nothing for IS NULL."]


ModelName = Annotated[str | None, "The published model to use, from list_models; omit it for the first one."]
Metrics = Annotated[list[str], "Metric names, copied exactly from search_semantics."]
Dimensions = Annotated[list[str] | None, "database.table.column identifiers to group by, from search_semantics or "
                                         "describe; never invented or shortened."]
Filters = Annotated[list[QueryFilter] | None, "Conditions on database.table.column identifiers."]
RowLimit = Annotated[int, f"At most this many rows (1 to {MAX_ROWS})."]

QUERY_GUIDANCE = (
    "Call search_semantics first and copy metric names exactly from its results. Dimensions and filter columns "
    "must be exact database.table.column identifiers returned by search_semantics or describe; never invent or "
    "shorten them. Keep each field's datatype in filter values: unquoted JSON numbers for numeric fields, "
    "booleans for Boolean fields, ISO strings for dates and timestamps.")


# ---------------------------------------------------------------------------- errors
def error(code: str, message: str, *, retryable: bool = False) -> dict[str, Any]:
    return {"error": code, "message": message, "retryable": retryable}


def exception_diagnostics(exc: BaseException, *, stage: str, excluded_values: tuple[str, ...] = ()) -> dict[str, Any]:
    """Administrator-only detail for an audit record: the exception chain and stack, with
    secret-looking environment values and ``excluded_values`` (the SQL) redacted."""
    from helios_cortex.settings import environment_secret_values

    secrets = environment_secret_values() | {v for v in excluded_values if v}

    def scrub(value: str) -> str:
        for secret in sorted(secrets, key=len, reverse=True):
            value = value.replace(secret, "[redacted]")
        return value

    chain: list[dict[str, str]] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(chain) < 5:
        seen.add(id(current))
        chain.append({"exception_type": type(current).__name__, "description": scrub(str(current) or repr(current))})
        current = current.__cause__ or current.__context__
    frames = [{"file": scrub(f.filename), "line": f.lineno, "function": f.name}
              for f in traceback.extract_tb(exc.__traceback__)[-20:]]
    return {"stage": stage, "exception_chain": chain, "stack": frames}


def _diagnosed(code: str, message: str, exc: BaseException, *, stage: str, retryable: bool = False,
               excluded_values: tuple[str, ...] = ()) -> dict[str, Any]:
    result = error(code, message, retryable=retryable)
    result[DIAGNOSTICS] = exception_diagnostics(exc, stage=stage, excluded_values=excluded_values)
    return result


def semantic_error_message(exc: BaseException) -> str:
    detail = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
    return (f"{detail}. Use an exact metric name from search_semantics and use "
            "database.table.column identifiers for dimensions and filters.")


VALUE_TYPE_REJECTED = (
    "The warehouse rejected a generated filter because its value type did not match the "
    "semantic field type. Search for the field again, preserve the datatype returned by "
    "search_semantics, and use JSON numbers without quotes for numeric fields.")


# ---------------------------------------------------------------------------- audit
def _username(caller: Caller | None) -> str | None:
    return caller.username if caller is not None else None


def _hooks_caller(caller: Caller | None) -> Caller:
    """The caller the host's hooks see: an empty Caller (the service) for None."""
    return caller if caller is not None else Caller()


def audited(name: str):
    """Record each call of the tool ``name`` to ``stores.audit``: success, denied (an
    error code in DENIED) or error, with rows and a SQL fingerprint for a query that ran."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(caller: Caller | None, stores: Stores, **arguments: Any) -> Any:
            started = time.perf_counter()
            base = {"action": name, "model": arguments.get("model"), "caller": _username(caller)}
            try:
                result = function(caller, stores, **arguments)
            except Exception as exc:
                LOGGER.exception("tool %s failed: %s", name, type(exc).__name__)
                if stores.audit is not None:
                    record_safely(stores.audit, AuditRecord(**base, outcome="error", exception=type(exc).__name__,
                                                            duration_ms=(time.perf_counter() - started) * 1000))
                raise
            diagnostics = result.pop(DIAGNOSTICS, None) if isinstance(result, dict) else None
            if stores.audit is not None:
                code = result.get("error") if isinstance(result, dict) else None
                rows = result.get("rows") if isinstance(result, dict) else None
                sql = result.get("sql") if isinstance(result, dict) else None
                ran = name == "run_query" and isinstance(rows, list)
                record_safely(stores.audit, AuditRecord(
                    **base,
                    outcome="denied" if code in DENIED else "error" if code else "success",
                    error_code=code,
                    row_count=len(rows) if ran else None,
                    sql_fingerprint=sql_fingerprint(sql) if ran and isinstance(sql, str) else None,
                    duration_ms=(time.perf_counter() - started) * 1000,
                    details={"diagnostics": diagnostics} if isinstance(diagnostics, dict) else {},
                ))
            return result

        return wrapped

    return decorate


# ---------------------------------------------------------------------------- models
def _resolve(caller: Caller | None, stores: Stores, model: str | None) -> tuple[dict | None, SemanticModel | None, dict | None]:
    """The source and semantic model ``model`` names (the first published one the caller
    may see when None), or the error to return."""
    try:
        if model is None:
            visible = [s for s in stores.models.sources() if stores.visibility.can_see(_hooks_caller(caller), s["name"])]
            if not visible:
                return None, None, error("model_not_found", "no published model is available")
            model = visible[0]["name"]
        require_visible(stores.visibility, _hooks_caller(caller), model)
        source, semantic_model = stores.models.load(model)
    except (ModelNotVisible, ModelNotAvailable, LookupError) as exc:
        return None, None, error("model_not_found", str(exc.args[0]) if exc.args else str(exc))
    return source, semantic_model, None


def search(model: SemanticModel, question: str, limit: int = 10) -> dict[str, Any]:
    """The datasets, fields and metrics whose names, descriptions and synonyms share words
    with ``question``, best first. Keys are left out: nobody asks about them by name."""
    def tokens(s: str) -> set[str]:
        return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) > 2}

    q = tokens(question)
    hits: list[tuple[int, dict]] = []
    for dataset in model.datasets.values():
        terms = tokens(dataset.name) | tokens(dataset.source) | tokens(dataset.description)
        for synonym in dataset.synonyms:
            terms |= tokens(synonym)
        if score := len(q & terms):
            hits.append((score * 2, {"kind": "dataset", "name": dataset.name, "table": dataset.source,
                                     "type": dataset.kind, "description": dataset.description}))
        for f in dataset.fields.values():
            if f.role in ("identifier", "foreign_key"):
                continue
            terms = tokens(f.name) | tokens(f.label) | tokens(f.description)
            for synonym in f.synonyms:
                terms |= tokens(synonym)
            if score := len(q & terms):
                hits.append((score, {"kind": "field", "role": f.role, "name": f.label, "datatype": f.datatype,
                                     "column": f"{dataset.source}.{f.name}", "description": f.description}))
    for metric in model.metrics.values():
        terms = tokens(metric.name) | tokens(metric.description)
        for synonym in metric.synonyms:
            terms |= tokens(synonym)
        if score := len(q & terms):
            hits.append((score * 2, {"kind": "metric", "name": metric.name, "dataset": metric.dataset,
                                     "datatype": metric.datatype, "expression": metric.expression,
                                     "description": metric.description}))
    hits.sort(key=lambda h: -h[0])
    return {"matches": [h for _, h in hits[:limit]]}


def _semantic_request(metrics: list[str], dimensions: list[str] | None, filters: list[dict] | None,
                      limit: int) -> SemanticRequest:
    semantic_filters: list[Filter] = []
    for item in filters or []:
        if not isinstance(item, dict):
            raise CompileError("each filter must be an object")
        column = item.get("field") or item.get("column")
        operator = item.get("op")
        if not isinstance(column, str) or not column:
            raise CompileError("each filter requires column or field")
        if not isinstance(operator, str) or not operator:
            raise CompileError("each filter requires op")
        semantic_filters.append(Filter(column, operator, item.get("value")))
    return SemanticRequest(metrics=list(metrics), dimensions=list(dimensions or []), filters=semantic_filters,
                           limit=min(max(limit, 1), MAX_ROWS))


def compile_semantic(model: SemanticModel, metrics: list[str], dimensions: list[str] | None,
                     filters: list[dict] | None, limit: int, dialect: SqlDialect) -> tuple[dict[str, Any], set[str]]:
    """The compiled query and every table it reads."""
    compiled = Compiler(model).compile(_semantic_request(metrics, dimensions, filters, limit), dialect=dialect)
    datasets = {compiled.fact}
    for name in compiled.joins:
        relationship = model.relationships[name]
        datasets.update({relationship.from_dataset, relationship.to_dataset})
    assets = {model.datasets[d].source for d in datasets}
    fact = model.datasets[compiled.fact].source
    return ({"sql": compiled.sql, "dataset": fact, "joins": sorted(assets - {fact}), "columns": compiled.columns,
             "dialect": compiled.dialect}, assets)


def _json(value: Any) -> Any:
    return value if isinstance(value, (int, float, str, bool)) or value is None else str(value)


# ---------------------------------------------------------------------------- the tools
@tool(effect=Effect.READ, description="List the semantic models this server can answer from.")
@audited("list_models")
def list_models(caller: Caller | None, stores: Stores) -> dict[str, Any]:
    """The published models the caller may see: ``{"models": [{"name", "status"}]}``."""
    return {"models": [{"name": s["name"], "status": s["status"]} for s in stores.models.sources()
                       if stores.visibility.can_see(_hooks_caller(caller), s["name"])]}


@tool(effect=Effect.READ,
      description="Datasets, fields, metrics and relationships of a model. Omit model to use the default.")
@audited("describe_model")
def describe_model(caller: Caller | None, stores: Stores, *, model: ModelName = None) -> dict[str, Any]:
    """Datasets, fields, metrics and relationships of a model (the first one when None)."""
    source, semantic_model, failed = _resolve(caller, stores, model)
    if failed:
        return failed
    description = semantic_model.describe()
    for item in description["datasets"]:
        dataset = semantic_model.datasets[item["name"]]
        item["table"] = dataset.source
        for f in item["fields"]:
            f["column"] = f["name"]
    return {"model": source["name"], "status": source["status"], **description}


@tool(effect=Effect.READ,
      description="Resolve a natural-language question to the datasets, fields and metrics it involves, best first.")
@audited("search_semantics")
def search_semantics(caller: Caller | None, stores: Stores, *,
                     question: Annotated[str, "The question, in the user's words."],
                     limit: Annotated[int, "At most this many matches (1 to 100)."] = 10,
                     model: ModelName = None) -> dict[str, Any]:
    """The datasets, fields and metrics a natural-language question involves."""
    _, semantic_model, failed = _resolve(caller, stores, model)
    if failed:
        return failed
    return search(semantic_model, question, min(max(limit, 1), 100))


@tool(effect=Effect.READ,
      description="Definition and context for one thing by name: a dataset, a column (database.table.column) or "
                  "a metric.")
@audited("describe")
def describe(caller: Caller | None, stores: Stores, *,
             name: Annotated[str, "A dataset name or table, a database.table.column, or a metric name."],
             model: ModelName = None) -> dict[str, Any]:
    """Definition and context for one dataset, column (database.table.column) or metric."""
    _, semantic_model, failed = _resolve(caller, stores, model)
    if failed:
        return failed
    wanted = name.lower()
    for dataset in semantic_model.datasets.values():
        if wanted in {dataset.name.lower(), dataset.source.lower(), *(s.lower() for s in dataset.synonyms)}:
            return {"kind": "dataset", "table": dataset.source, "name": dataset.name, "type": dataset.kind,
                    "description": dataset.description, "primary_key": dataset.primary_key,
                    "fields": [{"column": f.name, "name": f.label, "role": f.role, "datatype": f.datatype,
                                "description": f.description} for f in dataset.fields.values()]}
    try:
        metric = semantic_model.metric(name)
        return {"kind": "metric", "name": metric.name, "dataset": metric.dataset, "expression": metric.expression,
                "description": metric.description, "datatype": metric.datatype}
    except KeyError:
        pass
    try:
        f = semantic_model.field(name)
        dataset = semantic_model.datasets[f.dataset]
        return {"kind": "field", "dataset": dataset.source, "column": f.name, "name": f.label, "role": f.role,
                "datatype": f.datatype, "description": f.description}
    except KeyError:
        pass
    return error("semantic_not_found", f"nothing named {name!r} is available in the authorized model")


def _model_dialect(stores: Stores, model_id: str) -> SqlDialect | None:
    registered = stores.registry.model(model_id)
    if registered is None:
        return None
    connection, conflict = model_connection(stores.settings, stores.registry, registered)
    if conflict:
        return None
    engine = stores.engines(connection)
    return engine.dialect if engine is not None else None


@tool(effect=Effect.READ,
      description="Compile a semantic request into SQL without running it. " + QUERY_GUIDANCE)
@audited("compile_query")
def compile_query(caller: Caller | None, stores: Stores, *, metrics: Metrics, dimensions: Dimensions = None,
                  filters: Filters = None, limit: RowLimit = 100,
                  dialect: Annotated[str | None, "The SQL dialect (duckdb, postgres, trino, impala, ...); omit it "
                                                 "for the dialect of the model's own warehouse."] = None,
                  model: ModelName = None) -> dict[str, Any]:
    """Compile a semantic request into SQL, in ``dialect`` (a SQLGlot dialect or an engine
    plugin's name) or, by default, the dialect of the model's own connection."""
    source, semantic_model, failed = _resolve(caller, stores, model)
    if failed:
        return failed
    try:
        target = dialect_named(dialect) if dialect else _model_dialect(stores, source["name"])
    except ValueError as exc:
        return error("invalid_semantic_query", str(exc))
    if target is None:
        return error("dialect_required", f"model {source['name']!r} has no connection to take a SQL dialect from; "
                                         "name one (duckdb, postgres, trino, impala, ...)")
    try:
        compiled, _ = compile_semantic(semantic_model, metrics, dimensions, filters, limit, target)
    except FilterValueTypeError as exc:
        return error("invalid_filter_value_type", semantic_error_message(exc))
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        return error("invalid_semantic_query", semantic_error_message(exc))
    return compiled


@tool(effect=Effect.QUERY,
      description="Compile a semantic request and run it on the warehouse as the caller; returns the SQL, columns "
                  "and rows. The request is validated before the warehouse sees it. " + QUERY_GUIDANCE)
@audited("run_query")
def run_query(caller: Caller | None, stores: Stores, *, metrics: Metrics, dimensions: Dimensions = None,
              filters: Filters = None, limit: RowLimit = 100, model: ModelName = None) -> dict[str, Any]:
    """Compile a semantic request and run it on the model's connection as ``caller``:
    columns and rows, each value JSON-ready (numbers, strings, booleans and None; other
    values, such as decimals and dates, as strings). Data authorization
    (``stores.data_policy``) is checked for every table first; a refused credential or
    impersonation is diagnosed, not just reported."""
    result = _run_query(caller, stores, metrics=metrics, dimensions=dimensions, filters=filters, limit=limit,
                        model=model)
    if "rows" in result:
        result["rows"] = [[_json(v) for v in row] for row in result["rows"]]
    return result


@audited("run_query")
def query_values(caller: Caller | None, stores: Stores, *, metrics: list[str], dimensions: list[str] | None = None,
                 filters: list[dict] | None = None, limit: int = 100, model: str | None = None) -> dict[str, Any]:
    """``run_query`` for a Python program: the same checks, errors and audit record (as
    ``run_query``), with each value as the engine returned it (``Decimal``, ``date``, ...).
    Not a registered tool: an agent gets ``run_query``'s JSON."""
    return _run_query(caller, stores, metrics=metrics, dimensions=dimensions, filters=filters, limit=limit,
                      model=model)


def _run_query(caller: Caller | None, stores: Stores, *, metrics: list[str], dimensions: list[str] | None,
               filters: list[dict] | None, limit: int, model: str | None) -> dict[str, Any]:
    source, semantic_model, failed = _resolve(caller, stores, model)
    if failed:
        return failed
    registered = stores.registry.model(source["name"])
    if registered is None:
        return error("model_not_found", f"model {source['name']!r} is not registered")
    connection, conflict = model_connection(stores.settings, stores.registry, registered)
    if conflict:
        return error("connection_conflict", conflict)
    engine = stores.engines(connection)
    if engine is None:
        return error("connection_unavailable", "no warehouse connection is configured for the model's data sources",
                     retryable=True)
    safe_limit = min(max(limit, 1), MAX_ROWS)
    compiled_sql = ""
    resources: dict[tuple[str, str], DataResource] = {}
    try:
        compiled, assets = compile_semantic(semantic_model, metrics, dimensions, filters, safe_limit, engine.dialect)
        compiled_sql = compiled["sql"]
        for asset in assets:
            references = [r for r in registered.data_sources if not r.selected_assets or asset in r.selected_assets]
            if len(references) != 1:
                return error("data_authorization_denied", f"dataset {asset!r} is not uniquely authorized by the model")
            resource = DataResource(references[0].data_source_id, asset, engine.identity_mode)
            resources[(resource.data_source_id, asset)] = resource
        for resource in resources.values():
            access = stores.data_policy.can_access(_hooks_caller(caller), resource, DataAction.QUERY_EXECUTE)
            if not access.allowed:
                return error("data_authorization_denied", access.reason)
        result = engine.query(compiled["sql"], caller, limit=safe_limit)
    except EngineAuthenticationError as exc:
        LOGGER.warning("the warehouse refused a query's credentials")
        return _authentication_diagnosis(engine, exc)
    except ImpersonationError as exc:
        return _diagnosed("proxy_delegation_denied", str(exc), exc, stage="effective_user")
    except FilterValueTypeError as exc:
        return error("invalid_filter_value_type", semantic_error_message(exc))
    except (CompileError, KeyError, TypeError, ValueError) as exc:
        return error("invalid_semantic_query", semantic_error_message(exc))
    except PermissionError as exc:
        return error("query_denied", str(exc))
    except Exception as exc:
        if engine.rejected_value_type(exc):
            LOGGER.warning("the warehouse rejected generated filter types")
            return _diagnosed("invalid_filter_value_type", VALUE_TYPE_REJECTED, exc, stage="query_analysis",
                              excluded_values=(compiled_sql,))
        LOGGER.exception("query failed: %s", type(exc).__name__)
        return _diagnosed("query_unavailable", "The query could not be completed", exc, stage="query",
                          retryable=True, excluded_values=(compiled_sql,))
    return {"sql": compiled["sql"], "dataset": compiled["dataset"], "joins": compiled["joins"],
            "data_source_ids": sorted({r.data_source_id for r in resources.values()}),
            "columns": list(result.columns), "rows": [list(row) for row in result.rows]}


def _authentication_diagnosis(engine, exc: BaseException) -> dict[str, Any]:
    """Which credential the warehouse refused: the caller's token, the connection's own
    credentials, or (when those still work) the impersonation of the caller."""
    if engine.identity_mode is IdentityMode.TOKEN:
        # The caller's own token was refused; the service account is not involved.
        return _diagnosed("caller_token_rejected", "The warehouse rejected the caller's token", exc,
                          stage="token_authentication")
    try:
        service_authenticated = engine.ping()
    except EngineAuthenticationError as service_exc:
        return _diagnosed("workload_authentication_failed",
                          "The warehouse rejected the connection's own (service) credentials", service_exc,
                          stage="service_authentication")
    except Exception as diagnostic_exc:
        LOGGER.exception("the service-credential check failed")
        return _diagnosed("warehouse_authentication_failed",
                          "The warehouse rejected the query and the connection's own credentials could not be "
                          "confirmed", diagnostic_exc, stage="authentication_diagnostic", retryable=True)
    if service_authenticated:
        return _diagnosed("proxy_delegation_denied",
                          "The service connected to the warehouse, but the warehouse refused to run the query "
                          "as the caller", exc, stage="impersonation")
    return _diagnosed("warehouse_authentication_failed", "Warehouse authentication could not be confirmed", exc,
                      stage="authentication", retryable=True)


@tool(effect=Effect.READ, requires={LINEAGE},
      description="Lineage for a column given as database.table.column: the processes and datasets upstream and "
                  "downstream of it, from the data catalog.")
@audited("explain_lineage")
def explain_lineage(caller: Caller | None, stores: Stores, *,
                    column: Annotated[str, "database.table.column"],
                    depth: Annotated[int, "How many hops to follow each way."] = 3,
                    model: ModelName = None) -> dict[str, Any]:
    """A column's (database.table.column) lineage, upstream and downstream, from the
    configured catalog, when it has lineage. ``model`` is checked against the host's
    visibility; the model need not be published, since lineage is the catalog's."""
    if model is not None:
        try:
            require_visible(stores.visibility, _hooks_caller(caller), model)
        except ModelNotVisible as exc:
            return error("model_not_found", str(exc.args[0]) if exc.args else str(exc))
    catalog = stores.catalogs()
    if LINEAGE not in capabilities(catalog):
        return error("lineage_unavailable", "no catalog with lineage is configured", retryable=True)
    parts = column.split(".")
    if len(parts) != 3:
        return error("invalid_semantic_reference", "column must be database.table.column")
    lineage = catalog.column_lineage(*parts, depth)
    if lineage is None:
        return error("semantic_not_found", f"{column} was not found in the catalog")
    return lineage.as_dict()
