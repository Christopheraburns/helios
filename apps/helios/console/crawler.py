"""Crawler settings API (CR-0e; docs/crawler-analysis.md section 5).

    GET  /api/v1/crawler/settings                      active settings + all versions
    GET  /api/v1/crawler/settings/defaults             the built-in defaults
    GET  /api/v1/crawler/settings/versions/{version}   one version
    POST /api/v1/crawler/settings                      validate and save a new version
    POST /api/v1/crawler/settings/{version}:activate   use it for the next crawls
    GET  /api/v1/crawler/runs                          crawl runs, newest first
    GET  /api/v1/crawler/runs/{crawl_run_id}           one run with its assets
    POST /api/v1/crawler/runs/{crawl_run_id}:evaluate  score the run against a dataset (CR-8)
    GET  /api/v1/crawler/runs/{crawl_run_id}/evaluations   latest score per dataset
    GET  /api/v1/crawler/evaluations?dataset=          latest score per run, for comparing arms
    POST /api/v1/crawler/runs/{crawl_run_id}:project   push the run's index rows to Memgraph (CR-7)
    GET  /api/v1/crawler/runs/{crawl_run_id}/projection  the graph gateway's load state for the run

Settings live in helios_index; editing them never touches code. Saving checks
the document's structure and that every class and claim predicate it names
exists in the active ontology.

Evaluation runs synchronously as the signed-in principal: with Impala proxy
delegation on, the ground-truth queries run as that user (``evaluator_mode``
"proxy"), so Ranger decides who may evaluate; otherwise they run as the API's
WORKLOAD_USER ("workload_user"). The crawler identity never reads the truth.

Projection pushes one run's helios_index rows, table by table in reference
order, to the Helios Graph gateway (``HELIOS_GRAPH_GATEWAY_URL`` and
``HELIOS_GRAPH_TOKEN``); the lakehouse stays canonical and Memgraph is a
disposable copy that can be dropped and re-projected at any time.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.helios.crawler import evaluate as harness
from fastapi import APIRouter, HTTPException, Request
from helios_core.config import impala_config
from helios_core.crawler.settings import DEFAULT_SETTINGS, CrawlerSettings, ontology_problems
from helios_core.engines.impala import ImpalaEngine, ImpalaProxyDelegationError, _same_user
from helios_core.index import crawler_settings as versions
from helios_core.index import ontology_versions, runs
from helios_core.index.records import AssetRecord
from pydantic import BaseModel, ValidationError

from . import ontology
from .graph_client import GraphGatewayClient, GraphGatewayError, GraphGatewayUnavailable

LOGGER = logging.getLogger(__name__)
crawler_router = APIRouter(prefix="/api/v1/crawler", tags=["crawler"])


class SaveRequest(BaseModel):
    settings: dict[str, Any]
    note: str = ""


def _store():
    store = ontology.index_store()
    if store is None:
        raise HTTPException(
            status_code=503, detail="helios_index is not configured (no Impala settings)"
        )
    return store


def _actor(request: Request) -> str:
    return ontology._actor(request)


def _meta(record: Any, active_version: int | None) -> dict[str, Any]:
    return {
        "version": record.version,
        "content_hash": record.content_hash,
        "created_at": record.created_at,
        "created_by": record.created_by,
        "note": record.note,
        "active": record.version == active_version,
    }


def _ontology_vocabulary(store) -> tuple[set[str], set[str]] | None:
    """(classes, claim predicates) of the active ontology version, if one is active."""
    active = ontology_versions.active(store)
    if active is None:
        return None
    for record in ontology_versions.versions(store):
        if (record.version, record.content_hash) == (active.version, active.content_hash):
            graph = ontology_versions.graph_of(record)
            classes = {n.key for n in graph.nodes if n.label == "Class"}
            predicates = {
                n.key.split("#", 1)[1]
                for n in graph.nodes
                if n.label == "EnumValue" and n.key.startswith("RetailClaimPredicate#")
            }
            return classes, predicates
    return None


@crawler_router.get("/settings")
def get_settings() -> dict[str, Any]:
    store = _store()
    record, settings = versions.active(store)
    active_version = record.version if record else None
    return {
        "active_version": active_version,
        "using_defaults": record is None,
        "content_hash": settings.content_hash(),
        "settings": settings.model_dump(mode="json"),
        "versions": [_meta(r, active_version) for r in reversed(versions.versions(store))],
    }


@crawler_router.get("/settings/defaults")
def get_defaults() -> dict[str, Any]:
    return {
        "content_hash": DEFAULT_SETTINGS.content_hash(),
        "settings": DEFAULT_SETTINGS.model_dump(mode="json"),
    }


@crawler_router.get("/settings/versions/{version}")
def get_version(version: int) -> dict[str, Any]:
    store = _store()
    try:
        record = versions.get(store, version)
    except versions.UnknownSettingsVersion:
        raise HTTPException(status_code=404, detail=f"settings version {version} not found")
    active, _ = versions.active(store)
    return {
        **_meta(record, active.version if active else None),
        "settings": versions.settings_of(record).model_dump(mode="json"),
    }


@crawler_router.post("/settings")
def save_settings(body: SaveRequest, request: Request) -> dict[str, Any]:
    store = _store()
    try:
        settings = CrawlerSettings.model_validate(body.settings)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in error['loc']) or '(document)'}: {error['msg']}"
            for error in exc.errors()
        ]
        raise HTTPException(
            status_code=422, detail={"message": "invalid settings", "problems": problems}
        )
    vocabulary = _ontology_vocabulary(store)
    warnings: list[str] = []
    if vocabulary is None:
        warnings.append("no active ontology version: class and predicate names were not checked")
    else:
        problems = ontology_problems(settings, *vocabulary)
        if problems:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "settings name things the active ontology lacks",
                    "problems": problems,
                },
            )
    record, created = versions.save(store, settings, _actor(request), body.note)
    active, _ = versions.active(store)
    return {
        **_meta(record, active.version if active else None),
        "created": created,
        "warnings": warnings,
    }


@crawler_router.post("/settings/{version}:activate")
def activate_settings(version: int, request: Request) -> dict[str, Any]:
    store = _store()
    try:
        activation = versions.activate(store, version, _actor(request))
    except versions.UnknownSettingsVersion:
        raise HTTPException(status_code=404, detail=f"settings version {version} not found")
    return activation.model_dump()


# --- crawl runs ---------------------------------------------------------------------


def _evaluation_view(record: Any, run: Any = None, metrics: bool = True) -> dict[str, Any]:
    view = {
        "evaluation_id": record.evaluation_id,
        "crawl_run_id": record.crawl_run_id,
        "dataset_id": record.dataset_id,
        "evaluated_at": record.evaluated_at,
        "evaluator": record.evaluator,
        "evaluator_mode": record.evaluator_mode,
        "harness_version": record.harness_version,
        "ontology_version": record.ontology_version,
        "strategy": record.strategy,
        "status": record.status,
        "error": record.error,
        "summary": record.summary,
    }
    if metrics:
        view["metrics"] = record.metrics
    if run is not None:
        view["run"] = {
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "status": run.status,
            "source": run.source,
            "crawler_version": run.crawler_version,
            "settings_version": run.settings_version,
            "ontology_version": run.ontology_version,
            "counts": run.counts,
        }
    return view


def _latest_evaluations_by_run(store) -> dict[str, Any]:
    """crawl_run_id -> its newest evaluation (any dataset)."""
    latest: dict[str, Any] = {}
    for record in harness.evaluations(store):  # newest first
        latest.setdefault(record.crawl_run_id, record)
    return latest


def _run_view(run: Any, evaluation: Any = None) -> dict[str, Any]:
    return {
        "latest_evaluation": (
            _evaluation_view(evaluation, metrics=False) if evaluation is not None else None
        ),
        "crawl_run_id": run.crawl_run_id,
        "connector": run.connector,
        "source": run.source,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "actor": run.actor,
        "ontology_version": run.ontology_version,
        "crawler_version": run.crawler_version,
        "settings_version": run.settings_version,
        "settings_hash": run.settings_hash,
        "settings": run.settings,
        "counts": run.counts,
        "error": run.error,
    }


@crawler_router.get("/runs")
def list_runs(source: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Crawl runs (latest state of each), newest first; optionally for one source."""
    store = _store()
    selected = [r for r in runs.runs(store) if source is None or r.source == source]
    latest = _latest_evaluations_by_run(store)
    return [_run_view(r, latest.get(r.crawl_run_id)) for r in selected[: max(1, min(limit, 1000))]]


@crawler_router.get("/runs/{crawl_run_id}")
def get_run(crawl_run_id: str) -> dict[str, Any]:
    """One run, with every asset it recorded and counts by status and class."""
    store = _store()
    run = next((r for r in runs.runs(store) if r.crawl_run_id == crawl_run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail=f"crawl run {crawl_run_id} not found")
    assets = [
        a
        for a in store.read("helios_index.assets", {"crawl_run_id": crawl_run_id})
        if isinstance(a, AssetRecord)
    ]
    by_status: dict[str, int] = {}
    by_class: dict[str, int] = {}
    for asset in assets:
        by_status[asset.status] = by_status.get(asset.status, 0) + 1
        by_class[asset.ontology_class] = by_class.get(asset.ontology_class, 0) + 1
    return {
        **_run_view(run, _latest_evaluations_by_run(store).get(crawl_run_id)),
        "asset_counts": {"by_status": by_status, "by_class": by_class},
        "assets": [
            {
                "asset_id": a.asset_id,
                "asset_version_id": a.asset_version_id,
                "ontology_class": a.ontology_class,
                "mime_type": a.mime_type,
                "status": a.status,
                "status_detail": a.status_detail,
                "size_bytes": a.size_bytes,
                "semantic_timestamp": a.semantic_timestamp,
                "object_key": a.source_locator.get("key"),
            }
            for a in sorted(assets, key=lambda a: (a.status != "fetched", a.asset_id))
        ],
    }


# --- evaluation (CR-8 / CR-E1) --------------------------------------------------------


class EvaluateRequest(BaseModel):
    dataset_id: str


def _truth_connection(principal_id: str) -> tuple[Any, str]:
    """(DB-API connection for helios_ground_truth, evaluator_mode). With proxy
    delegation configured the connection runs as the principal and Impala is
    asked to confirm it (EFFECTIVE_USER); otherwise as the API's workload user."""
    config = impala_config()
    if config is None:
        raise HTTPException(
            status_code=503, detail="helios_index is not configured (no Impala settings)"
        )
    engine = ImpalaEngine(config)
    if not config.proxy_delegation:
        return engine.connect(), "workload_user"
    user = principal_id.split(":", 1)[-1]
    connection = engine._connect(user)
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT EFFECTIVE_USER()")
        effective = cursor.fetchone()
        if not effective or not _same_user(str(effective[0]), user):
            raise ImpalaProxyDelegationError("Impala did not enforce the delegated SSO identity")
    except BaseException:
        connection.close()
        raise
    return connection, "proxy"


@crawler_router.post("/runs/{crawl_run_id}:evaluate")
def evaluate_run(crawl_run_id: str, body: EvaluateRequest, request: Request) -> dict[str, Any]:
    """Score the run against a ground-truth dataset, as the signed-in principal."""
    from apps.helios.console.api import principal_from_request

    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="authenticated principal is required")
    store = _store()
    try:
        connection, mode = _truth_connection(principal.id)
    except ImpalaProxyDelegationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    try:
        record = harness.evaluate(
            store,
            connection.cursor,
            crawl_run_id,
            body.dataset_id,
            evaluator=principal.id,
            evaluator_mode=mode,
        )
    except harness.UnknownRun:
        raise HTTPException(status_code=404, detail=f"crawl run {crawl_run_id} not found")
    except harness.GroundTruthDenied as exc:
        LOGGER.info("evaluation refused for %s: %s", principal.id, exc)
        raise HTTPException(
            status_code=403, detail=f"ground truth is not readable by {principal.id}"
        )
    except harness.EvaluationFailed as exc:
        raise HTTPException(status_code=500, detail=f"evaluation failed: {exc.record.error}")
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001, S110 - closing is best effort
            pass
    run = harness.find_run(store, crawl_run_id)
    return _evaluation_view(record, run)


@crawler_router.get("/runs/{crawl_run_id}/evaluations")
def run_evaluations(crawl_run_id: str) -> list[dict[str, Any]]:
    """The latest evaluation of the run per ground-truth dataset, newest first."""
    store = _store()
    try:
        run = harness.find_run(store, crawl_run_id)
    except harness.UnknownRun:
        raise HTTPException(status_code=404, detail=f"crawl run {crawl_run_id} not found")
    return [_evaluation_view(r, run) for r in harness.evaluations(store, crawl_run_id=crawl_run_id)]


@crawler_router.get("/evaluations")
def list_evaluations(dataset: str | None = None) -> list[dict[str, Any]]:
    """The latest evaluation per run (for one dataset, if given), newest first:
    the view that compares crawler arms."""
    store = _store()
    by_run = {r.crawl_run_id: r for r in runs.runs(store)}
    return [
        _evaluation_view(record, by_run.get(record.crawl_run_id))
        for record in harness.evaluations(store, dataset_id=dataset)
    ]


# --- projection into Memgraph (CR-7) ---------------------------------------------------


# helios_index tables pushed to the gateway, in the order their references require.
PROJECTION_TABLES: tuple[str, ...] = (
    "assets",
    "segments",
    "mentions",
    "entities",
    "entity_links",
    "relationships",
    "claims",
    "claim_evidence",
)
PROJECTION_BATCH = 500


class ProjectRequest(BaseModel):
    force: bool = False


def _gateway() -> GraphGatewayClient:
    """The graph gateway client, or a 503 when the gateway is not configured."""
    try:
        return GraphGatewayClient()
    except GraphGatewayUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))


def _gateway_http_error(exc: GraphGatewayError) -> HTTPException:
    """422s (rejected rows) and 404s pass through; anything else is the gateway's fault."""
    if exc.status in (404, 409, 422):
        return HTTPException(status_code=exc.status, detail=exc.detail)
    return HTTPException(status_code=502, detail=f"graph gateway error: {exc.detail}")


def _batches(rows: list[dict[str, Any]], size: int | None = None):
    size = size or PROJECTION_BATCH
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


@crawler_router.post("/runs/{crawl_run_id}:project")
def project_run(crawl_run_id: str, request: Request, body: ProjectRequest | None = None) -> dict[str, Any]:
    """Push the run's index rows to Memgraph via the graph gateway and return the
    graph's counts next to the index row counts, so the two can be compared."""
    from apps.helios.console.api import principal_from_request

    if principal_from_request(request) is None:
        raise HTTPException(status_code=401, detail="authenticated principal is required")
    store = _store()
    run = next((r for r in runs.runs(store) if r.crawl_run_id == crawl_run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail=f"crawl run {crawl_run_id} not found")
    gateway = _gateway()
    force = body.force if body is not None else False
    index_rows: dict[str, int] = {}
    tables: dict[str, dict[str, int]] = {}
    try:
        gateway.begin_run(crawl_run_id, run.ontology_version, force=force)
        for table in PROJECTION_TABLES:
            rows = [
                record.model_dump(mode="json")
                for record in store.read(f"helios_index.{table}", {"crawl_run_id": crawl_run_id})
            ]
            index_rows[table] = len(rows)
            totals: dict[str, int] = {"merged": 0, "skipped": 0}
            for batch in _batches(rows):
                result = gateway.load_rows(crawl_run_id, table, batch)
                for key in ("merged", "skipped", "without_class"):
                    if key in result:
                        totals[key] = totals.get(key, 0) + int(result[key])
            tables[table] = totals
        finished = gateway.finish_run(crawl_run_id)
    except GraphGatewayUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GraphGatewayError as exc:
        raise _gateway_http_error(exc)
    finally:
        gateway.close()
    LOGGER.info("projected crawl run %s into the graph: %s", crawl_run_id, finished.get("counts"))
    return {
        "crawl_run_id": crawl_run_id,
        "ontology_version": run.ontology_version,
        "status": finished.get("status", "complete"),
        "index_rows": index_rows,
        "tables": tables,
        "graph": finished.get("counts"),
    }


@crawler_router.get("/runs/{crawl_run_id}/projection")
def run_projection(crawl_run_id: str) -> dict[str, Any]:
    """The gateway's load state for the run (404 when it has not been projected)."""
    gateway = _gateway()
    try:
        state = gateway.run_state(crawl_run_id)
    except GraphGatewayUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except GraphGatewayError as exc:
        raise _gateway_http_error(exc)
    finally:
        gateway.close()
    if state is None:
        raise HTTPException(status_code=404, detail=f"crawl run {crawl_run_id} is not projected")
    return state
