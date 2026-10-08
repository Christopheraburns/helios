"""Step 12 of the analysis (docs/crawler-analysis.md): extract claims.

A claim is a predicate asserted between two entities the resolution step
identified. Which predicates exist, the classes of their subject and object,
where to find each, who may assert them and the cue phrases that state them
all come from the crawler settings (``claims``); this module knows none by
name. Nothing here looks at the warehouse or the answer key: the inputs are the
run's segments, mentions, links, entities, relationships and case clusters.

**Units.** A chat message is one unit; an email body, a PDF page or a text file
is split into sentences (``.``, ``?``, ``!`` followed by whitespace, and line
breaks, so a labelled report line is one unit). The settings list the
abbreviations whose period does not end a sentence. Offsets are exact:
``segment.text[start:end]`` is the unit, which becomes the evidence excerpt and
locator.

**Cues.** Each predicate has a cue lexicon (``claims.cues``), matched
case-insensitively on word boundaries; ``*`` in a phrase stands for up to
``gap_words`` words. A cue is blocked by a negation within ``negation_window``
words before it or a hedge earlier in the same clause; clauses are split on
``.;,:`` so a negation in one clause does not block a cue in the next.
Confidence follows cue strength: a multi-word phrase or a labelled field is
strong, a single word is medium, and a single word the settings list under
``weak_words`` is weak.

**Subjects and objects** come from resolution. Each role names its class and an
ordered list of places to look (``find``): in the unit itself, the case's own
entity, an entity related to the case's by a named relationship, the
document's author, or the only entity of the class in the document or the case.

**Speakers.** The settings' ``speakers`` rules say who is speaking in a unit
(by a chat message's role, an email's resolved author, the kind of document);
a predicate may refuse some speakers, or ask an unidentified one for a strong
cue. A weak cue counts only when the unit itself names the claim's subject.

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

from helios_core.crawler.settings import CaseEdge, ClaimRole, Claims, CrawlerSettings
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
# Which kinds of claim exist, what each is about, who may make it, which words
# are weak cues, what cancels a cue and which abbreviations do not end a
# sentence all come from the settings' ``claims`` section. Segment types are
# the analyzers' own names.
SENTENCE_SEGMENTS = {"email_body", "page", "text", "row_column"}
SKIPPED_SEGMENTS = {"email_header", "email_subject"}

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


def _abbreviated(text: str, at: int, abbreviations: Iterable[str] = ()) -> bool:
    """Whether the period at ``at`` ends an abbreviation (a listed one, or a
    single capital initial) rather than a sentence."""
    before = text[:at]
    match = re.search(r"(\w+)$", before)
    if match is None:
        return False
    word = match.group(1)
    return word.lower() in abbreviations or (len(word) == 1 and word.isupper())


def sentence_spans(text: str, abbreviations: Iterable[str] = ()) -> list[tuple[int, int]]:
    """(start, end) of each sentence, whitespace trimmed, so text[start:end] is the sentence."""
    abbreviations = frozenset(abbreviations)
    spans = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        if match.group() != "\n" and _abbreviated(text, match.start(), abbreviations):
            continue
        end = match.start() if match.group() == "\n" else match.end()
        spans.append(_trimmed(text, start, end))
        start = match.end()
    spans.append(_trimmed(text, start, len(text)))
    return [(s, e) for s, e in spans if e > s]


def split_units(segment: SegmentRecord, abbreviations: Iterable[str] = ()) -> list[Unit]:
    """The claim units of one segment: the message, or the sentences."""
    if segment.segment_type in SKIPPED_SEGMENTS:
        return []
    if segment.segment_type in SENTENCE_SEGMENTS:
        return [Unit(segment, s, e) for s, e in sentence_spans(segment.text, abbreviations)]
    start, end = _trimmed(segment.text, 0, len(segment.text))
    return [Unit(segment, start, end)] if end > start else []


# --- cues -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cue:
    predicate: str
    phrase: str
    pattern: re.Pattern[str]
    strength: str  # strong, medium or weak


def cue_strength(phrase: str, weak_words: Iterable[str] = ()) -> str:
    """A phrase of several words, a labelled field ("Disposition:") or a gapped
    phrase is strong; two words are medium; one word is medium unless the
    settings list it as too general to stand alone (weak)."""
    words = phrase.split()
    if len(words) >= 3 or phrase.endswith(":") or "*" in words:
        return "strong"
    if len(words) == 2:
        return "medium"
    return "weak" if phrase.lower() in weak_words else "medium"


def compile_cue(phrase: str, gap_words: int = 4) -> re.Pattern[str]:
    """A whole-word, case-insensitive match of the phrase; ``*`` spans up to
    ``gap_words`` words."""
    body = ""
    for i, word in enumerate(phrase.split()):
        if word == "*":
            body += rf"(?:\s+\S+){{0,{gap_words}}}"
        else:
            body += (r"\s+" if i else "") + re.escape(word)
    head = r"(?<!\w)" if phrase[:1].isalnum() else ""
    tail = r"(?!\w)" if phrase[-1:].isalnum() else ""
    return re.compile(head + body + tail, re.IGNORECASE)


def compile_cues(settings: CrawlerSettings) -> list[Cue]:
    cues = []
    weak = frozenset(w.lower() for w in settings.claims.weak_words)
    for predicate in sorted(settings.claims.cues):
        for phrase in settings.claims.cues[predicate]:
            phrase = phrase.strip()
            if phrase:
                pattern = compile_cue(phrase, settings.claims.gap_words)
                cues.append(Cue(predicate, phrase, pattern, cue_strength(phrase, weak)))
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
    text: str,
    start: int,
    negations: Iterable[str],
    hedges: Iterable[str],
    end: int | None = None,
    window: int = 3,
) -> bool:
    """A negation in the last words before the cue (or inside a gapped cue,
    ``refund wasn't approved``), or a hedge anywhere earlier in the cue's
    clause, blocks it."""
    before = _words(_clause_before(text, start))
    inside = _words(text[start:end]) if end is not None else []
    negations = {n.lower() for n in negations}
    hedges = {h.lower() for h in hedges}
    recent = before[-window:] if window else []
    if any(w in negations for w in [*recent, *inside]):
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
                settings.claims.negation_window,
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
    """What resolution found, indexed for finding a claim's subject and object."""

    def __init__(
        self,
        clusters: dict[str, list[str]],
        segments: Sequence[SegmentRecord],
        mentions: Sequence[MentionRecord],
        links: Sequence[EntityLinkRecord],
        entities: Sequence[EntityRecord],
        relationships: Sequence[RelationshipRecord],
        rules: Claims | None = None,
    ):
        self.rules = rules or Claims()
        case_classes = list(self.rules.case_classes)
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
        authors: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
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
            if mention.segment_id in header_segments:
                authors[mention.asset_id][class_name].add(link.entity_id)
        # asset -> class -> the document's one author of that class
        self.author: dict[str, dict[str, str]] = {
            asset: {cls: next(iter(found)) for cls, found in by_class.items() if len(found) == 1}
            for asset, by_class in authors.items()
        }
        self.about: dict[str, str] = {}
        # case entity -> (relationship type, class) -> the entity it is related to
        self.edges: dict[str, dict[tuple[str, str], str]] = defaultdict(dict)
        for r in sorted(relationships, key=lambda r: r.relationship_id):
            if r.relationship_type == "About" and r.source_kind == "asset":
                self.about[r.source_id] = r.target_id
            elif (
                r.source_kind == "entity"
                and r.target_kind == "entity"
                and self.class_of.get(r.source_id) in case_classes
            ):
                target_class = self.class_of.get(r.target_id)
                if target_class:
                    self.edges[r.source_id].setdefault(
                        (r.relationship_type, target_class), r.target_id
                    )
        # cluster -> the entity the case is about: the one its documents are About,
        # else the only one of a case class the case names.
        self.case_entity: dict[str, str] = {}
        for cluster_id, members in clusters.items():
            for case_class in case_classes:
                targets = {
                    self.about[a]
                    for a in members
                    if a in self.about and self.class_of.get(self.about[a]) == case_class
                }
                if len(targets) != 1:
                    targets = self.in_cluster[cluster_id].get(case_class, set())
                if len(targets) == 1:
                    self.case_entity[cluster_id] = next(iter(targets))
                    break

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

    def entity_for(self, unit: Unit, role: ClaimRole) -> str | None:
        """The entity a claim's subject or object is: the first of the role's
        ``find`` steps that gives exactly one entity of its class."""
        class_name = role.class_name
        asset_id = unit.segment.asset_id
        cluster_id = self.cluster_of.get(asset_id, "")
        case = self.case_entity.get(cluster_id)
        for step in role.find:
            found: str | None = None
            if isinstance(step, CaseEdge):
                if case is not None:
                    found = self.edges.get(case, {}).get((step.case_edge, class_name))
            elif step == "in_unit":
                found = self.in_unit(unit, class_name)
            elif step == "case":
                found = case if case is not None and self.class_of.get(case) == class_name else None
            elif step == "author":
                found = self.author.get(asset_id, {}).get(class_name)
            elif step == "single_in_document":
                found = self._single(self.in_asset[asset_id], class_name)
            elif step == "single_in_case":
                found = self._single(self.in_cluster[cluster_id], class_name)
            if found is not None:
                return found
        return None

    def voice(self, unit: Unit) -> str:
        """Who is speaking in the unit, by the settings' speaker rules; "unknown" if none fits."""
        segment = unit.segment
        for rule in self.rules.speakers:
            if rule.segment != segment.segment_type:
                continue
            if rule.field is not None:
                value = str(segment.structure.get(rule.field) or "").lower()
                if not value or (rule.values and value not in {v.lower() for v in rule.values}):
                    continue
            if rule.author_class is not None and rule.author_class not in self.author.get(
                segment.asset_id, {}
            ):
                continue
            return rule.speaker
        return "unknown"


# --- the whole step ------------------------------------------------------------------------


@dataclass
class Extraction:
    claims: list[ClaimRecord]
    evidence: list[ClaimEvidenceRecord]
    unanchored: int = 0
    units: int = 0
    # Cue hits that made no claim, by why (CG-10).
    dropped: dict[str, int] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {
            "claims": len(self.claims),
            "claim_evidence": len(self.evidence),
            "claims_unanchored": self.unanchored,
            **{f"claims_dropped_{why}": count for why, count in sorted(self.dropped.items())},
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
    rules = settings.claims
    confidence = rules.confidence.model_dump()
    context = Context(clusters, segments, mentions, links, entities, relationships, rules)
    analyzed = {a.asset_id for a in assets}
    pending: dict[str, _Pending] = {}
    dropped = {"undefined": 0, "speaker": 0, "weak_cue": 0, "no_subject": 0, "no_object": 0}
    unanchored = 0
    units = 0
    for segment in sorted(segments, key=lambda s: (s.asset_id, s.ordinal)):
        if analyzed and segment.asset_id not in analyzed:
            continue
        for unit in split_units(segment, rules.abbreviations):
            units += 1
            hits = find_hits(unit.text, cues, settings)
            if not hits:
                continue
            voice = context.voice(unit)
            for predicate in sorted(hits):
                shape = rules.predicates.get(predicate)
                if shape is None:
                    dropped["undefined"] += 1  # cue words for a kind of claim not defined
                    continue
                hit = hits[predicate]
                if voice in shape.blocked_speakers or (
                    shape.unknown_speaker_needs_strong_cue
                    and voice == "unknown"
                    and hit.cue.strength != "strong"
                ):
                    dropped["speaker"] += 1  # not this speaker's claim to make
                    continue
                if (
                    hit.cue.strength == "weak"
                    and context.in_unit(unit, shape.subject.class_name) is None
                ):
                    unanchored += 1  # a general word needs the unit to name its subject
                    dropped["weak_cue"] += 1
                    continue
                subject = context.entity_for(unit, shape.subject)
                obj = context.entity_for(unit, shape.object)
                if subject is None or obj is None:
                    unanchored += 1
                    dropped["no_subject" if subject is None else "no_object"] += 1
                    continue
                claim_id = ids.claim_id(predicate, subject, obj)
                claim = pending.setdefault(claim_id, _Pending(predicate, subject, obj))
                claim.confidence = max(claim.confidence, confidence[hit.cue.strength])
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
    return Extraction(claims, evidence, unanchored, units, dropped)


__all__ = [
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
