"""Step 5 of the analysis (docs/crawler-analysis.md): find mentions in segments.

Four deterministic extractors run over every segment, and each mention records
which found it (``extractor``) and why (``extractor_detail``):

- ``pattern``: the structure the analyzers recognised (PDF label values
  ``label:<label>``, table cells ``table:<column>``, email identity headers
  ``header:<name>``), then the settings' identifier patterns (IDs, email
  addresses, ticket numbers, RMA and case numbers, money and dates);
- ``gazetteer``: names from the warehouse (``gazetteer.py``); a low-specificity
  name counts next to a context cue for its class, or when the same asset
  confirms the name elsewhere (a PDF's Store label confirms the store named
  in its opening line); a partial name (a bare first name in a sign-off)
  counts only when the asset anchors one of its instances by a key or a full
  name (the From header, an email address, a customer ID);
- ``contextual``: definite phrases ("the item", "this return") recorded with no
  entity yet.

TPC-DS reuses business keys across tables (store 1, item 1 and customer 1 are
all ``AAAAAAAABAAAAAAA``) and short store names double as product names, so one
span can name several classes. A mention has one ``mention_id`` per (span,
extractor), so each extractor proposes one class per span: the class whose
cue is nearest, then the first in the rule's column order; a label settles it
outright. Resolution (step 8) revisits the choice with structured joins.

Offsets are within the segment's text; locators follow the ground-truth
conventions (the segment locator plus ``start``/``end``, and ``text`` on PDF pages).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from helios_core.crawler.settings import CrawlerSettings, LabelRule, PatternRule
from helios_core.index import ids
from helios_core.index.records import CrawlRunRecord, MentionRecord, SegmentRecord
from helios_core.ontology.mapping import ResolutionConfig

from .connectors import SourceAsset
from .gazetteer import KIND_RANK, Gazetteer, GazetteerHit, normalise

CUE_WINDOW = 40  # characters either side of a low-specificity name
_TPCDS_ID = r"(?-i:\b[A-P]{16}\b)"
_EMAIL = r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"
_SALUTATION = r"\b(?:mr|mrs|ms|miss|dr|sir|madam|prof)\b\.?"
# Context that makes a low-specificity name count, per class. Classes without a
# rule (Reason, Brand: their forms are multi-word) always count.
CUES: dict[str, re.Pattern[str]] = {
    "Store": re.compile(rf"\b(?:store|at)\b|\(#|#|{_TPCDS_ID}", re.IGNORECASE),
    "Item": re.compile(rf"\b(?:item|product)\b|\(\s*{_TPCDS_ID}", re.IGNORECASE),
    "Customer": re.compile(rf"{_SALUTATION}|{_EMAIL}", re.IGNORECASE),
}
FAR = CUE_WINDOW + 1  # "no cue in the window"


@dataclass(frozen=True)
class Found:
    start: int | None
    end: int | None
    surface: str
    proposed_class: str | None
    extractor: str
    detail: str
    instances: tuple[str, ...] = ()  # the warehouse instances the span can name, if known
    partial: bool = False  # a partial name: counts only next to an anchored instance


@lru_cache(maxsize=256)
def _compiled(regex: str, ignore_case: bool) -> re.Pattern[str]:
    return re.compile(regex, re.IGNORECASE if ignore_case else 0)


@lru_cache(maxsize=256)
def _phrase(phrase: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)


# --- cues ----------------------------------------------------------------------------


def cue_distance(text: str, start: int, end: int, class_name: str) -> int:
    """Characters between the span and the nearest cue for ``class_name``: 0 for
    classes without cues or for a composite name carrying its own ("Mrs. Raymond");
    ``FAR`` when none is within the window."""
    cue = CUES.get(class_name)
    if cue is None:
        return 0
    before = text[max(0, start - CUE_WINDOW) : start]
    after = text[end : end + CUE_WINDOW]
    distances = [len(before) - m.end() for m in cue.finditer(before)]
    distances += [m.start() for m in cue.finditer(after)]
    inner = text[start:end]
    if any(m.group() != inner for m in cue.finditer(inner)):
        distances.append(0)
    return min(distances, default=FAR)


def _nearest_class(text: str, start: int, end: int, candidates: list[str]) -> str:
    """The candidate with the nearest cue; ties keep the candidates' order."""
    return min(candidates, key=lambda c: (cue_distance(text, start, end, c), candidates.index(c)))


# --- the extractors ------------------------------------------------------------------


def _patterns(
    text: str, rules: Iterable[PatternRule], gazetteer: Gazetteer, column_classes: dict[str, str]
) -> Iterator[Found]:
    for rule in rules:
        for match in _compiled(rule.regex, rule.ignore_case).finditer(text):
            value = match.group(rule.group)
            if not value:
                continue
            start, end = match.start(rule.group), match.end(rule.group)
            proposed = rule.proposed_class
            forms: dict[str, tuple[str, ...]] = {}
            if rule.kind == "key":
                for column in rule.columns:
                    form = gazetteer.lookup(column, value)
                    if form is not None:
                        forms.setdefault(form.class_name, form.instances)
            if proposed is None and rule.kind == "key":
                owners = list(forms)
                if not owners:  # unseen value: only one mapped class could own it
                    owners = sorted(
                        {column_classes[c] for c in rule.columns if c in column_classes}
                    )
                    owners = owners if len(owners) == 1 else []
                proposed = _nearest_class(text, start, end, owners) if owners else None
            yield Found(
                start, end, value, proposed, "pattern", rule.name, forms.get(proposed or "", ())
            )


def _gazetteer(
    text: str, gazetteer: Gazetteer, classes: set[str]
) -> tuple[list[Found], list[list[Found]]]:
    """(accepted, deferred): one accepted hit per span, or, for a low-specificity
    name with no cue, its candidates in order of preference, which count only
    if the asset confirms one of them elsewhere. Partial names are always
    deferred: they count only next to an anchored instance."""
    by_span: dict[tuple[int, int], list[GazetteerHit]] = {}
    for hit in gazetteer.scan(text):
        if hit.class_name in classes:
            by_span.setdefault((hit.start, hit.end), []).append(hit)
    accepted: list[Found] = []
    deferred: list[list[Found]] = []
    for (start, end), hits in by_span.items():
        partial = [h for h in hits if h.partial]
        whole = [h for h in hits if not h.partial]
        if partial:
            deferred.append([_hit_found(h) for h in sorted(partial, key=lambda h: len(h.instances))])
        if not whole:
            continue
        ranked = sorted(whole, key=lambda h: _rank(text, start, end, h))
        if _rank(text, start, end, ranked[0])[0] >= FAR:
            deferred.append([_hit_found(h) for h in ranked])
        else:
            accepted.append(_hit_found(ranked[0]))
    return accepted, deferred


def _rank(text: str, start: int, end: int, hit: GazetteerHit) -> tuple[int, int, int, str]:
    """Preference among the classes one span may name: nearest cue (for
    low-specificity names), most specific kind, fewest instances."""
    distance = cue_distance(text, start, end, hit.class_name) if hit.low_specificity else 0
    return (distance, KIND_RANK[hit.kind], len(hit.instances), hit.class_name)


def _hit_found(hit: GazetteerHit, offset: int = 0) -> Found:
    return Found(
        hit.start + offset,
        hit.end + offset,
        hit.surface,
        hit.class_name,
        "gazetteer",
        hit.columns,
        hit.instances,
        hit.partial,
    )


def _contextual(text: str, phrases: dict[str, list[str]]) -> Iterator[Found]:
    for class_name, words in phrases.items():
        for phrase in words:
            for match in _phrase(phrase).finditer(text):
                yield Found(
                    match.start(), match.end(), match.group(), class_name, "contextual", phrase
                )


# --- structure the analyzers recognised -------------------------------------------------


def _line_starts(text: str) -> list[int]:
    starts = [0]
    for i, char in enumerate(text):
        if char == "\n":
            starts.append(i + 1)
    return starts


def _locate(text: str, starts: list[int], line: int, value: str) -> int | None:
    """Offset of ``value`` on ``line`` of the page text (lines were stripped when
    recognised), else its first occurrence anywhere."""
    if 0 <= line < len(starts):
        at = text.find(value, starts[line])
        if at >= 0:
            return at
    at = text.find(value)
    return at if at >= 0 else None


def _structured_value(
    value: str,
    start: int | None,
    rule_class: str,
    kind: str,
    detail: str,
    gazetteer: Gazetteer,
) -> Iterator[Found]:
    """A labelled value: names inside a display value come from the gazetteer (the
    label is their cue), everything else is the value itself."""
    if kind == "value":
        return
    if kind == "display":
        hits = [h for h in gazetteer.scan(value) if h.class_name == rule_class and not h.partial]
        if hits and start is not None:
            spans: set[tuple[int, int]] = set()
            for hit in hits:
                if (hit.start, hit.end) not in spans:
                    spans.add((hit.start, hit.end))
                    yield _hit_found(hit, start)
            return
    end = None if start is None else start + len(value)
    yield Found(start, end, value, rule_class, "pattern", detail)


def _pdf_structure(
    text: str, structure: dict[str, Any], labels: dict[str, LabelRule], gazetteer: Gazetteer
) -> Iterator[Found]:
    starts = _line_starts(text)
    for pair in structure.get("labels", []):
        rule_class = pair.get("proposed_class")
        if not rule_class:
            continue
        value = str(pair["value"])
        start = _locate(text, starts, int(pair.get("line", -1)), value)
        yield from _structured_value(
            value, start, rule_class, pair.get("kind", "key"), f"label:{pair['label']}", gazetteer
        )
    for table in structure.get("tables", []):
        columns = table.get("columns", [])
        width = len(columns)
        first = int(table.get("first_line", -1))
        for r, row in enumerate(table.get("rows", [])):
            for k, (header, cell) in enumerate(zip(columns, row, strict=False)):
                rule = labels.get(str(header).lower())
                if rule is None or not rule.proposed_class or not str(cell).strip():
                    continue
                line = first + width + r * width + k if first >= 0 else -1
                start = _locate(text, starts, line, str(cell))
                yield from _structured_value(
                    str(cell), start, rule.proposed_class, rule.kind, f"table:{header}", gazetteer
                )


def _email_header(
    segment: SegmentRecord, classes: set[str], gazetteer: Gazetteer
) -> Iterator[Found]:
    name = str(segment.structure.get("display_name") or "").strip()
    if name and "Customer" in classes:
        at = segment.text.find(name)
        if at >= 0:
            header = segment.locator.get("header", "")
            form = gazetteer.forms.get(("Customer", normalise(name)))
            instances = form.instances if form is not None and not form.partial else ()
            yield Found(
                at, at + len(name), name, "Customer", "pattern", f"header:{header}", instances
            )


# --- assembling records ---------------------------------------------------------------


def _covered_by_pattern(found: Found, patterns: list[Found], key: bool) -> bool:
    """A gazetteer hit inside a pattern mention adds nothing: a key is the same
    string the pattern classified; another name adds nothing of the same class."""
    if found.start is None:
        return False
    return any(
        p.start is not None
        and p.start <= found.start
        and found.end <= p.end
        and (key or p.proposed_class == found.proposed_class)
        for p in patterns
    )


def _locator(segment: SegmentRecord, found: Found) -> dict[str, Any]:
    locator = dict(segment.locator)
    locator.pop("line_endings", None)
    if found.start is not None:
        locator["start"], locator["end"] = found.start, found.end
    if segment.segment_type == "page":
        locator["text"] = found.surface
    return locator


def _record(run: CrawlRunRecord, segment: SegmentRecord, found: Found) -> MentionRecord:
    return MentionRecord(
        crawl_run_id=run.crawl_run_id,
        mention_id=ids.mention_id(
            segment.segment_id, found.start, found.end, found.surface, found.extractor
        ),
        asset_id=segment.asset_id,
        segment_id=segment.segment_id,
        surface_form=found.surface,
        locator=_locator(segment, found),
        proposed_class=found.proposed_class,
        extractor=found.extractor,
        extractor_detail=found.detail,
        start_offset=found.start,
        end_offset=found.end,
        ontology_version=run.ontology_version,
    )


def segment_mentions(
    segment: SegmentRecord,
    settings: CrawlerSettings,
    gazetteer: Gazetteer,
    classes: set[str],
    column_classes: dict[str, str],
) -> tuple[list[Found], list[list[Found]]]:
    """(mentions, deferred low-specificity names with their candidates) of one segment."""
    text = segment.text
    labels = {rule.label.lower(): rule for rule in settings.pdf_labels}
    structured: list[Found] = []
    if segment.segment_type == "page":
        structured = list(_pdf_structure(text, segment.structure, labels, gazetteer))
    elif segment.segment_type == "email_header":
        structured = list(_email_header(segment, classes, gazetteer))
    patterns = [f for f in structured if f.extractor == "pattern"]
    patterns += list(_patterns(text, settings.patterns, gazetteer, column_classes))
    accepted, deferred = _gazetteer(text, gazetteer, classes)
    labelled = [f for f in structured if f.extractor == "gazetteer"]
    settled = {(f.start, f.end) for f in labelled}  # a label decides its span's class
    accepted = labelled + [f for f in accepted if (f.start, f.end) not in settled]
    key_columns = set(gazetteer.key_forms_by_column)
    names = [n for n in accepted if not _covered_by_pattern(n, patterns, n.detail in key_columns)]
    found = patterns + names + list(_contextual(text, settings.contextual))
    return found, deferred


def _confirmed(records: Iterable[MentionRecord]) -> set[tuple[str, str]]:
    """(class, normalised surface) pairs a document names with confidence."""
    return {
        (m.proposed_class, normalise(m.surface_form))
        for m in records
        if m.proposed_class and m.extractor in ("pattern", "gazetteer")
    }


def anchored_instances(found: Iterable[Found]) -> dict[str, set[str]]:
    """class -> the instances an asset names by a key or a whole name: what a
    partial name in the same asset may refer to."""
    anchored: dict[str, set[str]] = {}
    for item in found:
        if item.instances and not item.partial and item.proposed_class:
            anchored.setdefault(item.proposed_class, set()).update(item.instances)
    return anchored


def _add(records: dict[str, MentionRecord], record: MentionRecord) -> None:
    existing = records.get(record.mention_id)
    # The ID ignores the class: keep the first, unless it had no class.
    if existing is None or (existing.proposed_class is None and record.proposed_class is not None):
        records[record.mention_id] = record


def extract_mentions(
    run: CrawlRunRecord,
    asset: SourceAsset,
    segments: list[SegmentRecord],
    settings: CrawlerSettings,
    gazetteer: Gazetteer,
    config: ResolutionConfig,
) -> list[MentionRecord]:
    """Every mention in ``asset``'s segments, deduplicated and in a stable order."""
    classes = set(gazetteer.classes) | set(config.classes)
    column_classes = dict(gazetteer.column_classes)
    own = [s for s in segments if s.asset_id == asset.asset_id]
    records: dict[str, MentionRecord] = {}
    deferred: list[tuple[SegmentRecord, list[Found]]] = []
    accepted: list[Found] = []
    for segment in own:
        found, waiting = segment_mentions(segment, settings, gazetteer, classes, column_classes)
        for item in found:
            _add(records, _record(run, segment, item))
        accepted += found
        deferred += [(segment, candidates) for candidates in waiting]
    confirmed = _confirmed(records.values())
    anchored = anchored_instances(accepted)
    for segment, candidates in deferred:
        for item in candidates:
            if item.partial:
                known = anchored.get(item.proposed_class or "", set())
                if known.intersection(item.instances):
                    _add(records, _record(run, segment, item))
                    break
            elif (item.proposed_class, normalise(item.surface)) in confirmed:
                _add(records, _record(run, segment, item))
                break
    ordinal = {s.segment_id: s.ordinal for s in segments}
    return sorted(
        records.values(),
        key=lambda m: (
            ordinal.get(m.segment_id, 0),
            m.start_offset if m.start_offset is not None else -1,
            m.end_offset if m.end_offset is not None else -1,
            m.extractor,
            m.proposed_class or "",
            m.surface_form,
        ),
    )


__all__ = [
    "CUES",
    "Found",
    "anchored_instances",
    "cue_distance",
    "extract_mentions",
    "segment_mentions",
]
