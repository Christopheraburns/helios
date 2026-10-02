"""The crawler evaluation harness (CR-8 / CR-E1; docs/crawler-analysis.md sections 6
and 9): score one crawl run against a Helios-DS ground-truth dataset.

The harness is the only part of Helios that reads ``helios_ground_truth``; the
crawler never does, and nothing here is imported by it. It runs as the
evaluating principal (proxy delegation) or as the API's workload user, and each
record says which (``evaluator_mode``).

``evaluate`` loads the truth and the run's index rows and hands them to the pure
``score_*`` functions, which take already-loaded rows so tests run on DuckDB with
synthetic truth. Every metric carries its counts, so a number can be audited,
and iteration is sorted, so two evaluations of the same run give identical
``metrics``.

What is compared (crawler conventions, docs/crawler-burndown.md "Locators"):
- a truth mention is *found* when a crawler mention in the same asset and
  locator part overlaps its character span, or, without offsets (PDF ``page``
  and email ``header`` locators), names the same surface form;
- entity keys are ``tpcds.<table>:<key columns sorted>``, as the crawler's
  ``external_ids`` (helios_core.index.ids.external_id); Artifact entities are
  asset IDs;
- ground-truth predicates map onto the ontology's relationship classes
  (``RELATIONSHIP_MAP``); PURCHASED_IN is reversed (Sale PartyTo Customer).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from helios_core.index import IndexStore, ids, runs
from helios_core.index.records import (
    AssetRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    EntityRecord,
    EvaluationRecord,
    MentionRecord,
    RelationshipRecord,
    SegmentRecord,
)

HARNESS_VERSION = "0.1.0"
EVALUATIONS = "helios_index.evaluations"
TRUTH = "helios_ground_truth"
SOURCE = "tpcds"
ANALYZED = {"analyzed", "carried_forward"}
MIN_CONTAINMENT = 5  # surface forms this long may match by containment
TOP_DIAGNOSTICS = 25

# Ground-truth predicate -> (ontology relationship class, swap source and target).
RELATIONSHIP_MAP: dict[str, tuple[str, bool]] = {
    "MENTIONS": ("Mentions", False),
    "DISCUSSES": ("About", False),
    "REFERS_TO_SALE": ("ReturnOf", False),
    "CONTAINS": ("Contains", False),
    "RETURNS_ITEM": ("Contains", False),
    "OCCURRED_AT": ("LocatedAt", False),
    "RETURNED_AT": ("LocatedAt", False),
    "PURCHASED_IN": ("PartyTo", True),
    "RETURNED_BY": ("PartyTo", False),
    "HAS_REASON": ("HasReason", False),
}

TRUTH_QUERIES = {
    "entities": "SELECT entity_id, entity_type, source_key, canonical_name",
    "entity_mentions": (
        "SELECT mention_id, entity_id, artifact_id, surface_form, start_offset, end_offset, "
        "scenario_id, entity_type, locator, difficulty"
    ),
    "relationships": (
        "SELECT relationship_id, source_entity_id, predicate, target_entity_id, artifact_id"
    ),
    "claims": "SELECT claim_id, scenario_id, claim_type, subject, obj, truth_status",
    "evidence": (
        "SELECT evidence_id, claim_id, artifact_id, segment_id, start_offset, end_offset, "
        "locator_type, locator, excerpt"
    ),
    "expected_queries": (
        "SELECT query_id, question, kind, difficulty, required_structured, required_entities, "
        "required_artifacts, required_claims, required_evidence"
    ),
}
TRUTH_JSON = {
    "source_key",
    "locator",
    "required_structured",
    "required_entities",
    "required_artifacts",
    "required_claims",
    "required_evidence",
}
DENIAL_MARKERS = ("AuthorizationException", "does not have privileges", "Permission denied")


class GroundTruthDenied(PermissionError):
    """Ranger refused the evaluator access to helios_ground_truth."""


class UnknownRun(LookupError):
    """No crawl run with that ID."""


class EvaluationFailed(RuntimeError):
    """Scoring raised; the FAILED record was written and is on ``record``."""

    def __init__(self, record: EvaluationRecord):
        super().__init__(record.error or "evaluation failed")
        self.record = record


Row = dict[str, Any]


@dataclass
class Truth:
    entities: list[Row] = field(default_factory=list)
    entity_mentions: list[Row] = field(default_factory=list)
    relationships: list[Row] = field(default_factory=list)
    claims: list[Row] = field(default_factory=list)
    evidence: list[Row] = field(default_factory=list)
    expected_queries: list[Row] = field(default_factory=list)


@dataclass
class IndexRows:
    assets: list[AssetRecord] = field(default_factory=list)
    segments: list[SegmentRecord] = field(default_factory=list)
    mentions: list[MentionRecord] = field(default_factory=list)
    entities: list[EntityRecord] = field(default_factory=list)
    links: list[EntityLinkRecord] = field(default_factory=list)
    relationships: list[RelationshipRecord] = field(default_factory=list)
    claims: list[ClaimRecord] = field(default_factory=list)
    claim_evidence: list[ClaimEvidenceRecord] = field(default_factory=list)


# --- loading -----------------------------------------------------------------------


def _is_denial(exc: BaseException) -> bool:
    return any(marker in str(exc) for marker in DENIAL_MARKERS)


def load_truth(cursor_factory: Callable[[], Any], dataset_id: str) -> Truth:
    """Every ground-truth table for ``dataset_id`` (bound parameter), as dicts
    with JSON columns decoded. A Ranger denial raises GroundTruthDenied."""
    cursor = cursor_factory()
    truth = Truth()
    for table, select in sorted(TRUTH_QUERIES.items()):
        sql = f"{select} FROM {TRUTH}.{table} WHERE dataset_id = ?"
        try:
            cursor.execute(sql, [dataset_id])
            rows = cursor.fetchall()
        except Exception as exc:
            if _is_denial(exc):
                raise GroundTruthDenied(f"{TRUTH}.{table}: {exc}") from exc
            raise
        names = [str(d[0]).split(".")[-1].lower() for d in cursor.description]
        decoded = []
        for row in rows:
            data = dict(zip(names, row, strict=True))
            for name in TRUTH_JSON & set(names):
                value = data.get(name)
                if isinstance(value, str):
                    data[name] = json.loads(value) if value.strip() else None
            decoded.append(data)
        setattr(truth, table, decoded)
    return truth


def load_index(index: IndexStore, crawl_run_id: str) -> IndexRows:
    where = {"crawl_run_id": crawl_run_id}
    return IndexRows(
        assets=index.read("helios_index.assets", where),  # type: ignore[arg-type]
        segments=index.read("helios_index.segments", where),  # type: ignore[arg-type]
        mentions=index.read("helios_index.mentions", where),  # type: ignore[arg-type]
        entities=index.read("helios_index.entities", where),  # type: ignore[arg-type]
        links=index.read("helios_index.entity_links", where),  # type: ignore[arg-type]
        relationships=index.read("helios_index.relationships", where),  # type: ignore[arg-type]
        claims=index.read("helios_index.claims", where),  # type: ignore[arg-type]
        claim_evidence=index.read("helios_index.claim_evidence", where),  # type: ignore[arg-type]
    )


# --- keys and locators ---------------------------------------------------------------


def truth_key(entity: Row) -> str:
    """The crawler's external ID for a ground-truth entity: ``tpcds.<table>:<keys>``,
    or the artifact ID for an Artifact entity."""
    source_key = dict(entity.get("source_key") or {})
    if "artifact_id" in source_key:
        return str(source_key["artifact_id"])
    table = source_key.pop("table", None) or entity.get("entity_type", "").lower()
    return ids.external_id(SOURCE, str(table), source_key)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return round(2 * precision * recall / (precision + recall), 4)


def locator_part(locator: dict[str, Any] | None) -> tuple[str, str] | None:
    """The addressable part a locator names: ("part", "body"), ("header", "From"),
    ("message_id", "msg-1") or ("page", "1")."""
    locator = locator or {}
    for key in ("part", "header", "message_id", "page"):
        if locator.get(key) is not None:
            return key, str(locator[key])
    return None


def _span(locator: dict[str, Any] | None, start: Any = None, end: Any = None):
    locator = locator or {}
    s = locator.get("start", start)
    e = locator.get("end", end)
    if s is None or e is None:
        return None
    return int(s), int(e)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).casefold()


def _surface_match(a: str | None, b: str | None) -> bool:
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if len(x) >= MIN_CONTAINMENT and len(y) >= MIN_CONTAINMENT:
        return x in y or y in x
    return False


def _segments_by_part(segments: Iterable[SegmentRecord]) -> dict[tuple, list[SegmentRecord]]:
    by_part: dict[tuple, list[SegmentRecord]] = defaultdict(list)
    for segment in segments:
        part = locator_part(segment.locator)
        if part is not None:
            by_part[(segment.asset_id, *part)].append(segment)
    return by_part


def _inside_segment(
    by_part: dict[tuple, list[SegmentRecord]], asset_id: str, locator: dict[str, Any] | None,
    start: Any = None, end: Any = None,
) -> bool:
    part = locator_part(locator)
    if part is None:
        return False
    candidates = by_part.get((asset_id, *part), [])
    span = _span(locator, start, end)
    if span is None:
        return bool(candidates)
    return any(0 <= span[0] <= span[1] <= len(s.text) for s in candidates)


# --- step 3: segments ------------------------------------------------------------------


def score_segments(
    truth_evidence: Sequence[Row], truth_mentions: Sequence[Row], segments: Sequence[SegmentRecord]
) -> dict[str, Any]:
    """Fraction of truth evidence rows and truth mentions whose locator falls inside
    a crawler segment of the same asset (same part; offsets within the text)."""
    by_part = _segments_by_part(segments)
    evidence_in = sum(
        _inside_segment(by_part, r["artifact_id"], r.get("locator"), r.get("start_offset"), r.get("end_offset"))
        for r in truth_evidence
    )
    mentions_in = sum(
        _inside_segment(by_part, r["artifact_id"], r.get("locator"), r.get("start_offset"), r.get("end_offset"))
        for r in truth_mentions
    )
    # PDF evidence carries the page text rather than offsets: say how much of it the
    # extracted page text actually contains (a looser, informative check).
    page_rows = [r for r in truth_evidence if (r.get("locator") or {}).get("text")]
    page_found = 0
    for r in page_rows:
        part = locator_part(r["locator"])
        text = _norm(r["locator"]["text"])
        page_found += any(text in _norm(s.text) for s in by_part.get((r["artifact_id"], *part), []))
    total = len(truth_evidence) + len(truth_mentions)
    return {
        "segments_total": len(segments),
        "evidence_total": len(truth_evidence),
        "evidence_in_segments": evidence_in,
        "evidence_coverage": _rate(evidence_in, len(truth_evidence)),
        "mentions_total": len(truth_mentions),
        "mentions_in_segments": mentions_in,
        "mentions_coverage": _rate(mentions_in, len(truth_mentions)),
        "coverage": _rate(evidence_in + mentions_in, total),
        "page_text_total": len(page_rows),
        "page_text_found": page_found,
        "page_text_coverage": _rate(page_found, len(page_rows)),
    }


# --- step 5: mentions ------------------------------------------------------------------


def _mention_matches(truth: Row, crawler: MentionRecord) -> bool:
    truth_span = _span(truth.get("locator"), truth.get("start_offset"), truth.get("end_offset"))
    crawler_span = _span(crawler.locator, crawler.start_offset, crawler.end_offset)
    if truth_span is not None and crawler_span is not None:
        return _overlaps(truth_span, crawler_span)
    return _surface_match(truth.get("surface_form"), crawler.surface_form)


@dataclass
class MentionMatches:
    """Which crawler mentions found which truth mentions (both directions)."""

    found: dict[str, list[str]]  # truth mention_id -> crawler mention_ids
    matched: dict[str, list[str]]  # crawler mention_id -> truth mention_ids


def match_mentions(
    truth_mentions: Sequence[Row], crawler_mentions: Sequence[MentionRecord]
) -> MentionMatches:
    by_part: dict[tuple, list[MentionRecord]] = defaultdict(list)
    for m in sorted(crawler_mentions, key=lambda m: m.mention_id):
        part = locator_part(m.locator)
        if part is not None:
            by_part[(m.asset_id, *part)].append(m)
    found: dict[str, list[str]] = {}
    matched: dict[str, list[str]] = defaultdict(list)
    for t in sorted(truth_mentions, key=lambda r: r["mention_id"]):
        part = locator_part(t.get("locator"))
        if part is None:
            continue
        hits = [m for m in by_part.get((t["artifact_id"], *part), []) if _mention_matches(t, m)]
        if hits:
            found[t["mention_id"]] = [m.mention_id for m in hits]
            for m in hits:
                matched[m.mention_id].append(t["mention_id"])
    return MentionMatches(found, dict(matched))


def _pr(truth_total: int, truth_found: int, crawler_total: int, crawler_matched: int) -> dict:
    precision = _rate(crawler_matched, crawler_total)
    recall = _rate(truth_found, truth_total)
    return {
        "truth_total": truth_total,
        "truth_found": truth_found,
        "recall": recall,
        "crawler_total": crawler_total,
        "crawler_matched": crawler_matched,
        "precision": precision,
        "f1": _f1(precision, recall),
    }


def score_mentions(
    truth_mentions: Sequence[Row],
    crawler_mentions: Sequence[MentionRecord],
    assets: Sequence[AssetRecord] = (),
    matches: MentionMatches | None = None,
) -> dict[str, Any]:
    """Recall of truth mentions and precision of crawler mentions, overall and by
    (entity class, difficulty tier); class agreement among found mentions.
    Precision counts entity mentions only: a mention with no proposed class
    (money, a date) is a value for joins and claims, not an entity reference,
    and is reported as ``value_mentions`` instead."""
    matches = matches or match_mentions(truth_mentions, crawler_mentions)
    crawler_by_id = {m.mention_id: m for m in crawler_mentions}
    entity_mentions = [m for m in crawler_mentions if m.proposed_class is not None]
    value_mentions = len(crawler_mentions) - len(entity_mentions)
    analyzed = {a.asset_id for a in assets if a.status in ANALYZED}

    class_tier: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"truth_total": 0, "truth_found": 0, "class_correct": 0})
    )
    class_correct = 0
    unreachable = 0
    unfound: Counter = Counter()
    for t in truth_mentions:
        cls, tier = t.get("entity_type") or "?", t.get("difficulty") or "?"
        cell = class_tier[cls][tier]
        cell["truth_total"] += 1
        if assets and t["artifact_id"] not in analyzed:
            unreachable += 1
        hits = matches.found.get(t["mention_id"])
        if hits:
            cell["truth_found"] += 1
            if any(crawler_by_id[h].proposed_class == cls for h in hits):
                cell["class_correct"] += 1
                class_correct += 1
        else:
            unfound[(cls, tier, _norm(t.get("surface_form")))] += 1

    precision_cells: dict[str, dict[str, int]] = defaultdict(
        lambda: {"crawler_total": 0, "crawler_matched": 0}
    )
    unmatched: Counter = Counter()
    for m in entity_mentions:
        hit = m.mention_id in matches.matched
        for key in (f"extractor:{m.extractor}", f"class:{m.proposed_class or '?'}"):
            precision_cells[key]["crawler_total"] += 1
            precision_cells[key]["crawler_matched"] += int(hit)
        if not hit:
            unmatched[(m.extractor, m.proposed_class or "?")] += 1

    def rollup(cells: Iterable[dict[str, int]]) -> dict[str, Any]:
        total = sum(c["truth_total"] for c in cells)
        found = sum(c["truth_found"] for c in cells)
        correct = sum(c["class_correct"] for c in cells)
        return {
            "truth_total": total,
            "truth_found": found,
            "recall": _rate(found, total),
            "class_correct": correct,
            "class_correct_rate": _rate(correct, found),
        }

    by_class_tier = {
        cls: {tier: rollup([cell]) for tier, cell in sorted(tiers.items())}
        for cls, tiers in sorted(class_tier.items())
    }
    tiers = sorted({tier for t in class_tier.values() for tier in t})
    by_tier = {
        tier: rollup([cells[tier] for cells in class_tier.values() if tier in cells])
        for tier in tiers
    }
    by_class = {cls: rollup(cells.values()) for cls, cells in sorted(class_tier.items())}
    matched_entity = sum(1 for m in entity_mentions if m.mention_id in matches.matched)
    overall = _pr(len(truth_mentions), len(matches.found), len(entity_mentions), matched_entity)
    return {
        **overall,
        "value_mentions": value_mentions,
        "class_correct": class_correct,
        "class_correct_rate": _rate(class_correct, len(matches.found)),
        "truth_in_unanalyzed_assets": unreachable,
        "by_class_tier": by_class_tier,
        "by_tier": by_tier,
        "by_class": by_class,
        "precision_by": {
            key: {**cell, "precision": _rate(cell["crawler_matched"], cell["crawler_total"])}
            for key, cell in sorted(precision_cells.items())
        },
        "diagnostics": {
            "unmatched_crawler_mentions": [
                {"extractor": e, "class": c, "count": n}
                for (e, c), n in sorted(unmatched.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "unfound_truth_surfaces": [
                {"class": c, "tier": t, "surface": s, "count": n}
                for (c, t, s), n in sorted(unfound.items(), key=lambda kv: (-kv[1], kv[0]))[
                    :TOP_DIAGNOSTICS
                ]
            ],
        },
    }


# --- steps 6-10: resolution --------------------------------------------------------


def _entity_keys(entities: Iterable[EntityRecord]) -> dict[str, set[str]]:
    keys: dict[str, set[str]] = defaultdict(set)
    for e in entities:
        keys[e.entity_id].update(e.external_ids)
    return keys


def score_resolution(
    truth_mentions: Sequence[Row],
    truth_entities: Sequence[Row],
    mentions: Sequence[MentionRecord],
    links: Sequence[EntityLinkRecord],
    entities: Sequence[EntityRecord],
    matches: MentionMatches | None = None,
) -> dict[str, Any]:
    """Among found truth mentions: is a SameAs link's entity the truth entity
    (by external ID)? Accuracy by tier and by resolver; SameAs precision;
    PossiblySameAs and unresolved counts."""
    matches = matches or match_mentions(truth_mentions, mentions)
    truth_key_of = {e["entity_id"]: truth_key(e) for e in truth_entities}
    keys_of = _entity_keys(entities)
    links_by_mention: dict[str, list[EntityLinkRecord]] = defaultdict(list)
    for link in sorted(links, key=lambda l: l.link_id):
        links_by_mention[link.mention_id].append(link)

    def cell() -> dict[str, int]:
        return {"found": 0, "correct": 0, "wrong": 0, "possibly_only": 0, "unresolved": 0}

    by_tier: dict[str, dict[str, int]] = defaultdict(cell)
    by_class: dict[str, dict[str, int]] = defaultdict(cell)
    by_resolver: dict[str, dict[str, int]] = defaultdict(lambda: {"links": 0, "correct": 0})
    for t in truth_mentions:
        hits = matches.found.get(t["mention_id"])
        if not hits:
            continue
        expected = truth_key_of.get(t["entity_id"])
        same_as = [l for h in hits for l in links_by_mention.get(h, []) if l.link_type == "SameAs"]
        possibly = [
            l for h in hits for l in links_by_mention.get(h, []) if l.link_type == "PossiblySameAs"
        ]
        correct = [l for l in same_as if expected in keys_of.get(l.entity_id, set())]
        for cells in (by_tier[t.get("difficulty") or "?"], by_class[t.get("entity_type") or "?"]):
            cells["found"] += 1
            if correct:
                cells["correct"] += 1
            elif same_as:
                cells["wrong"] += 1
            elif possibly:
                cells["possibly_only"] += 1
            else:
                cells["unresolved"] += 1
        for l in same_as:
            by_resolver[l.resolved_by]["links"] += 1
            by_resolver[l.resolved_by]["correct"] += int(l in correct)

    # SameAs precision over every definite link of a crawler mention.
    truth_by_id = {t["mention_id"]: t for t in truth_mentions}
    sameas_correct = sameas_wrong = sameas_unverifiable = 0
    for link in links:
        if link.link_type != "SameAs":
            continue
        truth_ids = matches.matched.get(link.mention_id)
        if not truth_ids:
            sameas_unverifiable += 1
            continue
        expected_keys = {truth_key_of.get(truth_by_id[i]["entity_id"]) for i in truth_ids}
        if expected_keys & keys_of.get(link.entity_id, set()):
            sameas_correct += 1
        else:
            sameas_wrong += 1

    def finish(cells: dict[str, int]) -> dict[str, Any]:
        return {**cells, "accuracy": _rate(cells["correct"], cells["found"])}

    found_total = sum(c["found"] for c in by_tier.values())
    correct_total = sum(c["correct"] for c in by_tier.values())
    return {
        "found": found_total,
        "correct": correct_total,
        "accuracy": _rate(correct_total, found_total),
        "by_tier": {tier: finish(c) for tier, c in sorted(by_tier.items())},
        "by_class": {cls: finish(c) for cls, c in sorted(by_class.items())},
        "by_resolver": {
            name: {**c, "accuracy": _rate(c["correct"], c["links"])}
            for name, c in sorted(by_resolver.items())
        },
        "sameas_links": sum(1 for l in links if l.link_type == "SameAs"),
        "sameas_correct": sameas_correct,
        "sameas_wrong": sameas_wrong,
        "sameas_unverifiable": sameas_unverifiable,
        "sameas_precision": _rate(sameas_correct, sameas_correct + sameas_wrong),
        "possibly_sameas_links": sum(1 for l in links if l.link_type == "PossiblySameAs"),
    }


# --- step 7: cases ---------------------------------------------------------------------


def _pairs(groups: dict[str, set[str]]) -> set[tuple[str, str]]:
    """Unordered pairs of members sharing a group, under union-find over groups."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    members: dict[str, set[str]] = defaultdict(set)
    for asset, keys in groups.items():
        for key in keys:
            members[key].add(asset)
    for assets in members.values():
        ordered = sorted(assets)
        for other in ordered[1:]:
            parent[find(other)] = find(ordered[0])
    clusters: dict[str, list[str]] = defaultdict(list)
    for asset in groups:
        clusters[find(asset)].append(asset)
    pairs = set()
    for cluster in clusters.values():
        cluster.sort()
        for i, a in enumerate(cluster):
            for b in cluster[i + 1 :]:
                pairs.add((a, b))
    return pairs


def score_cases(
    truth_mentions: Sequence[Row],
    relationships: Sequence[RelationshipRecord],
    mentions: Sequence[MentionRecord],
    links: Sequence[EntityLinkRecord],
    entities: Sequence[EntityRecord],
) -> dict[str, Any]:
    """Pairwise precision/recall of assets grouped into one case: crawler groups =
    assets sharing an About target (else any SameAs Return entity); truth groups =
    the hidden scenario of each artifact."""
    about: dict[str, set[str]] = defaultdict(set)
    for r in relationships:
        if r.relationship_type == "About" and r.source_kind == "asset":
            about[r.source_id].add(r.target_id)
    grouping = "About"
    if not about:
        grouping = "SameAs Return"
        returns = {e.entity_id for e in entities if e.ontology_class == "Return"}
        asset_of = {m.mention_id: m.asset_id for m in mentions}
        for l in links:
            if l.link_type == "SameAs" and l.entity_id in returns and l.mention_id in asset_of:
                about[asset_of[l.mention_id]].add(l.entity_id)
    if not about:
        return {"skipped": True, "reason": "no About relationships or SameAs Return links"}
    truth_groups: dict[str, set[str]] = defaultdict(set)
    for t in truth_mentions:
        if t.get("scenario_id"):
            truth_groups[t["artifact_id"]].add(t["scenario_id"])
    crawler_pairs = _pairs(about)
    truth_pairs = _pairs(truth_groups)
    hit = len(crawler_pairs & truth_pairs)
    precision, recall = _rate(hit, len(crawler_pairs)), _rate(hit, len(truth_pairs))
    return {
        "skipped": False,
        "grouping": grouping,
        "assets_grouped": len(about),
        "crawler_pairs": len(crawler_pairs),
        "truth_pairs": len(truth_pairs),
        "pairs_correct": hit,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
    }


# --- step 11: relationships ---------------------------------------------------------


def _crawler_endpoint_keys(kind: str, id_: str, keys_of: dict[str, set[str]]) -> set[str]:
    return {id_} if kind == "asset" else keys_of.get(id_, set())


def score_relationships(
    truth_relationships: Sequence[Row],
    truth_entities: Sequence[Row],
    relationships: Sequence[RelationshipRecord],
    entities: Sequence[EntityRecord],
) -> dict[str, Any]:
    """Precision/recall per ontology relationship class over (type, source key,
    target key) triples; truth predicates mapped with RELATIONSHIP_MAP."""
    truth_key_of = {e["entity_id"]: truth_key(e) for e in truth_entities}
    keys_of = _entity_keys(entities)
    truth_triples: dict[tuple[str, str, str], str] = {}  # triple -> truth predicate
    unmapped_truth: Counter = Counter()
    for r in truth_relationships:
        mapped = RELATIONSHIP_MAP.get(r["predicate"])
        if mapped is None:
            unmapped_truth[r["predicate"]] += 1
            continue
        rtype, swap = mapped
        source, target = truth_key_of.get(r["source_entity_id"]), truth_key_of.get(r["target_entity_id"])
        if source is None or target is None:
            continue
        if swap:
            source, target = target, source
        truth_triples[(rtype, source, target)] = r["predicate"]
    crawler_triples: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for r in relationships:
        for s in _crawler_endpoint_keys(r.source_kind, r.source_id, keys_of):
            for t in _crawler_endpoint_keys(r.target_kind, r.target_id, keys_of):
                crawler_triples[r.relationship_id].add((r.relationship_type, s, t))
    crawler_set = {triple for triples in crawler_triples.values() for triple in triples}
    by_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"truth_total": 0, "truth_found": 0, "crawler_total": 0, "crawler_matched": 0}
    )
    by_predicate: dict[str, dict[str, int]] = defaultdict(lambda: {"truth_total": 0, "truth_found": 0})
    for triple, predicate in truth_triples.items():
        hit = triple in crawler_set
        by_type[triple[0]]["truth_total"] += 1
        by_type[triple[0]]["truth_found"] += int(hit)
        by_predicate[predicate]["truth_total"] += 1
        by_predicate[predicate]["truth_found"] += int(hit)
    for r in relationships:
        hit = any(triple in truth_triples for triple in crawler_triples[r.relationship_id])
        by_type[r.relationship_type]["crawler_total"] += 1
        by_type[r.relationship_type]["crawler_matched"] += int(hit)
    totals = _pr(
        sum(c["truth_total"] for c in by_type.values()),
        sum(c["truth_found"] for c in by_type.values()),
        sum(c["crawler_total"] for c in by_type.values()),
        sum(c["crawler_matched"] for c in by_type.values()),
    )
    return {
        **totals,
        "by_type": {t: _pr(**c) for t, c in sorted(by_type.items())},
        "by_predicate": {
            p: {**c, "recall": _rate(c["truth_found"], c["truth_total"])}
            for p, c in sorted(by_predicate.items())
        },
        "unmapped_truth_predicates": dict(sorted(unmapped_truth.items())),
    }


# --- step 12: claims and evidence -------------------------------------------------


def match_claims(
    truth_claims: Sequence[Row],
    truth_entities: Sequence[Row],
    claims: Sequence[ClaimRecord],
    entities: Sequence[EntityRecord],
) -> dict[str, list[str]]:
    """truth claim_id -> crawler claim_ids asserting the same (predicate, subject,
    object) with the object an entity key or a value."""
    truth_key_of = {e["entity_id"]: truth_key(e) for e in truth_entities}
    keys_of = _entity_keys(entities)
    crawler_index: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for c in sorted(claims, key=lambda c: c.claim_id):
        objects = (
            keys_of.get(c.object_entity_id, set())
            if c.object_entity_id
            else {_norm(c.object_value)}
        )
        for s in keys_of.get(c.subject_entity_id, set()):
            for o in objects:
                crawler_index[(c.predicate, s, o)].append(c.claim_id)
    matches: dict[str, list[str]] = {}
    for t in truth_claims:
        subject = truth_key_of.get(t["subject"])
        obj = truth_key_of.get(t["obj"], _norm(t.get("obj")))
        if subject is None:
            continue
        hits = crawler_index.get((t["claim_type"], subject, obj))
        if hits:
            matches[t["claim_id"]] = list(hits)
    return matches


def score_claims(
    truth_claims: Sequence[Row],
    truth_entities: Sequence[Row],
    claims: Sequence[ClaimRecord],
    entities: Sequence[EntityRecord],
    matches: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Per-predicate precision/recall on (predicate, subject key, object key or value)."""
    matches = match_claims(truth_claims, truth_entities, claims, entities) if matches is None else matches
    matched_crawler = {c for hits in matches.values() for c in hits}
    cells: dict[str, dict[str, int]] = defaultdict(
        lambda: {"truth_total": 0, "truth_found": 0, "crawler_total": 0, "crawler_matched": 0}
    )
    for t in truth_claims:
        cells[t["claim_type"]]["truth_total"] += 1
        cells[t["claim_type"]]["truth_found"] += int(t["claim_id"] in matches)
    for c in claims:
        cells[c.predicate]["crawler_total"] += 1
        cells[c.predicate]["crawler_matched"] += int(c.claim_id in matched_crawler)
    totals = _pr(
        len(truth_claims), len(matches), len(claims), len(matched_crawler)
    )
    return {**totals, "by_predicate": {p: _pr(**c) for p, c in sorted(cells.items())}}


def _evidence_agrees(truth: Row, crawler: ClaimEvidenceRecord) -> bool:
    if truth["artifact_id"] != crawler.asset_id:
        return False
    if locator_part(truth.get("locator")) != locator_part(crawler.locator):
        return False
    truth_span = _span(truth.get("locator"), truth.get("start_offset"), truth.get("end_offset"))
    crawler_span = _span(crawler.locator)
    if truth_span is not None and crawler_span is not None:
        return _overlaps(truth_span, crawler_span)
    truth_text = (truth.get("locator") or {}).get("text") or truth.get("excerpt")
    crawler_text = crawler.locator.get("text") or crawler.excerpt
    return _surface_match(truth_text, crawler_text)


def score_evidence(
    truth_evidence: Sequence[Row],
    claim_matches: dict[str, list[str]],
    claim_evidence: Sequence[ClaimEvidenceRecord],
) -> dict[str, Any]:
    """Evidence locator agreement: of the matched truth claims, how many have a
    crawler evidence row in the same asset and part overlapping a truth span (or
    naming the same page text)."""
    truth_by_claim: dict[str, list[Row]] = defaultdict(list)
    for e in truth_evidence:
        truth_by_claim[e["claim_id"]].append(e)
    crawler_by_claim: dict[str, list[ClaimEvidenceRecord]] = defaultdict(list)
    for e in claim_evidence:
        crawler_by_claim[e.claim_id].append(e)
    agreeing = 0
    rows_matched = 0
    rows_total = 0
    matched_evidence: set[str] = set()
    for truth_claim, crawler_claims in sorted(claim_matches.items()):
        rows = truth_by_claim.get(truth_claim, [])
        crawler_rows = [e for c in crawler_claims for e in crawler_by_claim.get(c, [])]
        hit_any = False
        for t in rows:
            rows_total += 1
            if any(_evidence_agrees(t, c) for c in crawler_rows):
                rows_matched += 1
                matched_evidence.add(t["evidence_id"])
                hit_any = True
        agreeing += int(hit_any)
    return {
        "claims_matched": len(claim_matches),
        "claims_with_agreeing_evidence": agreeing,
        "agreement": _rate(agreeing, len(claim_matches)),
        "evidence_rows_of_matched_claims": rows_total,
        "evidence_rows_matched": rows_matched,
        "evidence_row_agreement": _rate(rows_matched, rows_total),
        "truth_evidence_total": len(truth_evidence),
        "crawler_evidence_total": len(claim_evidence),
        "matched_evidence_ids": sorted(matched_evidence),
    }


# --- golden questions ---------------------------------------------------------------


def score_questions(
    expected_queries: Sequence[Row],
    truth_entities: Sequence[Row],
    truth_evidence: Sequence[Row],
    rows: IndexRows,
    claim_matches: dict[str, list[str]],
    matched_evidence: Iterable[str] = (),
) -> dict[str, Any]:
    """Retrieval-level checks per golden question: required entities linked,
    artifacts analyzed, claims found, evidence inside segments (and, once the
    crawler produces claims, matched by claim evidence). A no_answer question
    passes only when its entity is linked to no document."""
    truth_key_of = {e["entity_id"]: truth_key(e) for e in truth_entities}
    keys_of = _entity_keys(rows.entities)
    linked_keys = {
        k for l in rows.links if l.link_type == "SameAs" for k in keys_of.get(l.entity_id, set())
    }
    mentioned_keys = {
        k
        for r in rows.relationships
        if r.relationship_type == "Mentions" and r.target_kind == "entity"
        for k in keys_of.get(r.target_id, set())
    }
    analyzed = {a.asset_id for a in rows.assets if a.status in ANALYZED}
    by_part = _segments_by_part(rows.segments)
    evidence_by_id = {e["evidence_id"]: e for e in truth_evidence}
    matched_evidence = set(matched_evidence)
    crawler_has_claims = bool(rows.claim_evidence)

    results = []
    for q in sorted(expected_queries, key=lambda r: r["query_id"]):
        kind = q.get("kind") or "?"
        checks: dict[str, bool] = {}
        if kind == "no_answer":
            structured = q.get("required_structured") or {}
            key = structured.get("key") or {}
            table = structured.get("table") or "item"
            external = ids.external_id(SOURCE, table, key) if key else None
            checks["no_links"] = external is not None and external not in linked_keys
            checks["no_mentions"] = external is not None and external not in mentioned_keys
        else:
            required_entities = [
                truth_key_of[e] for e in q.get("required_entities") or [] if e in truth_key_of
            ]
            # Artifact entities are asset IDs (no "<source>.<object>:" prefix).
            checks["entities_linked"] = all(
                (k in analyzed) if "." not in k else (k in linked_keys) for k in required_entities
            )
            checks["artifacts_indexed"] = all(
                a in analyzed for a in q.get("required_artifacts") or []
            )
            checks["claims_found"] = all(c in claim_matches for c in q.get("required_claims") or [])
            required_evidence = [
                evidence_by_id[e] for e in q.get("required_evidence") or [] if e in evidence_by_id
            ]
            segmented = all(
                _inside_segment(
                    by_part, e["artifact_id"], e.get("locator"), e.get("start_offset"), e.get("end_offset")
                )
                for e in required_evidence
            )
            checks["evidence_segmented"] = segmented
            checks["evidence_indexed"] = segmented and (
                not crawler_has_claims
                or all(e["evidence_id"] in matched_evidence for e in required_evidence)
            )
        results.append(
            {
                "query_id": q["query_id"],
                "kind": kind,
                "difficulty": q.get("difficulty"),
                "passed": all(checks.values()),
                "checks": checks,
            }
        )
    by_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    by_check: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    for r in results:
        by_kind[r["kind"]]["total"] += 1
        by_kind[r["kind"]]["passed"] += int(r["passed"])
        for check, ok in r["checks"].items():
            by_check[check]["total"] += 1
            by_check[check]["passed"] += int(ok)
    passed = sum(r["passed"] for r in results)
    return {
        "total": len(results),
        "passed": passed,
        "pass_rate": _rate(passed, len(results)),
        "by_kind": {
            k: {**c, "pass_rate": _rate(c["passed"], c["total"])} for k, c in sorted(by_kind.items())
        },
        "by_check": {
            k: {**c, "pass_rate": _rate(c["passed"], c["total"])} for k, c in sorted(by_check.items())
        },
        "results": results,
    }


# --- the whole scorecard --------------------------------------------------------------


def score(truth: Truth, rows: IndexRows, run: CrawlRunRecord | None = None) -> dict[str, Any]:
    """Every metric for one run against one dataset. Deterministic for the same input."""
    mention_matches = match_mentions(truth.entity_mentions, rows.mentions)
    mentions = score_mentions(truth.entity_mentions, rows.mentions, rows.assets, mention_matches)
    diagnostics = {"mentions": mentions.pop("diagnostics")}
    claim_matches = match_claims(truth.claims, truth.entities, rows.claims, rows.entities)
    evidence = score_evidence(truth.evidence, claim_matches, rows.claim_evidence)
    matched_evidence = evidence.pop("matched_evidence_ids")
    return {
        "run": {
            "crawl_run_id": run.crawl_run_id if run else None,
            "strategy": _strategy(run),
            "crawler_version": run.crawler_version if run else None,
            "settings_version": run.settings_version if run else None,
            "counts": dict(sorted((run.counts if run else {}).items())),
            "assets_analyzed": sum(1 for a in rows.assets if a.status in ANALYZED),
            "truth": {
                "entities": len(truth.entities),
                "mentions": len(truth.entity_mentions),
                "relationships": len(truth.relationships),
                "claims": len(truth.claims),
                "evidence": len(truth.evidence),
                "questions": len(truth.expected_queries),
            },
        },
        "segments": score_segments(truth.evidence, truth.entity_mentions, rows.segments),
        "mentions": mentions,
        "resolution": score_resolution(
            truth.entity_mentions, truth.entities, rows.mentions, rows.links, rows.entities,
            mention_matches,
        ),
        "cases": score_cases(
            truth.entity_mentions, rows.relationships, rows.mentions, rows.links, rows.entities
        ),
        "relationships": score_relationships(
            truth.relationships, truth.entities, rows.relationships, rows.entities
        ),
        "claims": score_claims(truth.claims, truth.entities, rows.claims, rows.entities, claim_matches),
        "evidence": evidence,
        "questions": score_questions(
            truth.expected_queries, truth.entities, truth.evidence, rows, claim_matches,
            matched_evidence,
        ),
        "diagnostics": diagnostics,
    }


def summarize(metrics: dict[str, Any]) -> dict[str, Any]:
    """The headline numbers the CR-4/5/6 gates use."""
    tiers = metrics["mentions"]["by_tier"]
    return {
        "segments_coverage": metrics["segments"]["coverage"],
        "mention_recall_direct": tiers.get("direct", {}).get("recall"),
        "mention_recall_alias": tiers.get("alias", {}).get("recall"),
        "mention_recall_contextual": tiers.get("contextual", {}).get("recall"),
        "mention_precision": metrics["mentions"]["precision"],
        "resolution_accuracy_alias": metrics["resolution"]["by_tier"].get("alias", {}).get("accuracy"),
        "sameas_precision": metrics["resolution"]["sameas_precision"],
        "claim_precision": metrics["claims"]["precision"],
        "claim_recall": metrics["claims"]["recall"],
        "evidence_agreement": metrics["evidence"]["agreement"],
        "questions_pass_rate": metrics["questions"]["pass_rate"],
    }


def _strategy(run: CrawlRunRecord | None) -> str:
    if run is None:
        return "deterministic"
    return str(run.settings.get("strategy") or "deterministic")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def find_run(index: IndexStore, crawl_run_id: str) -> CrawlRunRecord:
    run = next((r for r in runs.runs(index) if r.crawl_run_id == crawl_run_id), None)
    if run is None:
        raise UnknownRun(crawl_run_id)
    return run


def evaluate(
    index: IndexStore,
    truth_cursor_factory: Callable[[], Any],
    crawl_run_id: str,
    dataset_id: str,
    *,
    evaluator: str,
    evaluator_mode: str,
    clock: Callable[[], str] = _now,
) -> EvaluationRecord:
    """Score ``crawl_run_id`` (read from ``index``) against ``dataset_id`` (read from
    helios_ground_truth through ``truth_cursor_factory``, called once) and append
    the EvaluationRecord to ``index``. A Ranger denial raises GroundTruthDenied
    (nothing is written); any other failure writes a FAILED record and raises
    EvaluationFailed."""
    run = find_run(index, crawl_run_id)
    base = {
        "evaluation_id": ids.evaluation_id(crawl_run_id, dataset_id, HARNESS_VERSION),
        "crawl_run_id": crawl_run_id,
        "dataset_id": dataset_id,
        "evaluator": evaluator,
        "evaluator_mode": evaluator_mode,
        "harness_version": HARNESS_VERSION,
        "ontology_version": run.ontology_version,
        "strategy": _strategy(run),
    }
    try:
        truth = load_truth(truth_cursor_factory, dataset_id)
        rows = load_index(index, crawl_run_id)
        metrics = score(truth, rows, run)
        record = EvaluationRecord(
            **base,
            evaluated_at=clock(),
            metrics=metrics,
            summary=summarize(metrics),
            status="SUCCEEDED",
        )
    except GroundTruthDenied:
        raise
    except Exception as exc:
        record = EvaluationRecord(
            **base,
            evaluated_at=clock(),
            status="FAILED",
            error=f"{type(exc).__name__}: {exc}"[:4000],
        )
        index.append(EVALUATIONS, [record])
        raise EvaluationFailed(record) from exc
    index.append(EVALUATIONS, [record])
    return record


def evaluations(
    index: IndexStore, *, crawl_run_id: str | None = None, dataset_id: str | None = None
) -> list[EvaluationRecord]:
    """The latest evaluation of each (run, dataset), newest first."""
    where: dict[str, Any] = {}
    if crawl_run_id:
        where["crawl_run_id"] = crawl_run_id
    if dataset_id:
        where["dataset_id"] = dataset_id
    latest: dict[tuple[str, str], EvaluationRecord] = {}
    for record in index.read(EVALUATIONS, where or None):
        assert isinstance(record, EvaluationRecord)
        key = (record.crawl_run_id, record.dataset_id)
        if key not in latest or record.evaluated_at >= latest[key].evaluated_at:
            latest[key] = record
    return sorted(latest.values(), key=lambda r: r.evaluated_at, reverse=True)


def summary_table(record: EvaluationRecord) -> str:
    """The summary as an aligned text table (for the CLI)."""
    width = max(len(k) for k in record.summary) if record.summary else 10
    lines = [f"{record.evaluation_id}  {record.status}  ({record.evaluator}, {record.evaluator_mode})"]
    for key, value in record.summary.items():
        shown = "n/a" if value is None else f"{value:.4f}"
        lines.append(f"  {key.ljust(width)}  {shown}")
    if record.error:
        lines.append(f"  error: {record.error}")
    return "\n".join(lines)
