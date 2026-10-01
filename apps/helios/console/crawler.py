"""Crawler settings API (CR-0e; docs/crawler-analysis.md section 5).

    GET  /api/v1/crawler/settings                      active settings + all versions
    GET  /api/v1/crawler/settings/defaults             the built-in defaults
    GET  /api/v1/crawler/settings/versions/{version}   one version
    POST /api/v1/crawler/settings                      validate and save a new version
    POST /api/v1/crawler/settings/{version}:activate   use it for the next crawls

Settings live in helios_index; editing them never touches code. Saving checks
the document's structure and that every class and claim predicate it names
exists in the active ontology.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from helios_core.crawler.settings import DEFAULT_SETTINGS, CrawlerSettings, ontology_problems
from helios_core.index import crawler_settings as versions
from helios_core.index import ontology_versions
from pydantic import BaseModel, ValidationError

from . import ontology

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
