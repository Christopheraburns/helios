"""One crawl of one source (docs/crawler-analysis.md section 3).

Step 1 (CR-2): list the source's assets, decide which changed since the last
successful crawl, fetch and verify those, and record every asset with its status.
Steps 2 and 3 (CR-3): detect each verified asset's type and split it into
segments (``analyzers``). Later steps (mentions, resolution, claims) plug in
through ``analyze``, which receives each verified asset and its segments.

An unchanged asset is carried forward, segments included, only if the previous
run used the same crawler version and settings; otherwise it is analyzed again,
so a settings change always takes effect.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from helios_core.crawler.settings import CrawlerSettings
from helios_core.index import IndexStore, ids, runs
from helios_core.index.records import (
    AssetRecord,
    CrawlerSettingsRecord,
    CrawlRunRecord,
    SegmentRecord,
)

from .analyzers import Segment, analyze_asset
from .connectors import Connector, Fetched, SourceAsset, plan_incremental

CRAWLER_VERSION = "0.3.0"
ASSETS = "helios_index.assets"
SEGMENTS = "helios_index.segments"
# Statuses whose asset version (and segments) the next run can carry forward:
# with the same content, crawler version and settings, the outcome can't change.
# Fetch problems (missing, fetch_failed, integrity_failed) are retried instead.
REUSABLE = {"analyzed", "carried_forward", "unsupported", "type_mismatch", "no_text", "invalid"}

Analyzer = Callable[[CrawlRunRecord, Fetched, list[SegmentRecord]], None]


def _asset_row(
    run: CrawlRunRecord, asset: SourceAsset, status: str, detail: str = ""
) -> AssetRecord:
    return AssetRecord(
        crawl_run_id=run.crawl_run_id,
        asset_id=asset.asset_id,
        asset_version_id=asset.asset_version_id,
        connector=run.connector,
        source=asset.source,
        ontology_class=asset.ontology_class,
        mime_type=asset.mime_type,
        source_locator=asset.locator,
        size_bytes=asset.size_bytes if asset.size_bytes is not None else -1,
        semantic_timestamp=asset.semantic_timestamp,
        ontology_version=run.ontology_version,
        status=status,
        status_detail=detail,
    )


def previous_run(index: IndexStore, source: str, settings_hash: str) -> CrawlRunRecord | None:
    """The last successful crawl of ``source`` whose results can be reused: same
    crawler version and settings as this one."""
    last = runs.latest_succeeded(index, source)
    if (
        last is None
        or last.crawler_version != CRAWLER_VERSION
        or last.settings_hash != settings_hash
    ):
        return None
    return last


def previous_assets(index: IndexStore, run: CrawlRunRecord | None) -> dict[str, AssetRecord]:
    """asset_id -> the reusable asset rows of ``run``."""
    if run is None:
        return {}
    return {
        a.asset_id: a
        for a in index.read(ASSETS, {"crawl_run_id": run.crawl_run_id})
        if isinstance(a, AssetRecord) and a.status in REUSABLE
    }


def segment_rows(
    run: CrawlRunRecord, asset: SourceAsset, segments: list[Segment]
) -> list[SegmentRecord]:
    return [
        SegmentRecord(
            crawl_run_id=run.crawl_run_id,
            segment_id=ids.segment_id(asset.asset_version_id, ordinal),
            asset_id=asset.asset_id,
            segment_type=segment.segment_type,
            ordinal=ordinal,
            locator=segment.locator,
            text=segment.text,
            ontology_version=run.ontology_version,
            structure=segment.fields,
        )
        for ordinal, segment in enumerate(segments)
    ]


def crawl(
    index: IndexStore,
    connector: Connector,
    source_id: str,
    *,
    actor: str,
    ontology_version: str,
    settings: CrawlerSettingsRecord | None,
    settings_hash: str,
    crawler_settings: CrawlerSettings,
    analyze: Analyzer | None = None,
    full: bool = False,
    source_snapshot: dict | None = None,
) -> CrawlRunRecord:
    """Crawl one data source with ``connector``. ``full`` re-fetches and re-analyzes
    everything. ``source_snapshot`` (the data source's configuration, no secrets)
    is recorded on the run so results stay explainable."""
    run = runs.start(
        index,
        connector=connector.TYPE,
        source=source_id,
        ontology_version=ontology_version,
        crawler_version=CRAWLER_VERSION,
        actor=actor,
        settings={"data_source": source_snapshot or {}, "full": full},
        settings_version=settings.version if settings else None,
        settings_hash=settings_hash,
    )
    try:
        assets = connector.list_assets()
        previous = None if full else previous_run(index, source_id, settings_hash)
        known = previous_assets(index, previous)
        to_fetch, unchanged = plan_incremental(
            assets, {k: a.asset_version_id for k, a in known.items()}
        )
        rows: list[AssetRecord] = []
        segments: list[SegmentRecord] = []
        for fetched in connector.fetch_all(to_fetch):
            if fetched.status != "fetched" or fetched.data is None:
                rows.append(_asset_row(run, fetched.asset, fetched.status, fetched.detail))
                continue
            analysis = analyze_asset(fetched.asset.mime_type, fetched.data, crawler_settings)
            rows.append(_asset_row(run, fetched.asset, analysis.status, analysis.detail))
            asset_segments = segment_rows(run, fetched.asset, analysis.segments)
            segments += asset_segments
            if analysis.status == "analyzed" and analyze is not None:
                analyze(run, fetched, asset_segments)
        if unchanged and previous is not None:
            keep = {a.asset_id for a in unchanged}
            segments += [
                s.model_copy(update={"crawl_run_id": run.crawl_run_id})
                for s in index.read(SEGMENTS, {"crawl_run_id": previous.crawl_run_id})
                if isinstance(s, SegmentRecord) and s.asset_id in keep
            ]
        for asset in unchanged:
            before = known[asset.asset_id]
            if before.status in ("analyzed", "carried_forward"):
                rows.append(_asset_row(run, asset, "carried_forward"))
            else:  # keep why it wasn't analyzed
                detail = f"{before.status_detail} (unchanged since the last run)".strip()
                rows.append(_asset_row(run, asset, before.status, detail))
        index.append(ASSETS, rows)
        index.append(SEGMENTS, segments)
        counts = Counter(r.status for r in rows)
        return runs.finish(
            index,
            run,
            {"listed": len(assets), **dict(sorted(counts.items())), "segments": len(segments)},
        )
    except Exception as exc:
        runs.fail(index, run, f"{type(exc).__name__}: {exc}")
        raise
