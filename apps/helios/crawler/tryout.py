"""Try a draft rule on sample text (the Settings editor's "try it" panel).

Applies one rule of a settings document that has not been saved to passages,
with the same code a crawl uses, and returns what it matches. Nothing is
written and the warehouse is not asked, so only rules that read text alone can
be tried: identifier patterns, PDF labels, contextual phrases, class cues and
claim cues. How the warehouse dictionary judges a name needs a crawl.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from helios_core.crawler.settings import CrawlerSettings
from helios_core.index.records import SegmentRecord

from .analyzers import _pdf_fields
from .claims import blocked, compile_cues, split_units
from .mentions import _compiled, _line_starts, _locate, _phrase

RULE_TYPES = ("pattern", "label", "contextual", "class_cue", "claim")
MAX_PASSAGES = 25
MAX_TEXT = 2400
MAX_VALUES = 12


@dataclass(frozen=True)
class Passage:
    id: str
    segment_type: str
    locator: dict[str, Any]
    text: str


def pasted(text: str) -> list[Passage]:
    """Pasted text as one passage, read the way a plain text file is."""
    return [Passage("pasted", "text", {"part": "text"}, text)] if text.strip() else []


def _segment(passage: Passage) -> SegmentRecord:
    return SegmentRecord(
        crawl_run_id="try",
        segment_id=passage.id,
        asset_id="try",
        segment_type=passage.segment_type,
        ordinal=0,
        locator=passage.locator,
        text=passage.text,
    )


def _match(start: int, end: int, text: str, **extra: Any) -> dict[str, Any]:
    return {"start": start, "end": end, "text": text[start:end], **extra}


def _pattern(settings: CrawlerSettings, rule: dict[str, Any], passage: Passage) -> list[dict[str, Any]]:
    found = next((p for p in settings.patterns if p.name == rule.get("name")), None)
    if found is None:
        raise ValueError(f"no pattern named {rule.get('name')!r} in these settings")
    matches = []
    for match in _compiled(found.regex, found.ignore_case).finditer(passage.text):
        if match.group(found.group):
            matches.append(_match(match.start(found.group), match.end(found.group), passage.text))
    return matches


def _phrases(passage: Passage, phrases: list[str]) -> list[dict[str, Any]]:
    return [
        _match(m.start(), m.end(), passage.text, rule=phrase)
        for phrase in phrases
        for m in _phrase(phrase).finditer(passage.text)
    ]


def _class_cue(settings: CrawlerSettings, rule: dict[str, Any], passage: Passage) -> list[dict[str, Any]]:
    cue = settings.cue_pattern(str(rule.get("class") or ""))
    if cue is None:
        return []
    return [_match(m.start(), m.end(), passage.text) for m in cue.finditer(passage.text) if m.group()]


def _label(settings: CrawlerSettings, rule: dict[str, Any], passage: Passage) -> list[dict[str, Any]]:
    labels = {r.label.lower(): r for r in settings.pdf_labels}
    wanted = str(rule.get("label") or "").lower()
    starts = _line_starts(passage.text)
    matches = []
    for pair in _pdf_fields(passage.text, labels).get("labels", []):
        if wanted and pair["label"].lower() != wanted:
            continue
        at = _locate(passage.text, starts, int(pair["line"]), str(pair["value"]))
        if at is not None:
            matches.append(_match(at, at + len(str(pair["value"])), passage.text, rule=pair["label"]))
    return matches


def _claim(settings: CrawlerSettings, rule: dict[str, Any], passage: Passage) -> list[dict[str, Any]]:
    predicate = rule.get("predicate")
    cues = [c for c in compile_cues(settings) if c.predicate == predicate]
    matches = []
    for unit in split_units(_segment(passage)):
        for cue in cues:
            for m in cue.pattern.finditer(unit.text):
                stopped = blocked(
                    unit.text, m.start(), settings.claims.negations, settings.claims.hedges, m.end()
                )
                matches.append(
                    _match(
                        unit.start + m.start(),
                        unit.start + m.end(),
                        passage.text,
                        rule=cue.phrase,
                        strength=cue.strength,
                        blocked=stopped,
                    )
                )
    return matches


def try_rule(settings: CrawlerSettings, rule: dict[str, Any], passages: list[Passage]) -> dict[str, Any]:
    """What ``rule`` (``{"type": ..., ...}``) matches in ``passages``: totals, the
    commonest matched strings, and the first passages with their matches."""
    kind = rule.get("type")
    if kind not in RULE_TYPES:
        raise ValueError(f"cannot try a rule of type {kind!r}; one of {', '.join(RULE_TYPES)}")
    if kind == "contextual":
        phrases = list(settings.contextual.get(str(rule.get("class") or ""), []))
    scanned = matched = total = stopped = 0
    values: Counter[str] = Counter()
    shown: list[dict[str, Any]] = []
    for passage in passages:
        if kind == "label" and passage.segment_type not in ("page", "text"):
            continue
        scanned += 1
        if kind == "pattern":
            found = _pattern(settings, rule, passage)
        elif kind == "contextual":
            found = _phrases(passage, phrases)
        elif kind == "class_cue":
            found = _class_cue(settings, rule, passage)
        elif kind == "label":
            found = _label(settings, rule, passage)
        else:
            found = _claim(settings, rule, passage)
        if not found:
            continue
        found.sort(key=lambda m: (m["start"], m["end"]))
        matched += 1
        total += len(found)
        stopped += sum(1 for m in found if m.get("blocked"))
        values.update(m["text"] for m in found if not m.get("blocked"))
        if len(shown) < MAX_PASSAGES:
            shown.append(
                {
                    "segment_type": passage.segment_type,
                    "locator": passage.locator,
                    "text": passage.text[:MAX_TEXT],
                    "truncated": len(passage.text) > MAX_TEXT,
                    "matches": [m for m in found if m["end"] <= MAX_TEXT],
                }
            )
    return {
        "scanned": scanned,
        "matched": matched,
        "matches": total,
        "blocked": stopped,
        "values": [{"text": text, "count": count} for text, count in values.most_common(MAX_VALUES)],
        "distinct_values": len(values),
        "passages": shown,
    }
