"""Coverage signals (CG-10): what a crawl could not explain.

A crawl of unfamiliar data usually succeeds with low counts, which looks the
same as a corpus with little in it. These signals say what was left on the
table, with no ground truth needed, so a missing or wrong rule shows:

- passages in which nothing was recognised, and documents with no linked entity;
- text that looks like an identifier but that no pattern caught, grouped by its
  shape (letters as ``A``, digits as ``9``) with an example;
- lines on PDF pages that look like field labels but have no label rule.

Totals go into the run's counts; the examples are rows of
``helios_index.coverage``. What looks like an identifier or a label is a
heuristic: every row is a suggestion to look at, not an error.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence

from helios_core.crawler.settings import CrawlerSettings
from helios_core.index.records import (
    CoverageRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    MentionRecord,
    SegmentRecord,
)

from .analyzers import _is_header_like

COVERAGE = "helios_index.coverage"
TOP = 20  # examples kept per signal
# A code with letters then digits (AB-12345, X9/4410), a long number, or an email address.
IDENTIFIER_LIKE = re.compile(
    r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+"
    r"|\b[A-Za-z]{1,8}[-#/_]?\d{3,}[\w-]*"
    r"|\b\d{5,}\b"
)
UNMATCHED_IDENTIFIER = "unmatched_identifier"
UNKNOWN_LABEL = "unknown_label"


def shape_of(text: str) -> str:
    """The pattern of a string: its letters as A, its digits as 9."""
    return re.sub(r"\d", "9", re.sub(r"[A-Za-z]", "A", text))


def _unmatched_identifiers(
    segments: Sequence[SegmentRecord], spans: dict[str, list[tuple[int, int]]]
) -> tuple[Counter[str], dict[str, str]]:
    shapes: Counter[str] = Counter()
    example: dict[str, str] = {}
    for segment in segments:
        covered = spans.get(segment.segment_id, [])
        for match in IDENTIFIER_LIKE.finditer(segment.text):
            start, end = match.span()
            if any(s < end and start < e for s, e in covered):
                continue
            shape = shape_of(match.group())
            shapes[shape] += 1
            example.setdefault(shape, match.group())
    return shapes, example


def _unknown_labels(
    segments: Sequence[SegmentRecord], settings: CrawlerSettings
) -> tuple[Counter[str], dict[str, str]]:
    known = {rule.label.lower() for rule in settings.pdf_labels}
    rules = settings.analyzers.pdf
    labels: Counter[str] = Counter()
    example: dict[str, str] = {}
    for segment in segments:
        if segment.segment_type != "page":
            continue
        lines = [line.strip() for line in segment.text.split("\n")]
        for line, following in zip(lines, lines[1:], strict=False):
            if (
                line
                and following
                and line.lower() not in known
                and not line.endswith((".", ",", ";"))
                and _is_header_like(line, rules)
                and not _is_header_like(following, rules)
            ):
                labels[line] += 1
                example.setdefault(line, following)
    return labels, example


def measure(
    run: CrawlRunRecord,
    segments: Sequence[SegmentRecord],
    mentions: Sequence[MentionRecord],
    links: Sequence[EntityLinkRecord],
    settings: CrawlerSettings,
) -> tuple[dict[str, int], list[CoverageRecord]]:
    """(counts for the run, example rows) for the segments this run read."""
    read = [s for s in segments if s.text.strip()]
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for mention in mentions:
        if mention.start_offset is not None and mention.end_offset is not None:
            spans[mention.segment_id].append((mention.start_offset, mention.end_offset))
    mentioned = {m.segment_id for m in mentions}
    asset_of = {m.mention_id: m.asset_id for m in mentions}
    linked_assets = {asset_of[l.mention_id] for l in links if l.link_type == "SameAs" and l.mention_id in asset_of}
    assets = {s.asset_id for s in read}

    identifiers, identifier_example = _unmatched_identifiers(read, spans)
    labels, label_example = _unknown_labels(read, settings)
    counts = {
        "segments_without_mentions": sum(1 for s in read if s.segment_id not in mentioned),
        "assets_without_links": len(assets - linked_assets),
        "unmatched_identifiers": sum(identifiers.values()),
        "unknown_labels": sum(labels.values()),
    }
    rows = [
        CoverageRecord(
            crawl_run_id=run.crawl_run_id,
            signal=signal,
            value=value,
            count=count,
            example=examples[value],
            ontology_version=run.ontology_version,
        )
        for signal, found, examples in (
            (UNMATCHED_IDENTIFIER, identifiers, identifier_example),
            (UNKNOWN_LABEL, labels, label_example),
        )
        for value, count in sorted(found.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP]
    ]
    return counts, rows
