"""One crawl of one source (docs/crawler-analysis.md section 3).

Step 1 (CR-2): list the source's assets, decide which changed since the last
successful crawl, fetch and verify those, and record every asset with its status.
Steps 2 and 3 (CR-3): detect each verified asset's type and split it into
segments (``analyzers``). Step 5 (CR-4): find mentions in the segments
(``mentions``), when a gazetteer and resolution config are given. Steps 6-11
(CR-5): resolve the run's mentions to entities, jointly per case cluster
(``resolution``), after every asset has been read: a case spans several
documents, so resolution runs over all mentions of the run, carried-forward
ones included, and its entities, links and relationships are recomputed each
run rather than carried. Step 12 (CR-6): extract claims from the run's units
(sentences and chat messages) with the resolved entities as subjects and
objects (``claims``), after resolution. ``analyze`` still receives each verified
asset with its segments and mentions, for anything that plugs in per asset.

An unchanged asset is carried forward, segments included, only if the previous
run used the same crawler version, settings and strategy; otherwise it is
analyzed again, so a settings change always takes effect.

``strategy`` (CR-E2) says how the text is understood and is recorded on the run:
``deterministic`` is everything above; ``llm`` (arm B, ``llm_arm``) shares
steps 1 to 3 and replaces the rest with a model's reading, grounded to exact
spans and resolved by exact keys only.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Any

from helios_core.crawler.settings import CrawlerSettings
from helios_core.index import IndexStore, ids, runs
from helios_core.index.records import (
    AssetRecord,
    CrawlerSettingsRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    EntityRecord,
    MentionRecord,
    RelationshipRecord,
    SegmentRecord,
)
from helios_core.ontology.mapping import ResolutionConfig

from . import coverage
from .analyzers import Segment, analyze_asset
from .claims import extract_claims
from .connectors import Connector, Fetched, SourceAsset, plan_incremental
from .gazetteer import Gazetteer
from .llm_arm import LlmExtractor, run_arm
from .mentions import extract_mentions
from .resolution import resolve

CRAWLER_VERSION = "0.7.0"
STRATEGIES = ("deterministic", "llm")
ASSETS = "helios_index.assets"
SEGMENTS = "helios_index.segments"
MENTIONS = "helios_index.mentions"
ENTITIES = "helios_index.entities"
ENTITY_LINKS = "helios_index.entity_links"
RELATIONSHIPS = "helios_index.relationships"
CLAIMS = "helios_index.claims"
CLAIM_EVIDENCE = "helios_index.claim_evidence"
# Statuses whose asset version (and segments) the next run can carry forward:
# with the same content, crawler version and settings, the outcome can't change.
# Fetch problems (missing, fetch_failed, integrity_failed) are retried instead.
REUSABLE = {"analyzed", "carried_forward", "unsupported", "type_mismatch", "no_text", "invalid"}

Analyzer = Callable[[CrawlRunRecord, Fetched, list[SegmentRecord], list[MentionRecord]], None]
# Makes the run's segments searchable; returns how many it embedded (CR-11).
Embed = Callable[
    [CrawlRunRecord, list[SegmentRecord], list[EntityRecord], list[RelationshipRecord]], int
]
LOGGER = logging.getLogger(__name__)


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


def strategy_of(run: CrawlRunRecord) -> str:
    """Runs from before CR-E2 recorded no strategy; they were deterministic."""
    return str((run.settings or {}).get("strategy") or "deterministic")


def previous_run(
    index: IndexStore, source: str, settings_hash: str, strategy: str = "deterministic"
) -> CrawlRunRecord | None:
    """The last successful crawl of ``source`` with this strategy, if its results
    can be reused: same crawler version and settings as this one."""
    last = next(
        (
            r
            for r in runs.runs(index)
            if r.source == source and r.status == "SUCCEEDED" and strategy_of(r) == strategy
        ),
        None,
    )
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
    request: dict | None = None,
    gazetteer: Gazetteer | None = None,
    resolution: ResolutionConfig | None = None,
    warehouse_cursor: Callable[[], Any] | None = None,
    embed: Embed | None = None,
    strategy: str = "deterministic",
    llm: LlmExtractor | None = None,
    mapping: dict | None = None,
) -> CrawlRunRecord:
    """Crawl one data source with ``connector``. ``full`` re-fetches and re-analyzes
    everything. ``source_snapshot`` (the data source's configuration, no secrets)
    is recorded on the run so results stay explainable. Mentions are extracted,
    resolved and turned into claims when both ``gazetteer`` and ``resolution``
    are given; joint resolution against the warehouse needs ``warehouse_cursor``
    (a read-only cursor factory on the source schema). Strategy ``llm`` needs
    ``llm`` (the extractor) as well, and queries nothing in the warehouse."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown crawl strategy {strategy!r}")
    if strategy == "llm" and (llm is None or gazetteer is None or resolution is None):
        raise ValueError("the llm strategy needs a model, a gazetteer and a resolution config")
    run = runs.start(
        index,
        connector=connector.TYPE,
        source=source_id,
        ontology_version=ontology_version,
        crawler_version=CRAWLER_VERSION,
        actor=actor,
        settings={
            "data_source": source_snapshot or {},
            "full": full,
            "strategy": strategy,
            **({"llm": llm.provenance()} if strategy == "llm" and llm else {}),
            # Which mapping the crawl resolved against (model, version, hash, database).
            **({"mapping": mapping} if mapping else {}),
            **({"request": request} if request else {}),
        },
        settings_version=settings.version if settings else None,
        settings_hash=settings_hash,
    )
    try:
        assets = connector.list_assets()
        previous = None if full else previous_run(index, source_id, settings_hash, strategy)
        known = previous_assets(index, previous)
        to_fetch, unchanged = plan_incremental(
            assets, {k: a.asset_version_id for k, a in known.items()}
        )
        rows: list[AssetRecord] = []
        segments: list[SegmentRecord] = []
        mentions: list[MentionRecord] = []
        for fetched in connector.fetch_all(to_fetch):
            if fetched.status != "fetched" or fetched.data is None:
                rows.append(_asset_row(run, fetched.asset, fetched.status, fetched.detail))
                continue
            analysis = analyze_asset(fetched.asset.mime_type, fetched.data, crawler_settings)
            rows.append(_asset_row(run, fetched.asset, analysis.status, analysis.detail))
            asset_segments = segment_rows(run, fetched.asset, analysis.segments)
            segments += asset_segments
            if analysis.status != "analyzed":
                continue
            asset_mentions: list[MentionRecord] = []
            if strategy == "deterministic" and gazetteer is not None and resolution is not None:
                asset_mentions = extract_mentions(
                    run, fetched.asset, asset_segments, crawler_settings, gazetteer, resolution
                )
                mentions += asset_mentions
            if analyze is not None:
                analyze(run, fetched, asset_segments, asset_mentions)
        if unchanged and previous is not None:
            keep = {a.asset_id for a in unchanged}
            segments += [
                s.model_copy(update={"crawl_run_id": run.crawl_run_id})
                for s in index.read(SEGMENTS, {"crawl_run_id": previous.crawl_run_id})
                if isinstance(s, SegmentRecord) and s.asset_id in keep
            ]
            if strategy == "deterministic":  # the llm arm reads every document again, from its cache
                mentions += [
                    m.model_copy(update={"crawl_run_id": run.crawl_run_id})
                    for m in index.read(MENTIONS, {"crawl_run_id": previous.crawl_run_id})
                    if isinstance(m, MentionRecord) and m.asset_id in keep
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
        resolution_counts: dict[str, int] = {}
        entities: list[EntityRecord] = []
        relationships: list[RelationshipRecord] = []
        links: list[EntityLinkRecord] = []
        readable = [r for r in rows if r.status in ("analyzed", "carried_forward")]
        if strategy == "llm" and llm is not None and gazetteer is not None and resolution is not None:
            arm = run_arm(run, readable, segments, llm, gazetteer, resolution, crawler_settings)
            mentions = arm.mentions
            index.append(MENTIONS, mentions)
            index.append(ENTITIES, arm.resolution.entities)
            index.append(ENTITY_LINKS, arm.resolution.links)
            index.append(RELATIONSHIPS, arm.resolution.relationships)
            index.append(CLAIMS, arm.claims)
            index.append(CLAIM_EVIDENCE, arm.evidence)
            resolution_counts = arm.counts
            entities, relationships = arm.resolution.entities, arm.resolution.relationships
            links = arm.resolution.links
        elif gazetteer is not None and resolution is not None:
            index.append(MENTIONS, mentions)
            resolved = resolve(
                run,
                readable,
                segments,
                mentions,
                gazetteer,
                resolution,
                crawler_settings,
                warehouse_cursor,
            )
            index.append(ENTITIES, resolved.entities)
            index.append(ENTITY_LINKS, resolved.links)
            index.append(RELATIONSHIPS, resolved.relationships)
            extracted = extract_claims(
                run,
                resolved.clusters,
                readable,
                segments,
                mentions,
                resolved.links,
                resolved.entities,
                resolved.relationships,
                crawler_settings,
            )
            index.append(CLAIMS, extracted.claims)
            index.append(CLAIM_EVIDENCE, extracted.evidence)
            resolution_counts = {**resolved.counts, **extracted.counts}
            entities, relationships = resolved.entities, resolved.relationships
            links = resolved.links
        else:
            index.append(MENTIONS, mentions)
        # What the crawl could not explain (CG-10): totals on the run, examples as rows.
        coverage_counts, coverage_rows = coverage.measure(
            run, segments, mentions, links, crawler_settings
        )
        index.append(coverage.COVERAGE, coverage_rows)
        if "cases" in resolution_counts:
            resolution_counts["cases_unresolved"] = (
                resolution_counts["cases"] - resolution_counts.get("cases_resolved", 0)
            )
        # The index is complete without embeddings, so a failure here is counted
        # on the run instead of failing it.
        embedding_counts: dict[str, int] = {}
        if embed is not None:
            try:
                embedding_counts = {"embedded": embed(run, segments, entities, relationships)}
            except Exception:
                LOGGER.exception("embedding the segments of %s failed", run.crawl_run_id)
                embedding_counts = {"embedding_failed": 1}
        counts = Counter(r.status for r in rows)
        return runs.finish(
            index,
            run,
            {
                "listed": len(assets),
                **dict(sorted(counts.items())),
                "segments": len(segments),
                "mentions": len(mentions),
                **resolution_counts,
                **coverage_counts,
                **embedding_counts,
            },
        )
    except Exception as exc:
        runs.fail(index, run, f"{type(exc).__name__}: {exc}")
        raise
