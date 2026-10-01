"""Crawl runs: start, finish, fail, and read a run's latest state (append-only)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from .records import CrawlRunRecord
from .store import IndexStore
from .tables import RUN_TABLES

CRAWL_RUNS = "helios_index.crawl_runs"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def new_run_id() -> str:
    return f"crawl_{uuid.uuid4().hex}"


def start(
    store: IndexStore,
    *,
    connector: str,
    source: str,
    ontology_version: str,
    crawler_version: str,
    actor: str,
    settings: dict[str, Any] | None = None,
    settings_version: int | None = None,
    settings_hash: str | None = None,
    clock: Callable[[], str] = _now,
) -> CrawlRunRecord:
    now = clock()
    record = CrawlRunRecord(
        crawl_run_id=new_run_id(),
        connector=connector,
        source=source,
        ontology_version=ontology_version,
        crawler_version=crawler_version,
        status="RUNNING",
        started_at=now,
        recorded_at=now,
        actor=actor,
        settings=settings or {},
        settings_version=settings_version,
        settings_hash=settings_hash,
    )
    store.append(CRAWL_RUNS, [record])
    return record


def finish(
    store: IndexStore,
    run: CrawlRunRecord,
    counts: dict[str, int],
    clock: Callable[[], str] = _now,
) -> CrawlRunRecord:
    now = clock()
    record = run.model_copy(
        update={"status": "SUCCEEDED", "recorded_at": now, "finished_at": now, "counts": counts}
    )
    store.append(CRAWL_RUNS, [record])
    return record


def fail(
    store: IndexStore,
    run: CrawlRunRecord,
    error: str,
    clock: Callable[[], str] = _now,
) -> CrawlRunRecord:
    """Record the failure and remove the run's partial rows."""
    store.delete_run(run.crawl_run_id, [t for t in RUN_TABLES if t != CRAWL_RUNS])
    now = clock()
    record = run.model_copy(
        update={"status": "FAILED", "recorded_at": now, "finished_at": now, "error": error[:4000]}
    )
    store.append(CRAWL_RUNS, [record])
    return record


def runs(store: IndexStore) -> list[CrawlRunRecord]:
    """Each run's latest state, newest first."""
    latest: dict[str, CrawlRunRecord] = {}
    for record in store.read(CRAWL_RUNS):
        assert isinstance(record, CrawlRunRecord)
        current = latest.get(record.crawl_run_id)
        if current is None or record.recorded_at >= current.recorded_at:
            latest[record.crawl_run_id] = record
    return sorted(latest.values(), key=lambda r: r.started_at, reverse=True)


def latest_succeeded(store: IndexStore, source: str) -> CrawlRunRecord | None:
    for run in runs(store):
        if run.source == source and run.status == "SUCCEEDED":
            return run
    return None
