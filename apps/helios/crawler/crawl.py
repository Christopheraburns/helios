"""One crawl of one source (docs/crawler-analysis.md section 3).

CR-2 covers step 1: list the source's assets, decide which changed since the
last successful crawl, fetch and verify those, and record every asset with its
status. Later steps (segments, mentions, resolution, claims) plug in through
``analyze``, which receives each verified asset's bytes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from helios_core.index import IndexStore, runs
from helios_core.index.records import AssetRecord, CrawlerSettingsRecord, CrawlRunRecord

from .connector import CONNECTOR, Fetched, HeliosDsConnector, SourceAsset, plan_incremental

CRAWLER_VERSION = "0.1.0"
ASSETS = "helios_index.assets"
# Statuses whose asset version can be carried forward by the next run.
REUSABLE = {"fetched", "carried_forward", "analyzed"}

Analyzer = Callable[[CrawlRunRecord, Fetched], None]


def _asset_row(
    run: CrawlRunRecord, asset: SourceAsset, status: str, detail: str = ""
) -> AssetRecord:
    return AssetRecord(
        crawl_run_id=run.crawl_run_id,
        asset_id=asset.asset_id,
        asset_version_id=asset.asset_version_id,
        connector=CONNECTOR,
        source=asset.source,
        ontology_class=asset.ontology_class,
        mime_type=asset.mime_type,
        source_locator=asset.locator,
        size_bytes=asset.size_bytes,
        semantic_timestamp=asset.semantic_timestamp,
        ontology_version=run.ontology_version,
        status=status,
        status_detail=detail,
    )


def previous_versions(index: IndexStore, source: str) -> dict[str, str]:
    """asset_id -> asset_version_id from the last successful crawl of ``source``."""
    last = runs.latest_succeeded(index, source)
    if last is None:
        return {}
    return {
        a.asset_id: a.asset_version_id
        for a in index.read(ASSETS, {"crawl_run_id": last.crawl_run_id})
        if isinstance(a, AssetRecord) and a.status in REUSABLE
    }


def crawl(
    index: IndexStore,
    connector: HeliosDsConnector,
    dataset_id: str,
    *,
    actor: str,
    ontology_version: str,
    settings: CrawlerSettingsRecord | None,
    settings_hash: str,
    analyze: Analyzer | None = None,
    full: bool = False,
) -> CrawlRunRecord:
    """Crawl one Helios-DS dataset. ``full`` re-fetches unchanged assets too."""
    run = runs.start(
        index,
        connector=CONNECTOR,
        source=dataset_id,
        ontology_version=ontology_version,
        crawler_version=CRAWLER_VERSION,
        actor=actor,
        settings={"dataset_id": dataset_id, "full": full},
        settings_version=settings.version if settings else None,
        settings_hash=settings_hash,
    )
    try:
        assets = connector.list_assets(dataset_id)
        known = {} if full else previous_versions(index, dataset_id)
        to_fetch, unchanged = plan_incremental(assets, known)
        rows: list[AssetRecord] = []
        for fetched in connector.fetch_all(to_fetch):
            rows.append(_asset_row(run, fetched.asset, fetched.status, fetched.detail))
            if fetched.status == "fetched" and analyze is not None:
                analyze(run, fetched)
        rows += [_asset_row(run, a, "carried_forward") for a in unchanged]
        index.append(ASSETS, rows)
        counts = Counter(r.status for r in rows)
        return runs.finish(index, run, {"listed": len(assets), **dict(sorted(counts.items()))})
    except Exception as exc:
        runs.fail(index, run, f"{type(exc).__name__}: {exc}")
        raise
