"""Step 12 of the analysis (docs/crawler-analysis.md): extract claims.

A claim is a predicate from the retail pack's vocabulary (``PACKAGING_DAMAGED``,
``RETURN_REASON``, ``REFUND_REQUESTED``, ``REFUND_APPROVED``) asserted between
two entities the resolution step identified. Nothing here looks at the
warehouse or the answer key: the inputs are the run's segments, mentions, links,
entities, relationships and case clusters.

**Units.** A chat message is one unit; an email body, a PDF page or a text file
is split into sentences (``.``, ``?``, ``!`` followed by whitespace, and line
breaks, so a labelled report line such as ``Reason code on file: Package was
damaged.`` is one unit). Offsets are exact: ``segment.text[start:end]`` is the
unit, which becomes the evidence excerpt and locator.

**Cues.** Each predicate has a cue lexicon in the crawler settings
(``claims.cues``), matched case-insensitively on word boundaries; ``*`` in a
phrase stands for up to four words (``refund * approved``). A cue is blocked
by a negation within the three words before it (``not damaged``, ``no damage``)
or a hedge earlier in the same clause (``if it was approved``); clauses are
split on ``.;,:`` so a negation in one clause does not block a cue in the
next (``Damage is on the carrier, not the customer. Approve it.``).
Confidence follows cue strength: a multi-word phrase or a labelled field
(``Disposition:``) is strong, a single domain word is medium, and a generic
word (``damage``, ``refund``) is weak.

**Subjects and objects** come from resolution, per the predicate's shape:
``PACKAGING_DAMAGED`` Item -> Return, ``RETURN_REASON`` Return -> Reason,
``REFUND_REQUESTED`` Customer -> Return, ``REFUND_APPROVED`` Return -> Customer.
The case's Return is the cluster's About target; its Item, Customer and Reason
are the structural edges of the identified warehouse row (Contains, PartyTo,
HasReason), else the one SameAs entity of that class in the asset or cluster.
A Reason named in the unit itself wins over the row's. Who is speaking matters
for refunds: a customer (the email's author, a chat participant with the
customer role) asks, staff (agents, supervisors, inspectors, the returns desk
report) approve, so a customer's unit never yields ``REFUND_APPROVED`` and an
unknown speaker's (an email whose author did not resolve) does so only on a
strong cue.
A weak cue (a generic word such as ``damaged``) counts only when the unit
itself names the claim's subject (``the item was broken``), so a recorded
reason reading ``Package was damaged`` is not damage evidence; the case
supplies the object.

**One claim per (predicate, subject, object) per case**, with one evidence row
per triggering unit across the case's documents; the claim's confidence is the
best of its evidence. A cue with no resolvable subject or object yields nothing
(precision over recall) and is counted as unanchored.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from helios_core.crawler.settings import CrawlerSettings
from helios_core.index import ids
from helios_core.index.records import (
    AssetRecord,
    ClaimEvidenceRecord,
    ClaimRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    EntityRecord,
    MentionRecord,
    RelationshipRecord,
    SegmentRecord,
)

EXTRACTOR = "cues"
CONFIDENCE = {"strong": 0.95, "medium": 0.85, "weak": 0.7}
GENERIC_WORDS = {
    "damage",
    "damaged",
    "receive",
    "refund",
    "reason",
    "approved",
    "approval",
    "broken",
}
NEGATION_WINDOW = 3  # words before the cue a negation reaches over
GAP_WORDS = 4  # words a "*" in a cue phrase may stand for
SENTENCE_SEGMENTS = {"email_body", "page", "text", "row_column"}
SKIPPED_SEGMENTS = {"email_header", "email_subject"}
ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "no",
    "st",
    "vs",
    "etc",
    "jr",
    "sr",
    "inc",
    "ltd",
    "approx",
}
CUSTOMER_ROLES = {"customer", "buyer", "shopper"}
STAFF_SEGMENTS = {"page"}  # a returns-desk report speaks for the store

# Predicate -> (subject class, object class); the shapes the retail pack declares.
SHAPES: dict[str, tuple[str, str]] = {
    "PACKAGING_DAMAGED": ("Item", "Return"),
    "RETURN_REASON": ("Return", "Reason"),
    "REFUND_REQUESTED": ("Customer", "Return"),
    "REFUND_APPROVED": ("Return", "Customer"),
}
# Predicates a customer's own words cannot assert.
STAFF_ONLY = {"REFUND_APPROVED"}
# Structural edge of the Return entity that names each class (resolution step 11).
STRUCTURAL_EDGES = {"Item": "Contains", "Customer": "PartyTo", "Reason": "HasReason"}
# Classes whose mention inside the unit wins over the case's entity.
UNIT_FIRST = {"Reason"}

_WORD = re.compile(r"[\w'’]+")
_CLAUSE_BREAK = re.compile(r"[.;,:?!\n]")
_SENTENCE_END = re.compile(r"[.?!]+(?=\s)|\n")


# --- units ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    segment: SegmentRecord
    start: int
    end: int

    @property
    def text(self) -> str:
        return self.segment.text[self.start : self.end]


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _abbreviated(text: str, at: int) -> bool:
    """Whether the period at ``at`` ends an abbreviation (Mrs., Arjun V.) rather than a sentence."""
    before = text[:at]
    match = re.search(r"(\w+)$", before)
    if match is None:
        return False
    word = match.group(1)
    return word.lower() in ABBREVIATIONS or (len(word) == 1 and word.isupper())


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence, whitespace trimmed, so text[start:end] is the sentence."""
    spans = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.group() != "\n" and _abbreviated(text, match.start()):
            continue
        end = match.start() if match.group() == "\n" else match.end()
        spans.append(_trimmed(text, start, end))
        start = match.end()
    spans.append(_trimmed(text, start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def split_units(segment: SegmentRecord) -> list[Unit]:
    """The claim units of one segment: the message, or the sentences."""
    if segment.segment_type in SKIPPED_SEGMENTS:
        return []
    if segment.segment_type in SENTENCE_SEGMENTS:
        return [Unit(segment, s, e) for s, e in sentence_spans(segment.text)]
    start, end = _trimmed(segment.text, 0, len(segment.text))
    return [Unit(segment, start, end)] if end > start else []


# --- cues -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    predicate: str
    phrase: str
    pattern: re.Pattern[str]
    strength: str  # strong, medium or weak


def cue_strength(phrase: str) -> str:
    words = phrase.split()
    if len(words) >= 3 or phrase.endswith(":") or "*" in words:
        return "strong"
    if len(words) == 2:
        return "medium"
    return "weak" if phrase.lower() in GENERIC_WORDS else "medium"


def compile_cue(phrase: str) -> re.Pattern[str]:
    """A whole-word, case-insensitive match of the phrase; ``*`` spans up to
    ``GAP_WORDS`` words."""
    body = ""
    for i, word in enumerate(phrase.split()):
        if word == "*":
            body += rf"(?:\s+\S+){{0,{GAP_WORDS}}}"
        else:
            body += (r"\s+" if i else "") + re.escape(word)
    head = r"(?<!\w)" if phrase[:1].isalnum() else ""
    tail = r"(?!\w)" if phrase[-1:].isalnum() else ""
    return re.compile(head + body + tail, re.IGNORECASE)


def compile_cues(settings: CrawlerSettings) -> list[Cue]:
    cues = []
    for predicate in sorted(settings.claims.cues):
        for phrase in settings.claims.cues[predicate]:
            phrase = phrase.strip()
            if phrase:
                cues.append(Cue(predicate, phrase, compile_cue(phrase), cue_strength(phrase)))
    return cues


@dataclass(frozen=True)
class Hit:
    cue: Cue
    start: int  # within the unit
    end: int


def _clause_before(text: str, at: int) -> str:
    """The text from the last clause break before ``at`` up to ``at``."""
    breaks = [m.end() for m in _CLAUSE_BREAK.finditer(text, 0, at)]
    return text[breaks[-1] if breaks else 0 : at]


def _words(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD.findall(text)]


def blocked(
    text: str, start: int, negations: Iterable[str], hedges: Iterable[str], end: int | None = None
) -> bool:
    """A negation in the last words before the cue (or inside a gapped cue,
    ``refund wasn't approved``), or a hedge anywhere earlier in the cue's
    clause, blocks it."""
    before = _words(_clause_before(text, start))
    inside = _words(text[start:end]) if end is not None else []
    negations = {n.lower() for n in negations}
    hedges = {h.lower() for h in hedges}
    if any(w in negations for w in [*before[-NEGATION_WINDOW:], *inside]):
        return True
    return any(w in hedges for w in [*before, *inside])


def find_hits(text: str, cues: Sequence[Cue], settings: CrawlerSettings) -> dict[str, Hit]:
    """predicate -> its strongest unblocked cue hit in the unit."""
    best: dict[str, Hit] = {}
    rank = {"strong": 2, "medium": 1, "weak": 0}
    for cue in cues:
        for match in cue.pattern.finditer(text):
            if blocked(
                text,
                match.start(),
                settings.claims.negations,
                settings.claims.hedges,
                match.end(),
            ):
                continue
            hit = Hit(cue, match.start(), match.end())
            current = best.get(cue.predicate)
            if current is None or rank[cue.strength] > rank[current.cue.strength]:
                best[cue.predicate] = hit
            break  # one match per cue is enough
    return best


# --- who the documents name ---------------------------------------------------------------


class Context:
    """What resolution found, indexed for the claim shapes."""

    def __init__(
        self,
        clusters: dict[str, list[str]],
        segments: Sequence[SegmentRecord],
        mentions: Sequence[MentionRecord],
        links: Sequence[EntityLinkRecord],
        entities: Sequence[EntityRecord],
        relationships: Sequence[RelationshipRecord],
    ):
        self.class_of = {e.entity_id: e.ontology_class for e in entities}
        self.cluster_of = {a: c for c, members in clusters.items() for a in members}
        mention_by_id = {m.mention_id: m for m in mentions}
        # SameAs links per segment, with the mention's span.
        self.linked: dict[str, list[tuple[MentionRecord, str]]] = defaultdict(list)
        self.in_asset: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        self.in_cluster: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        header_segments = {
            s.segment_id
            for s in segments
            if s.segment_type == "email_header"
            and str(s.locator.get("header", "")).lower() == "from"
        }
        authors: dict[str, set[str]] = defaultdict(set)
        for link in sorted(links, key=lambda l: l.link_id):
            if link.link_type != "SameAs":
                continue
            mention = mention_by_id.get(link.mention_id)
            class_name = self.class_of.get(link.entity_id)
            if mention is None or class_name is None:
                continue
            self.linked[mention.segment_id].append((mention, link.entity_id))
            self.in_asset[mention.asset_id][class_name].add(link.entity_id)
            self.in_cluster[self.cluster_of.get(mention.asset_id, "")][class_name].add(
                link.entity_id
            )
            if mention.segment_id in header_segments and class_name == "Customer":
                authors[mention.asset_id].add(link.entity_id)
        self.author = {a: next(iter(found)) for a, found in authors.items() if len(found) == 1}
        self.about: dict[str, str] = {}
        self.edges: dict[str, dict[str, str]] = defaultdict(
            dict
        )  # return entity -> class -> entity
        for r in sorted(relationships, key=lambda r: r.relationship_id):
            if r.relationship_type == "About" and r.source_kind == "asset":
                self.about[r.source_id] = r.target_id
            elif (
                r.source_kind == "entity"
                and r.target_kind == "entity"
                and self.class_of.get(r.source_id) == "Return"
            ):
                target_class = self.class_of.get(r.target_id)
                if target_class and STRUCTURAL_EDGES.get(target_class) == r.relationship_type:
                    self.edges[r.source_id].setdefault(target_class, r.target_id)
        self.case_return: dict[str, str] = {}
        for cluster_id, members in clusters.items():
            targets = {
                self.about[a]
                for a in members
                if a in self.about and self.class_of.get(self.about[a]) == "Return"
            }
            if len(targets) == 1:
                self.case_return[cluster_id] = next(iter(targets))
            else:
                returns = self.in_cluster[cluster_id].get("Return", set())
                if len(returns) == 1:
                    self.case_return[cluster_id] = next(iter(returns))

    def in_unit(self, unit: Unit, class_name: str) -> str | None:
        """The one entity of ``class_name`` a SameAs mention inside the unit names."""
        found = {
            entity
            for mention, entity in self.linked.get(unit.segment.segment_id, [])
            if self.class_of.get(entity) == class_name
            and mention.start_offset is not None
            and mention.end_offset is not None
            and unit.start <= mention.start_offset
            and mention.end_offset <= unit.end
        }
        return next(iter(found)) if len(found) == 1 else None

    def _single(self, scope: dict[str, set[str]], class_name: str) -> str | None:
        found = scope.get(class_name, set())
        return next(iter(found)) if len(found) == 1 else None

    def entity_for(self, unit: Unit, class_name: str) -> str | None:
        """The entity of ``class_name`` a unit's claim is about: the case's,
        unless the unit itself names one (always, for a Reason)."""
        named = self.in_unit(unit, class_name)
        if named is not None and class_name in UNIT_FIRST:
            return named
        asset_id = unit.segment.asset_id
        cluster_id = self.cluster_of.get(asset_id, "")
        case_return = self.case_return.get(cluster_id)
        if class_name == "Return":
            return case_return or named or self._single(self.in_asset[asset_id], "Return")
        if case_return is not None:
            structural = self.edges.get(case_return, {}).get(class_name)
            if structural is not None:
                return structural
        if named is not None:
            return named
        if class_name == "Customer" and asset_id in self.author:
            return self.author[asset_id]
        return self._single(self.in_asset[asset_id], class_name) or self._single(
            self.in_cluster[cluster_id], class_name
        )

    def voice(self, unit: Unit) -> str:
        """customer, staff or unknown: who is speaking in the unit."""
        segment = unit.segment
        if segment.segment_type == "message":
            role = str(segment.structure.get("role") or "").lower()
            if not role:
                return "unknown"
            return "customer" if role in CUSTOMER_ROLES else "staff"
        if segment.segment_type == "email_body":
            return "customer" if segment.asset_id in self.author else "unknown"
        if segment.segment_type in STAFF_SEGMENTS:
            return "staff"
        return "unknown"


# --- the whole step ------------------------------------------------------------------------


@dataclass
class Extraction:
    claims: list[ClaimRecord]
    evidence: list[ClaimEvidenceRecord]
    unanchored: int = 0
    units: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {
            "claims": len(self.claims),
            "claim_evidence": len(self.evidence),
            "claims_unanchored": self.unanchored,
        }

    def __iter__(self):
        yield self.claims
        yield self.evidence


@dataclass
class _Pending:
    predicate: str
    subject: str
    obj: str
    confidence: float = 0.0
    evidence: dict[str, tuple[Unit, str]] = field(default_factory=dict)


def evidence_locator(unit: Unit) -> dict[str, Any]:
    locator = dict(unit.segment.locator)
    locator.pop("line_endings", None)
    locator["start"], locator["end"] = unit.start, unit.end
    if unit.segment.segment_type == "page":
        locator["text"] = unit.text
    return locator


def extract_claims(
    run: CrawlRunRecord,
    clusters: dict[str, list[str]],
    assets: Sequence[AssetRecord],
    segments: Sequence[SegmentRecord],
    mentions: Sequence[MentionRecord],
    links: Sequence[EntityLinkRecord],
    entities: Sequence[EntityRecord],
    relationships: Sequence[RelationshipRecord],
    settings: CrawlerSettings,
    ontology_version: str | None = None,
) -> Extraction:
    """Claims and their evidence for one run, from the resolution's output.
    Unpacks as ``(claims, evidence)``; ``counts`` has the run counts."""
    ontology_version = ontology_version if ontology_version is not None else run.ontology_version
    cues = compile_cues(settings)
    context = Context(clusters, segments, mentions, links, entities, relationships)
    analyzed = {a.asset_id for a in assets}
    pending: dict[str, _Pending] = {}
    unanchored = 0
    units = 0
    for segment in sorted(segments, key=lambda s: (s.asset_id, s.ordinal)):
        if analyzed and segment.asset_id not in analyzed:
            continue
        for unit in split_units(segment):
            units += 1
            hits = find_hits(unit.text, cues, settings)
            if not hits:
                continue
            voice = context.voice(unit)
            for predicate in sorted(hits):
                shape = SHAPES.get(predicate)
                if shape is None:
                    continue
                hit = hits[predicate]
                if predicate in STAFF_ONLY and (
                    voice == "customer" or (voice == "unknown" and hit.cue.strength != "strong")
                ):
                    continue  # only staff approve; an unknown speaker needs a labelled phrase
                if hit.cue.strength == "weak" and context.in_unit(unit, shape[0]) is None:
                    unanchored += 1  # a generic word needs the unit to name its subject
                    continue
                subject = context.entity_for(unit, shape[0])
                obj = context.entity_for(unit, shape[1])
                if subject is None or obj is None:
                    unanchored += 1
                    continue
                claim_id = ids.claim_id(predicate, subject, obj)
                claim = pending.setdefault(claim_id, _Pending(predicate, subject, obj))
                claim.confidence = max(claim.confidence, CONFIDENCE[hit.cue.strength])
                evidence_id = ids.evidence_id(claim_id, segment.segment_id, unit.start, unit.end)
                claim.evidence.setdefault(evidence_id, (unit, hit.cue.phrase))
    claims: list[ClaimRecord] = []
    evidence: list[ClaimEvidenceRecord] = []
    for claim_id in sorted(pending):
        claim = pending[claim_id]
        claims.append(
            ClaimRecord(
                crawl_run_id=run.crawl_run_id,
                claim_id=claim_id,
                predicate=claim.predicate,
                subject_entity_id=claim.subject,
                object_entity_id=claim.obj,
                object_value=None,
                confidence=claim.confidence,
                extractor=EXTRACTOR,
                ontology_version=ontology_version,
            )
        )
        for evidence_id in sorted(claim.evidence):
            unit, _ = claim.evidence[evidence_id]
            evidence.append(
                ClaimEvidenceRecord(
                    crawl_run_id=run.crawl_run_id,
                    claim_id=claim_id,
                    evidence_id=evidence_id,
                    asset_id=unit.segment.asset_id,
                    segment_id=unit.segment.segment_id,
                    locator=evidence_locator(unit),
                    excerpt=unit.text,
                    ontology_version=ontology_version,
                )
            )
    return Extraction(claims, evidence, unanchored, units)


__all__ = [
    "SHAPES",
    "Context",
    "Cue",
    "Extraction",
    "Hit",
    "Unit",
    "blocked",
    "compile_cue",
    "compile_cues",
    "cue_strength",
    "evidence_locator",
    "extract_claims",
    "find_hits",
    "sentence_spans",
    "split_units",
]
