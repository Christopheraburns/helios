"""Step 7 of the analysis (docs/crawler-analysis.md): link documents into cases.

A case is usually spread over several documents (an email, a report, a chat
about one return), and the story grouping is hidden from the crawler. Documents
are joined into one **case cluster** when they share a strong identifier: a value
of one of the settings' ``cases.identifiers`` patterns (an RMA or support-case
number, a ticket number, a customer's email address). A key-kind identifier
counts only when the warehouse knows it (an email address that is a customer's,
not the support mailbox); a document ID or ticket number always counts.

A TPC-DS business ID (any other key pattern) is a weak identifier: it joins two
documents only when both carry a date within ``cases.date_window_days`` of each
other, and the value is not a hub shared by many documents (a store's ID).

Clusters are computed with union-find, so A-B and B-C put A, B and C together.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from helios_core.crawler.settings import CrawlerSettings, LabelRule, PatternRule
from helios_core.index.records import AssetRecord, MentionRecord

from .gazetteer import Gazetteer, normalise

def parse_date(value: str | None, formats: Iterable[str] = ()) -> date | None:
    """A date written in one of ``formats`` (the settings' ``date_formats``), or
    an ISO date or timestamp; None otherwise."""
    if not value:
        return None
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text[:10])
    for pattern in formats:
        try:  # a calendar date in a document: no time zone to attach
            return datetime.strptime(text, pattern).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class Identifier:
    name: str  # the pattern rule
    value: str  # normalised
    strong: bool


class Rules:
    """The settings' rules by name and label, for classifying mention details."""

    def __init__(self, settings: CrawlerSettings):
        self.patterns: dict[str, PatternRule] = {p.name: p for p in settings.patterns}
        self.labels: dict[str, LabelRule] = {r.label.lower(): r for r in settings.pdf_labels}
        self.strong = list(settings.cases.identifiers)
        self.window = settings.cases.date_window_days
        self.max_hub = settings.cases.max_hub_documents
        self.date_formats = tuple(settings.date_formats)
        self.tuning = settings.resolution
        self.about = settings.about

    def key_name(self, pattern: str) -> str:
        """The key a document identifier found by ``pattern`` is stored under."""
        rule = self.patterns.get(pattern)
        return (rule.key_name if rule is not None else None) or pattern

    def label_of(self, mention: MentionRecord) -> LabelRule | None:
        detail = mention.extractor_detail
        if detail.startswith(("label:", "table:")):
            return self.labels.get(detail.split(":", 1)[1].lower())
        return None

    def pattern_of(self, mention: MentionRecord) -> PatternRule | None:
        if mention.extractor != "pattern":
            return None
        return self.patterns.get(mention.extractor_detail)

    def document_rule(self, value: str) -> PatternRule | None:
        """The document_id pattern whose regular expression matches ``value`` whole."""
        for rule in self.patterns.values():
            if rule.kind != "document_id":
                continue
            flags = re.IGNORECASE if rule.ignore_case else 0
            if re.fullmatch(rule.regex, value, flags):
                return rule
        return None

    def key_rule(self, columns: Iterable[str]) -> PatternRule | None:
        """A key pattern looking values up in one of ``columns`` (for labelled values)."""
        wanted = set(columns)
        for rule in self.patterns.values():
            if rule.kind == "key" and wanted & set(rule.columns):
                return rule
        return None


def _known(gazetteer: Gazetteer, columns: Iterable[str], value: str) -> bool | None:
    """Whether the gazetteer knows ``value`` as a key; None if it indexes none of the columns."""
    indexed = [c for c in columns if c in gazetteer.key_forms_by_column]
    if not indexed:
        return None
    return any(gazetteer.lookup(c, value) is not None for c in indexed)


def identifiers_of(mention: MentionRecord, rules: Rules, gazetteer: Gazetteer) -> list[Identifier]:
    """The case identifiers one mention carries (usually none or one)."""
    if mention.extractor != "pattern":
        return []
    value = normalise(mention.surface_form)
    rule = rules.pattern_of(mention)
    label = rules.label_of(mention)
    if label is not None:
        if label.kind == "document_id":
            rule = rules.document_rule(mention.surface_form)
        elif label.kind == "key":
            rule = rules.key_rule(label.columns)
        else:
            rule = None
    if rule is None or rule.kind not in ("key", "document_id"):
        return []
    if rule.kind == "key" and _known(gazetteer, rule.columns, mention.surface_form) is False:
        return []  # an email address or ID the warehouse does not know
    return [Identifier(rule.name, value, strong=rule.name in rules.strong)]


def asset_dates(
    assets: Iterable[AssetRecord], mentions: Iterable[MentionRecord], rules: Rules
) -> dict[str, date]:
    """asset_id -> the asset's date: its semantic timestamp, else the earliest
    date its text mentions."""
    dates: dict[str, date] = {}
    for asset in assets:
        parsed = parse_date(asset.semantic_timestamp, rules.date_formats)
        if parsed is not None:
            dates[asset.asset_id] = parsed
    mentioned: dict[str, list[date]] = defaultdict(list)
    for mention in mentions:
        rule = rules.pattern_of(mention)
        if rule is not None and rule.kind == "value":
            parsed = parse_date(mention.surface_form, rules.date_formats)
            if parsed is not None:
                mentioned[mention.asset_id].append(parsed)
    for asset_id, found in mentioned.items():
        dates.setdefault(asset_id, min(found))
    return dates


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # The smaller root wins, so cluster IDs are stable across runs.
            self.parent[max(ra, rb)] = min(ra, rb)


def cluster_assets(
    assets: Iterable[AssetRecord],
    mentions: Iterable[MentionRecord],
    rules: Rules,
    gazetteer: Gazetteer,
) -> dict[str, list[str]]:
    """cluster_id (its smallest asset_id) -> sorted asset_ids. Every asset is in
    exactly one cluster; a document that shares nothing is a cluster of one."""
    assets = list(assets)
    mentions = list(mentions)
    dates = asset_dates(assets, mentions, rules)
    members: dict[Identifier, set[str]] = defaultdict(set)
    for mention in mentions:
        for identifier in identifiers_of(mention, rules, gazetteer):
            members[identifier].add(mention.asset_id)
    uf = UnionFind()
    for asset in assets:
        uf.find(asset.asset_id)
    for identifier, asset_ids in sorted(members.items(), key=lambda kv: (kv[0].name, kv[0].value)):
        ordered = sorted(asset_ids)
        if identifier.strong:
            for other in ordered[1:]:
                uf.union(ordered[0], other)
        elif len(ordered) <= rules.max_hub:
            for i, a in enumerate(ordered):
                for b in ordered[i + 1 :]:
                    if (
                        a in dates
                        and b in dates
                        and abs((dates[a] - dates[b]).days) <= rules.window
                    ):
                        uf.union(a, b)
    clusters: dict[str, list[str]] = defaultdict(list)
    for asset in assets:
        clusters[uf.find(asset.asset_id)].append(asset.asset_id)
    return {root: sorted(ids) for root, ids in sorted(clusters.items())}


__all__ = [
    "Identifier",
    "Rules",
    "UnionFind",
    "asset_dates",
    "cluster_assets",
    "identifiers_of",
    "parse_date",
]
