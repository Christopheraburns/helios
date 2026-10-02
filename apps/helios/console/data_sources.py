"""Data sources API (DS-2): where documents live, for the crawler.

    GET    /api/v1/data-source-types                                connector types + scope schemas
    GET    /api/v1/organizations/{org_id}/data-sources              list (datasource.read)
    GET    /api/v1/organizations/{org_id}/data-sources/{id}         one, with recent crawls
    POST   /api/v1/organizations/{org_id}/data-sources              create (datasource.manage)
    PUT    /api/v1/organizations/{org_id}/data-sources/{id}         update (datasource.manage)
    DELETE /api/v1/organizations/{org_id}/data-sources/{id}         delete (datasource.manage)
    POST   /api/v1/organizations/{org_id}/data-sources/{id}:test    test the connection

Data sources live in Helios's metadata store (the ``data_sources`` table, shared
with semantic models). A source stores a *reference* to credentials (a Workbench
data connection name, or ``impala``), never credentials. Testing a connection
runs in the API, with the API's own Impala user and the signed-in user's data
connection; the crawler later reads as ``srv_helios_crawler``, so its Ranger and
RAZ rights must cover the same location.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from helios_core import authz
from helios_core.crawler.sources import CrawlConfig, catalog, is_crawlable, validate_scope
from helios_core.domain import DataSource
from helios_core.index import runs
from pydantic import BaseModel, Field, ValidationError

from . import ontology

LOGGER = logging.getLogger(__name__)
data_sources_router = APIRouter(prefix="/api/v1", tags=["data sources"])


class DataSourceBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    connector: str
    connection_ref: str = Field(min_length=1, max_length=500)
    description: str = Field("", max_length=4000)
    scope: dict[str, Any] = Field(default_factory=dict)
    crawl: dict[str, Any] = Field(default_factory=dict)


def _principal(request: Request) -> authz.Principal | None:
    from apps.helios.console.api import principal_from_request

    return principal_from_request(request)


def _repository(request: Request) -> Any:
    return request.app.state.metadata_repository


def _authorize(request: Request, org_id: str, action: authz.Action) -> authz.Principal:
    principal = _principal(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="no authenticated principal")
    repository = _repository(request)
    if repository.organization(org_id) is None:
        raise HTTPException(status_code=404, detail="organization not found")
    policy = getattr(request.app.state, "authorization_policy", None) or authz.Policy(
        repository.grants_for_principal(principal.id)
    )
    decision = policy.can(
        principal, action, authz.Resource("organization", org_id, organization_id=org_id)
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=decision.reason)
    return principal


def _source(request: Request, org_id: str, source_id: str) -> DataSource:
    source = _repository(request).data_source(source_id)
    if source is None or source.organization_id != org_id:
        raise HTTPException(status_code=404, detail="data source not found")
    return source


def _validated(body: DataSourceBody) -> tuple[dict[str, Any], dict[str, Any]]:
    problems: list[str] = []
    scope: dict[str, Any] = {}
    crawl: dict[str, Any] = {}
    if is_crawlable(body.connector):
        try:
            scope = validate_scope(body.connector, body.scope)
        except ValidationError as exc:
            problems += [
                f"scope.{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            ]
        except ValueError as exc:
            problems.append(str(exc))
        try:
            crawl = CrawlConfig.model_validate(body.crawl).model_dump(mode="json")
        except ValidationError as exc:
            problems += [
                f"crawl.{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
            ]
        if body.connector == "table_rows" and body.connection_ref != "impala":
            problems.append("connection_ref: table_rows sources read through 'impala'")
    elif body.scope or body.crawl:
        problems.append(
            f"connector {body.connector!r} is not crawlable; scope and crawl must be empty"
        )
    if problems:
        raise HTTPException(
            status_code=422, detail={"message": "invalid data source", "problems": problems}
        )
    return scope, crawl


def _recent_runs(source_id: str, limit: int = 5) -> list[dict[str, Any]]:
    index = ontology.index_store()
    if index is None:
        return []
    return [
        {
            "crawl_run_id": r.crawl_run_id,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "counts": r.counts,
        }
        for r in runs.runs(index)
        if r.source == source_id
    ][:limit]


def _view(source: DataSource, recent: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    view = {
        "id": source.id,
        "organization_id": source.organization_id,
        "name": source.name,
        "connector": source.connector,
        "connection_ref": source.connection_ref,
        "description": source.description,
        "scope": source.scope,
        "crawl": source.crawl,
        "crawlable": is_crawlable(source.connector),
        "updated_at": source.updated_at,
        "updated_by": source.updated_by,
    }
    if recent is not None:
        view["recent_crawls"] = recent
    return view


@data_sources_router.get("/data-source-types")
def list_types() -> list[dict[str, Any]]:
    return catalog()


@data_sources_router.get("/organizations/{org_id}/data-sources")
def list_sources(org_id: str, request: Request) -> list[dict[str, Any]]:
    _authorize(request, org_id, authz.Action.DATASOURCE_READ)
    sources = _repository(request).data_sources_for_organization(org_id)
    last: dict[str, dict[str, Any]] = {}
    index = ontology.index_store()
    if index is not None:
        for run in runs.runs(index):  # newest first
            last.setdefault(
                run.source,
                {"status": run.status, "started_at": run.started_at, "counts": run.counts},
            )
    return [{**_view(s), "last_crawl": last.get(s.id)} for s in sources]


@data_sources_router.get("/organizations/{org_id}/data-sources/{source_id}")
def get_source(org_id: str, source_id: str, request: Request) -> dict[str, Any]:
    _authorize(request, org_id, authz.Action.DATASOURCE_READ)
    return _view(_source(request, org_id, source_id), _recent_runs(source_id))


def _save(
    request: Request, org_id: str, source_id: str, body: DataSourceBody, principal: authz.Principal
) -> DataSource:
    scope, crawl = _validated(body)
    source = DataSource(
        id=source_id,
        organization_id=org_id,
        name=body.name.strip(),
        connector=body.connector,
        connection_ref=body.connection_ref.strip(),
        description=body.description,
        scope=scope,
        crawl=crawl,
        updated_at=datetime.now(UTC).isoformat(),
        updated_by=principal.id,
    )
    try:
        return _repository(request).save_data_source(source)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@data_sources_router.post("/organizations/{org_id}/data-sources", status_code=201)
def create_source(org_id: str, body: DataSourceBody, request: Request) -> dict[str, Any]:
    principal = _authorize(request, org_id, authz.Action.DATASOURCE_MANAGE)
    return _view(_save(request, org_id, f"ds_{uuid.uuid4().hex[:12]}", body, principal))


@data_sources_router.put("/organizations/{org_id}/data-sources/{source_id}")
def update_source(
    org_id: str, source_id: str, body: DataSourceBody, request: Request
) -> dict[str, Any]:
    principal = _authorize(request, org_id, authz.Action.DATASOURCE_MANAGE)
    _source(request, org_id, source_id)
    return _view(_save(request, org_id, source_id, body, principal))


@data_sources_router.delete("/organizations/{org_id}/data-sources/{source_id}")
def delete_source(org_id: str, source_id: str, request: Request) -> dict[str, Any]:
    _authorize(request, org_id, authz.Action.DATASOURCE_MANAGE)
    _source(request, org_id, source_id)
    try:
        _repository(request).delete_data_source(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"deleted": source_id}


def _connector_for(source: DataSource) -> Any:
    """The source's connector, with the API's credentials (see the module notes)."""
    from apps.helios.crawler.connector import s3_client_from_connection
    from apps.helios.crawler.connectors import build
    from helios_core.config import impala_config
    from helios_core.engines.impala import ImpalaEngine

    config = impala_config()

    def cursor() -> Any:
        if config is None:
            raise RuntimeError("Impala is not configured for the Helios API")
        return ImpalaEngine(config).connect().cursor()

    return build(
        source.id,
        source.connector,
        source.connection_ref,
        dict(source.scope),
        cursor=cursor,
        s3_client_for=s3_client_from_connection,
    )


@data_sources_router.post("/organizations/{org_id}/data-sources/{source_id}:test")
def test_source(org_id: str, source_id: str, request: Request) -> dict[str, Any]:
    _authorize(request, org_id, authz.Action.DATASOURCE_MANAGE)
    source = _source(request, org_id, source_id)
    if not is_crawlable(source.connector):
        raise HTTPException(
            status_code=400, detail=f"{source.connector!r} isn't a crawlable connector"
        )
    try:
        result = _connector_for(source).test()
    except Exception as exc:  # noqa: BLE001 - shown to the user as the test result
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"[:500], "sample": []}
    return {
        "ok": result.ok,
        "detail": result.detail,
        "sample": [
            {
                "asset_id": a.asset_id,
                "mime_type": a.mime_type,
                "size_bytes": a.size_bytes,
                "semantic_timestamp": a.semantic_timestamp,
            }
            for a in result.sample
        ],
        "tested_as": "the Helios API's Impala user and your data connection",
    }
