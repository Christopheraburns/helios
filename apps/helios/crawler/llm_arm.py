"""Arm B, the LLM crawler (docs/crawler-analysis.md section 9; strategy ``llm``).

It shares fetching, type detection, segments and the output tables with the
deterministic crawler, and differs in understanding:

- **Finding:** one call per document. The model is given the ontology classes
  and claim vocabulary (never the ground truth) and returns entities and
  claims, each with the exact text it read.
- **Grounding:** every quote is mapped back to character offsets in its
  segment. A quote that is nowhere in the document is dropped and counted as
  hallucinated; so is a claim whose quote is. Nothing ungrounded is written.
- **Resolving:** exact keys only. A quoted business key (customer ID, email
  address, item ID) that names exactly one warehouse row links to it, and a
  quoted document ID (RMA, case number) is its own Return entity. No names, no
  case grouping, no warehouse queries.
- **Claims** need a subject and an object that resolved, of the classes the
  predicate declares; others are counted as unanchored.

Responses are cached on the project filesystem by provider, model, prompt hash
and document text, so a rerun costs nothing and gives the same rows. Temperature
is 0. Provider, model, prompt version and hash are recorded on the run; token,
cost and hallucination totals are in its counts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from helios_core.crawler.settings import CrawlerSettings
from helios_core.index import ids
from helios_core.index.records import (
    AssetRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    MentionRecord,
    SegmentRecord,
)
from helios_core.ontology.mapping import ResolutionConfig

from .cases import Rules
from .claims import SHAPES, Unit, evidence_locator
from .gazetteer import Gazetteer, normalise
from .mentions import Found, _record
from .resolution import EntityBook, Link, Reader, Reading, Resolution, derive_relationships

LOGGER = logging.getLogger(__name__)
EXTRACTOR = "llm"
CONFIDENCE = 0.8  # the model gives no calibrated score; one value for every claim
RATE_LIMIT_WAITS = (15, 30, 60, 120)  # seconds, after the client's own retries
REPLY_FORMAT = (
    'Reply with one JSON object: {"entities": [{"id": "e1", "segment": 0, "quote": "...", '
    '"class": "<class>"}], "claims": [{"predicate": "<predicate>", "segment": 0, '
    '"quote": "...", "subject": "e1", "object": "e2"}]}. "segment" is the number in the '
    "segment's heading. Use empty lists when there is nothing to report."
)


@dataclass(frozen=True)
class Vocabulary:
    """What the model is told: the classes to look for and the claims to report."""

    classes: dict[str, str]  # class -> definition
    predicates: dict[str, tuple[str, str, str]]  # predicate -> (definition, subject, object)


def load_vocabulary(directory: Path, classes: Sequence[str], predicates: Sequence[str]) -> Vocabulary:
    """Definitions from the ontology's LinkML files, read as plain YAML."""
    import yaml

    class_text: dict[str, str] = {}
    predicate_text: dict[str, str] = {}
    for path in sorted(directory.rglob("*.yaml")):
        if "mappings" in path.parts:
            continue
        document = yaml.safe_load(path.read_text()) or {}
        if not isinstance(document, dict):
            continue
        for name, body in (document.get("classes") or {}).items():
            if name in classes and isinstance(body, dict) and body.get("description"):
                class_text.setdefault(name, " ".join(str(body["description"]).split()))
        for enum in (document.get("enums") or {}).values():
            for name, body in ((enum or {}).get("permissible_values") or {}).items():
                if name in predicates and isinstance(body, dict) and body.get("description"):
                    predicate_text.setdefault(name, " ".join(str(body["description"]).split()))
    return Vocabulary(
        {name: class_text.get(name, "") for name in sorted(classes)},
        {
            name: (predicate_text.get(name, ""), *SHAPES[name])
            for name in sorted(predicates)
            if name in SHAPES
        },
    )


def system_prompt(settings: CrawlerSettings, vocabulary: Vocabulary) -> str:
    classes = "\n".join(f"- {name}: {text}" for name, text in vocabulary.classes.items())
    predicates = "\n".join(
        f"- {name} (subject: {subject}, object: {obj}): {text}"
        for name, (text, subject, obj) in vocabulary.predicates.items()
    )
    return (
        f"{settings.llm.instructions.strip()}\n\nEntity classes:\n{classes}\n\n"
        f"Claim predicates:\n{predicates}\n\n{REPLY_FORMAT}"
    )


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


@dataclass
class RawClaim:
    predicate: str
    unit: Unit
    subject: str  # mention_id
    obj: str


@dataclass
class AssetExtraction:
    mentions: list[MentionRecord] = field(default_factory=list)
    claims: list[RawClaim] = field(default_factory=list)


class LlmExtractor:
    """Calls the model per document, grounds what it returns, and keeps the totals."""

    def __init__(
        self,
        llm: Any,
        settings: CrawlerSettings,
        vocabulary: Vocabulary,
        cache_dir: str | os.PathLike[str] | None,
        sleep: Any = time.sleep,
    ):
        self.llm = llm
        self.settings = settings
        self.vocabulary = vocabulary
        self.system = system_prompt(settings, vocabulary)
        self.prompt_hash = _sha(self.system)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.sleep = sleep
        self.counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def provenance(self) -> dict[str, Any]:
        """What produced the rows; never a key or an endpoint."""
        return {
            "provider": self.llm.provider,
            "model": self.llm.model,
            "temperature": self.llm.temperature,
            "prompt_version": self.settings.llm.prompt_version,
            "prompt_hash": self.prompt_hash,
        }

    def _count(self, **changes: int) -> None:
        with self._lock:
            self.counts.update(changes)

    def document_text(self, segments: Sequence[SegmentRecord]) -> str:
        parts = [f"[segment {s.ordinal} | {s.segment_type}]\n{s.text}" for s in segments]
        text = "\n\n".join(parts)
        limit = self.settings.llm.max_asset_chars
        if len(text) > limit:
            self._count(llm_truncated=1)
            text = text[:limit]
        return text

    def _reply(self, user: str) -> str | None:
        key = _sha(self.llm.provider, self.llm.model, self.prompt_hash, user)
        path = self.cache_dir / f"{key}.json" if self.cache_dir else None
        if path is not None and path.exists():
            self._count(llm_cached=1)
            return str(json.loads(path.read_text())["text"])
        started = time.perf_counter()
        for wait in (*RATE_LIMIT_WAITS, None):
            try:
                text, tokens_in, tokens_out = self.llm.complete_with_usage(
                    self.system + "\n\nRespond with the JSON object and nothing else.", user
                )
                break
            except Exception as exc:
                if wait is None or " 429" not in str(exc):
                    LOGGER.warning("llm call failed: %s", str(exc)[:300])
                    self._count(llm_failed=1)
                    return None
                self._count(llm_rate_limited=1)
                self.sleep(wait)
        self._count(
            llm_calls=1,
            llm_tokens_in=tokens_in,
            llm_tokens_out=tokens_out,
            llm_ms=int((time.perf_counter() - started) * 1000),
        )
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{threading.get_ident()}.tmp")
            temporary.write_text(
                json.dumps({"text": text, "tokens_in": tokens_in, "tokens_out": tokens_out})
            )
            temporary.replace(path)
        return text

    def extract(self, run: CrawlRunRecord, segments: Sequence[SegmentRecord]) -> AssetExtraction:
        """One document's grounded mentions and claims."""
        from helios_core.llm.client import _parse_json

        segments = sorted(segments, key=lambda s: s.ordinal)
        result = AssetExtraction()
        if not any(s.text.strip() for s in segments):
            return result
        reply = self._reply(self.document_text(segments))
        if reply is None:
            return result
        try:
            parsed = _parse_json(reply)
        except ValueError:
            self._count(llm_invalid_replies=1)
            return result
        if not isinstance(parsed, dict):
            self._count(llm_invalid_replies=1)
            return result
        by_ordinal = {s.ordinal: s for s in segments}
        used: dict[tuple[str, str], int] = defaultdict(int)  # (segment, quote) -> next search start

        def ground(item: dict[str, Any], repeat: bool) -> tuple[SegmentRecord, int, int] | None:
            quote = item.get("quote")
            if not isinstance(quote, str) or not quote.strip():
                return None
            named = by_ordinal.get(item.get("segment")) if isinstance(item.get("segment"), int) else None
            for segment in ([named] if named else []) + [s for s in segments if s is not named]:
                start = segment.text.find(quote, used[(segment.segment_id, quote)] if repeat else 0)
                if start < 0 and repeat:
                    start = segment.text.find(quote)  # quoted again: the same span
                if start >= 0:
                    if repeat:
                        used[(segment.segment_id, quote)] = start + len(quote)
                    if segment is not named:
                        self._count(llm_relocated=1)
                    return segment, start, start + len(quote)
            return None

        mention_of: dict[str, str] = {}
        seen: dict[str, MentionRecord] = {}
        for item in parsed.get("entities") or []:
            if not isinstance(item, dict):
                continue
            self._count(llm_entities=1)
            class_name = item.get("class")
            if class_name not in self.vocabulary.classes:
                self._count(llm_invalid_items=1)
                continue
            spot = ground(item, repeat=True)
            if spot is None:
                self._count(llm_hallucinated_spans=1)
                continue
            segment, start, end = spot
            found = Found(
                start, end, segment.text[start:end], class_name, EXTRACTOR,
                self.settings.llm.prompt_version,
            )
            record = _record(run, segment, found)
            seen.setdefault(record.mention_id, record)
            if isinstance(item.get("id"), str):
                mention_of[item["id"]] = record.mention_id
        result.mentions = list(seen.values())
        for item in parsed.get("claims") or []:
            if not isinstance(item, dict):
                continue
            self._count(llm_claims=1)
            predicate = item.get("predicate")
            if predicate not in self.vocabulary.predicates:
                self._count(llm_invalid_items=1)
                continue
            spot = ground(item, repeat=False)
            if spot is None:
                self._count(llm_hallucinated_spans=1)
                continue
            subject = mention_of.get(str(item.get("subject")))
            obj = mention_of.get(str(item.get("object")))
            if subject is None or obj is None:
                self._count(llm_claims_unanchored=1)
                continue
            result.claims.append(RawClaim(predicate, Unit(*spot), subject, obj))
        return result

    def extract_all(
        self, run: CrawlRunRecord, by_asset: dict[str, list[SegmentRecord]]
    ) -> dict[str, AssetExtraction]:
        order = sorted(by_asset)
        with ThreadPoolExecutor(max_workers=self.settings.llm.concurrency) as pool:
            results = list(pool.map(lambda asset_id: self.extract(run, by_asset[asset_id]), order))
        return dict(zip(order, results, strict=True))

    def totals(self) -> dict[str, int]:
        counts = dict(sorted(self.counts.items()))
        prices = (self.settings.llm.input_price_per_million, self.settings.llm.output_price_per_million)
        if prices[0] is not None and prices[1] is not None:
            # Micro-dollars, because run counts are whole numbers.
            counts["llm_cost_microusd"] = round(
                counts.get("llm_tokens_in", 0) * prices[0] + counts.get("llm_tokens_out", 0) * prices[1]
            )
        return counts


# --- resolving and claims ---------------------------------------------------------------


def resolve_exact(
    run: CrawlRunRecord,
    assets: Sequence[AssetRecord],
    segments: Sequence[SegmentRecord],
    mentions: Sequence[MentionRecord],
    gazetteer: Gazetteer,
    config: ResolutionConfig,
    settings: CrawlerSettings,
    source_schema: str = "tpcds",
) -> tuple[Resolution, dict[str, Link]]:
    """Arm B's resolution: a quoted business key naming exactly one warehouse
    row, or a document's own ID. Returns the resolution and each linked
    mention's link."""
    rules = Rules(settings)
    reader = Reader(gazetteer, config, rules)
    book = EntityBook(gazetteer, config, source_schema)
    for mention in sorted(mentions, key=lambda m: m.mention_id):
        # Names and keys as the documents write them, for the entities' display names.
        book._written.setdefault(
            (mention.proposed_class or "", normalise(mention.surface_form)), mention.surface_form
        )
    links: dict[str, Link] = {}
    for mention in sorted(mentions, key=lambda m: m.mention_id):
        class_name = mention.proposed_class or ""
        value = mention.surface_form.strip()
        rule = rules.document_rule(value)
        target: str | None = None
        if rule is not None and rule.proposed_class == class_name:
            target = reader._document_id(value, class_name)
        else:
            identifiers = config.classes.get(class_name)
            if identifiers is not None and identifiers.secondary:
                reading = Reading(mention)
                reader._from_keys(reading, identifiers.secondary, mention)
                exact = [c for c in reading.candidates if c.class_name == class_name]
                if len(exact) == 1:
                    target = exact[0].instance
        if target is None:
            continue
        book.add(target, class_name)
        links[mention.mention_id] = Link(
            mention, target, class_name, "SameAs", "exact_key", 1.0, [mention.segment_id]
        )
    link_records = [
        EntityLinkRecord(
            crawl_run_id=run.crawl_run_id,
            link_id=ids.link_id(mention_id, book.entity_id(link.target)),
            mention_id=mention_id,
            entity_id=book.entity_id(link.target),
            link_type=link.link_type,
            resolved_by=link.resolved_by,
            score=link.score,
            evidence_segment_ids=list(link.evidence),
            ontology_version=run.ontology_version,
        )
        for mention_id, link in sorted(links.items())
    ]
    relationships = derive_relationships(
        run, assets, segments, {m: [l] for m, l in links.items()}, {}, {}, {}, book
    )
    resolution = Resolution(
        entities=book.records(run),
        links=link_records,
        relationships=relationships,
        clusters={},
        queried=0,
        resolved=0,
    )
    return resolution, {m: l for m, l in links.items()}


def build_claims(
    run: CrawlRunRecord,
    raw: Sequence[RawClaim],
    links: dict[str, Link],
    book_id: Any,
) -> tuple[list[ClaimRecord], list[ClaimEvidenceRecord], int]:
    """Claims whose subject and object resolved to the classes the predicate
    declares; the rest are counted as unanchored."""
    claims: dict[str, ClaimRecord] = {}
    evidence: dict[str, ClaimEvidenceRecord] = {}
    unanchored = 0
    for item in raw:
        subject, obj = links.get(item.subject), links.get(item.obj)
        shape = SHAPES[item.predicate]
        if subject is None or obj is None or (subject.class_name, obj.class_name) != shape:
            unanchored += 1
            continue
        subject_id, object_id = book_id(subject), book_id(obj)
        claim_id = ids.claim_id(item.predicate, subject_id, object_id)
        claims.setdefault(
            claim_id,
            ClaimRecord(
                crawl_run_id=run.crawl_run_id,
                claim_id=claim_id,
                predicate=item.predicate,
                subject_entity_id=subject_id,
                object_entity_id=object_id,
                object_value=None,
                confidence=CONFIDENCE,
                extractor=EXTRACTOR,
                ontology_version=run.ontology_version,
            ),
        )
        unit = item.unit
        evidence_id = ids.evidence_id(claim_id, unit.segment.segment_id, unit.start, unit.end)
        evidence.setdefault(
            evidence_id,
            ClaimEvidenceRecord(
                crawl_run_id=run.crawl_run_id,
                claim_id=claim_id,
                evidence_id=evidence_id,
                asset_id=unit.segment.asset_id,
                segment_id=unit.segment.segment_id,
                locator=evidence_locator(unit),
                excerpt=unit.text,
                ontology_version=run.ontology_version,
            ),
        )
    return (
        [claims[k] for k in sorted(claims)],
        [evidence[k] for k in sorted(evidence)],
        unanchored,
    )


@dataclass
class ArmResult:
    mentions: list[MentionRecord]
    resolution: Resolution
    claims: list[ClaimRecord]
    evidence: list[ClaimEvidenceRecord]
    counts: dict[str, int]


def run_arm(
    run: CrawlRunRecord,
    assets: Sequence[AssetRecord],
    segments: Sequence[SegmentRecord],
    extractor: LlmExtractor,
    gazetteer: Gazetteer,
    config: ResolutionConfig,
    settings: CrawlerSettings,
) -> ArmResult:
    """Everything after segments, for the run's analyzed assets."""
    wanted = {a.asset_id for a in assets}
    by_asset: dict[str, list[SegmentRecord]] = defaultdict(list)
    for segment in segments:
        if segment.asset_id in wanted:
            by_asset[segment.asset_id].append(segment)
    extracted = extractor.extract_all(run, dict(by_asset))
    mentions = [m for asset_id in sorted(extracted) for m in extracted[asset_id].mentions]
    resolution, links = resolve_exact(run, assets, segments, mentions, gazetteer, config, settings)
    raw = [c for asset_id in sorted(extracted) for c in extracted[asset_id].claims]
    claims, evidence, unanchored = build_claims(
        run, raw, links, lambda link: ids.entity_id(link.class_name, link.target)
    )
    totals = extractor.totals()
    totals["llm_claims_unanchored"] = totals.get("llm_claims_unanchored", 0) + unanchored
    return ArmResult(
        mentions,
        resolution,
        claims,
        evidence,
        {
            **{k: v for k, v in resolution.counts.items() if not k.startswith("cases")},
            "claims": len(claims),
            "claim_evidence": len(evidence),
            **totals,
        },
    )
