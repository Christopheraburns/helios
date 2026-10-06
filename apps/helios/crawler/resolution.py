"""Steps 6-11 of the analysis (docs/crawler-analysis.md): resolve mentions to
warehouse instances, jointly per case, and derive relationships.

Per mention, **candidates** are scored by tier (step 6): ``exact_key`` for keys
the gazetteer knows, ``alias`` for names (score = the form's specificity),
``fuzzy`` for a labelled name with a typo (rapidfuzz against the names the asset
anchors). Values for an anchor's lookups, and dates, are not candidates but
*constraints*. A document's own ID names the case's entity before its warehouse
row is known.

Per **case cluster** (``cases.py``), the constraints are combined into one
parameterised query for the row of an *anchor* the mapping declares
(``anchors.py``; step 8). Exactly one row identifies the anchor's entity, its
related records, and the entities their foreign keys name; every mention in the
cluster whose candidates (or whose name's instances) include an identified
instance is promoted to SameAs with ``resolved_by = "joint"``. No row, or
several, promotes nothing. Which class is an anchor, how documents point at its
row and what the row names all come from the mapping; this module knows no
class by name.

Links are decided per tier threshold (step 10): SameAs at or above the tier's
threshold with no close runner-up, else PossiblySameAs for the top candidates.
A low-specificity name never becomes SameAs on its own. Contextual references
link when the asset, else its cluster, has exactly one resolved instance of the
class (step 9). Relationships (step 11): Mentions and About per asset, and the
structural edges of the identified warehouse row.

The crawler never reads ground truth; everything here comes from the documents,
the mapping and the warehouse.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from helios_core.crawler.settings import About, CrawlerSettings, ResolutionRules
from helios_core.index import ids
from helios_core.index.records import (
    AssetRecord,
    CrawlRunRecord,
    EntityLinkRecord,
    EntityRecord,
    MentionRecord,
    RelationshipRecord,
    SegmentRecord,
)
from helios_core.ontology.mapping import ResolutionConfig

from .anchors import (
    AnchorPlan,
    Constraints,
    LookupValue,
    Match,
    build_query,
    decide_rows,
    gather_constraints,
    key_value,
    lookups_for,
    plans,
    resolve_cluster,
    row_match,
)
from .cases import Rules, cluster_assets, parse_date
from .gazetteer import Form, Gazetteer, normalise

LOGGER = logging.getLogger(__name__)

# Tuning of single-mention resolution (how many candidates, how close a
# runner-up may be) is in the settings' ``resolution`` section.
DOCUMENT_SOURCE = "documents"
STRUCTURED = "structured"


# --- readings: candidates and constraints per mention ------------------------------------


@dataclass(frozen=True)
class Candidate:
    instance: str  # external ID, e.g. tpcds.customer:c_customer_sk=1
    class_name: str
    tier: str  # exact_key, alias or fuzzy
    score: float


@dataclass
class Reading:
    """What one mention may refer to, and what it constrains."""

    mention: MentionRecord
    candidates: list[Candidate] = field(default_factory=list)
    instances: tuple[str, ...] = ()  # every instance the name could mean (for promotion)
    class_name: str | None = None
    form: Form | None = None  # the gazetteer form behind a name
    partial: bool = False
    needs_anchor: bool = False  # a partial name or a fuzzy match: read again with anchors
    lookups: list[LookupValue] = field(default_factory=list)  # values for an anchor's lookups
    date: date | None = None
    document_id: str | None = None  # documents.<class>:<key>=<value>, a case's own ID
    document_class: str | None = None  # the class that ID identifies


def _fuzzy_ratio(a: str, b: str) -> float:
    from rapidfuzz import fuzz

    return fuzz.ratio(a, b) / 100.0


class Reader:
    """Turns mentions into readings against the gazetteer and the settings' rules."""

    def __init__(self, gazetteer: Gazetteer, config: ResolutionConfig, rules: Rules):
        self.gazetteer = gazetteer
        self.config = config
        self.rules = rules
        self.thresholds = config.thresholds
        self._names: dict[str, list[str]] | None = None

    def read(self, mention: MentionRecord, anchored: dict[str, set[str]] | None) -> Reading:
        """``anchored`` (class -> instances the asset names by key or whole name)
        is None on the first pass; partial names and fuzzy matches wait for it."""
        reading = Reading(mention)
        if mention.extractor == "contextual":
            return reading
        if mention.extractor == "gazetteer":
            form = self.gazetteer.forms.get(
                (mention.proposed_class, normalise(mention.surface_form))
            )
            if form is not None:
                self._from_form(reading, form, anchored)
            return reading
        label = self.rules.label_of(mention)
        if label is not None:
            if label.kind == "document_id":
                reading.document_id = self._document_id(mention.surface_form, label.proposed_class)
                reading.document_class = label.proposed_class if reading.document_id else None
            elif label.kind == "key":
                self._from_keys(reading, label.columns, mention)
            elif label.kind == "display":
                self._fuzzy(reading, mention, anchored)
            return reading
        if mention.extractor_detail.startswith("header:"):
            form = self.gazetteer.forms.get(
                (mention.proposed_class, normalise(mention.surface_form))
            )
            if form is not None and not form.partial:
                self._from_form(reading, form, anchored)
            else:
                self._fuzzy(reading, mention, anchored)
            return reading
        rule = self.rules.pattern_of(mention)
        if rule is None:
            return reading
        if rule.kind == "key":
            self._from_keys(reading, rule.columns, mention)
        elif rule.kind == "partial_key":
            self._lookups(reading, rule.columns, mention.surface_form.strip(), "ends_with")
        elif rule.kind == "document_id":
            reading.document_id = self._document_id(mention.surface_form, rule.proposed_class)
            reading.document_class = rule.proposed_class if reading.document_id else None
        elif rule.kind == "value":
            reading.date = parse_date(mention.surface_form, self.rules.date_formats)
        return reading

    def _document_id(self, value: str, class_name: str | None) -> str | None:
        rule = self.rules.document_rule(value.strip())
        if rule is None or not class_name:
            return None
        name = rule.key_name or rule.name
        return ids.external_id(DOCUMENT_SOURCE, class_name.lower(), {name: value.strip()})

    def _from_keys(self, reading: Reading, columns: Sequence[str], mention: MentionRecord) -> None:
        value = mention.surface_form.strip()
        forms: dict[str, Form] = {}
        for column in columns:
            form = self.gazetteer.lookup(column, value)
            if form is not None:
                forms.setdefault(form.class_name, form)
        if forms:
            chosen = forms
            if mention.proposed_class in forms:
                chosen = {mention.proposed_class: forms[mention.proposed_class]}
            reading.class_name = mention.proposed_class
            for class_name, form in sorted(chosen.items()):
                for instance in form.instances:
                    score = 1.0 / (len(chosen) * len(form.instances))
                    reading.candidates.append(Candidate(instance, class_name, "exact_key", score))
            reading.instances = tuple(i for f in chosen.values() for i in f.instances)
        else:
            self._lookups(reading, columns, value, "equals")

    def _lookups(self, reading: Reading, columns: Sequence[str], value: str, match: str) -> None:
        """A key value the dictionary does not know may still identify an anchor's
        row: it becomes a constraint for each lookup fed by one of ``columns``."""
        for lookup in lookups_for(self.config, columns, match):
            if lookup.type == "integer" and not value.isdigit():
                continue
            reading.lookups.append(LookupValue(lookup.column, match, value, lookup.identifies))

    def _from_form(
        self, reading: Reading, form: Form, anchored: dict[str, set[str]] | None
    ) -> None:
        reading.class_name = form.class_name
        reading.form = form
        reading.partial = form.partial
        instances = form.instances
        if form.kind == "key":
            tier, factor = "exact_key", 1.0
        else:
            tier = "alias"
            if form.partial:
                if anchored is None:
                    reading.needs_anchor = True
                    return
                known = anchored.get(form.class_name, set())
                instances = tuple(i for i in form.instances if i in known)
                factor = 1.0  # the anchor confirms the name
            else:
                factor = self.rules.tuning.low_specificity_factor if form.low_specificity else 1.0
        reading.instances = instances
        if 0 < len(instances) <= self.rules.tuning.max_candidates:
            score = factor / len(instances)
            reading.candidates = [Candidate(i, form.class_name, tier, score) for i in instances]

    def _fuzzy(
        self, reading: Reading, mention: MentionRecord, anchored: dict[str, set[str]] | None
    ) -> None:
        """Typo tolerance for a labelled name: rapidfuzz ratio against the display
        names of the instances the asset anchors."""
        if anchored is None:
            reading.needs_anchor = True
            return
        class_name = mention.proposed_class or ""
        known = sorted(anchored.get(class_name, ()))
        if not known:
            return
        reading.class_name = class_name
        surface = normalise(mention.surface_form)
        for instance in known:
            best = max(
                (_fuzzy_ratio(surface, name) for name in self._display_names().get(instance, ())),
                default=0.0,
            )
            if best >= self.thresholds.fuzzy_min_score:
                reading.candidates.append(Candidate(instance, class_name, "fuzzy", round(best, 4)))
        reading.instances = tuple(c.instance for c in reading.candidates)

    def _display_names(self) -> dict[str, list[str]]:
        if self._names is None:
            names: dict[str, list[str]] = defaultdict(list)
            for form in self.gazetteer.forms.values():
                if form.kind == "display" and not form.partial:
                    for instance in form.instances:
                        names[instance].append(form.surface)
            self._names = dict(names)
        return self._names


def anchors_of(readings: Iterable[Reading]) -> dict[str, set[str]]:
    """class -> instances named by a key or a whole name (first-pass readings)."""
    anchored: dict[str, set[str]] = defaultdict(set)
    for reading in readings:
        if reading.partial:
            continue
        for candidate in reading.candidates:
            anchored[candidate.class_name].add(candidate.instance)
        if reading.instances and reading.class_name:
            anchored[reading.class_name].update(reading.instances)
    return anchored


def read_all(mentions: Sequence[MentionRecord], reader: Reader) -> dict[str, Reading]:
    """Every mention's reading, with partial names and fuzzy matches read against
    the instances their asset anchors."""
    readings = {m.mention_id: reader.read(m, None) for m in mentions}
    by_asset: dict[str, list[Reading]] = defaultdict(list)
    for reading in readings.values():
        by_asset[reading.mention.asset_id].append(reading)
    for asset_readings in by_asset.values():
        pending = [r for r in asset_readings if r.needs_anchor]
        if not pending:
            continue
        anchored = anchors_of(r for r in asset_readings if not r.needs_anchor)
        for reading in pending:
            readings[reading.mention.mention_id] = reader.read(reading.mention, anchored)
    return readings


# --- links, entities and relationships -------------------------------------------------------


@dataclass
class Link:
    mention: MentionRecord
    target: str  # the entity's primary external ID
    class_name: str
    link_type: str
    resolved_by: str
    score: float
    evidence: list[str]


@dataclass
class Resolution:
    entities: list[EntityRecord]
    links: list[EntityLinkRecord]
    relationships: list[RelationshipRecord]
    clusters: dict[str, list[str]]
    queried: int
    resolved: int

    @property
    def counts(self) -> dict[str, int]:
        return {
            "entities": len(self.entities),
            "links_sameas": sum(1 for l in self.links if l.link_type == "SameAs"),
            "links_possible": sum(1 for l in self.links if l.link_type == "PossiblySameAs"),
            "relationships": len(self.relationships),
            "cases": len(self.clusters),
            "cases_queried": self.queried,
            "cases_resolved": self.resolved,
        }


def threshold_of(tier: str, config: ResolutionConfig) -> float:
    if tier == "alias":
        return config.thresholds.alias_min_score
    if tier == "fuzzy":
        return config.thresholds.fuzzy_min_score
    return 1.0  # exact_key: the value must name exactly one instance


def decide_links(
    reading: Reading, config: ResolutionConfig, tuning: ResolutionRules | None = None
) -> list[Link]:
    """Step 10 for a mention the joint query did not settle."""
    if not reading.candidates:
        return []
    ranked = sorted(reading.candidates, key=lambda c: (-c.score, c.class_name, c.instance))
    best = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    evidence = [reading.mention.segment_id]
    tuning = tuning or ResolutionRules()
    if best.score >= threshold_of(best.tier, config) and best.score - runner_up > tuning.close:
        return [
            Link(
                reading.mention,
                best.instance,
                best.class_name,
                "SameAs",
                best.tier,
                round(best.score, 4),
                evidence,
            )
        ]
    return [
        Link(
            reading.mention,
            c.instance,
            c.class_name,
            "PossiblySameAs",
            c.tier,
            round(c.score, 4),
            evidence,
        )
        for c in ranked[: tuning.top_possible]
    ]


def _document_rank(external_id: str, rules: Rules) -> tuple[int, str]:
    """Document identifiers in the order the settings list them for linking cases."""
    name = next(iter(key_value(external_id)), "")
    for i, rule in enumerate(rules.strong):
        if rules.key_name(rule) == name:
            return i, external_id
    return len(rules.strong), external_id


def document_entities(
    readings: Sequence[Reading], rules: Rules
) -> tuple[dict[str, str], dict[str, list[str]], bool]:
    """Group a cluster's document IDs into case entities: (document id -> entity
    key, entity key -> its document ids, ambiguous). One primary ID (an RMA)
    in the cluster owns every document ID; several primary IDs each own the
    IDs of their own assets, and the cluster is ambiguous for joint resolution."""
    by_asset: dict[str, set[str]] = defaultdict(set)
    for reading in readings:
        if reading.document_id:
            by_asset[reading.mention.asset_id].add(reading.document_id)
    all_ids = sorted(
        {d for found in by_asset.values() for d in found}, key=lambda d: _document_rank(d, rules)
    )
    if not all_ids:
        return {}, {}, False
    top_rank = _document_rank(all_ids[0], rules)[0]
    primaries = [d for d in all_ids if _document_rank(d, rules)[0] == top_rank]
    owner: dict[str, str] = {}
    if len(primaries) == 1:
        for d in all_ids:
            owner[d] = primaries[0]
        return owner, {primaries[0]: all_ids}, False
    for found in by_asset.values():
        ordered = sorted(found, key=lambda d: _document_rank(d, rules))
        head = ordered[0]
        for d in ordered:
            owner.setdefault(d, head if _document_rank(head, rules)[0] == top_rank else d)
    members: dict[str, list[str]] = defaultdict(list)
    for d, key in owner.items():
        if d not in members[key]:
            members[key].append(d)
    return (
        owner,
        {k: sorted(v, key=lambda d: _document_rank(d, rules)) for k, v in members.items()},
        True,
    )


def promote(
    readings: Sequence[Reading], match: Match, evidence: list[str]
) -> dict[str, list[Link]]:
    """Step 8's promotion: every mention in the cluster that can name an
    identified instance becomes SameAs (joint). The anchor row's own instances
    win over a related record's when a name could mean either."""
    links: dict[str, list[Link]] = {}
    identified = match.instances
    anchor = match.anchor
    assert anchor.key is not None
    preferred = {external for external, _ in anchor.links.values()} | {anchor.key}
    by_class = {record.class_name: record for record in match.records}
    for reading in readings:
        m = reading.mention
        target: str | None = None
        class_of = {c.instance: c.class_name for c in reading.candidates}
        class_name = reading.class_name or m.proposed_class or ""
        named = set(reading.instances) | set(class_of)
        colocated = match.colocated.get(class_name)
        if (
            colocated is not None
            and reading.form is not None
            and reading.form.kind == "display"
            and normalise(m.surface_form) == normalise(colocated[1])
        ):
            named.add(colocated[0])  # the row's own name for it, as the document wrote it
        hit = named & identified
        if hit:
            target = min(hit & preferred) if hit & preferred else min(hit)
            class_name = class_of.get(target, class_name)
        elif reading.document_id:
            target, class_name = anchor.key, anchor.class_name
        elif reading.lookups:
            # A value for one of the anchor's lookups (a full number, or its tail),
            # when it agrees with the identified row, names one of the case's records:
            # the one the mention proposes, else the one the lookup says it identifies.
            found = reading.lookups[0]
            actual = match.values.get(found.column)
            consistent = actual is not None and (
                actual == found.value if found.match == "equals" else actual.endswith(found.value)
            )
            if consistent:
                record = by_class.get(m.proposed_class or "") or by_class.get(
                    found.identifies or ""
                )
                if record is not None and record.key is not None:
                    target, class_name = record.key, record.class_name
        if target is not None:
            links[m.mention_id] = [Link(m, target, class_name, "SameAs", "joint", 1.0, evidence)]
    return links


def contextual_links(
    readings: Sequence[Reading],
    links: dict[str, list[Link]],
    clusters: dict[str, list[str]],
) -> dict[str, list[Link]]:
    """Step 9: a definite phrase links when its asset, else its cluster, has
    exactly one SameAs instance of the class."""
    cluster_of = {a: c for c, members in clusters.items() for a in members}
    by_asset: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    by_cluster: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for found in links.values():
        for link in found:
            if link.link_type == "SameAs":
                by_asset[link.mention.asset_id][link.class_name].add(link.target)
                by_cluster[cluster_of.get(link.mention.asset_id, "")][link.class_name].add(
                    link.target
                )
    added: dict[str, list[Link]] = {}
    for reading in readings:
        m = reading.mention
        if m.extractor != "contextual" or not m.proposed_class:
            continue
        for scope in (by_asset[m.asset_id], by_cluster[cluster_of.get(m.asset_id, "")]):
            targets = scope.get(m.proposed_class, set())
            if len(targets) == 1:
                [target] = targets
                added[m.mention_id] = [
                    Link(m, target, m.proposed_class, "SameAs", "contextual", 1.0, [m.segment_id])
                ]
                break
            if targets:
                break  # several in the asset: the cluster cannot narrow it down
    return added


class EntityBook:
    """The entities links and relationships refer to, with their names and keys."""

    def __init__(self, gazetteer: Gazetteer, config: ResolutionConfig, source: str):
        self.gazetteer = gazetteer
        self.config = config
        self.source = source
        self.classes: dict[str, str] = {}
        self.external: dict[str, list[str]] = defaultdict(list)
        self.names: dict[str, str] = {}
        self._written: dict[tuple[str, str], str] = {}
        self._forms_by_instance: dict[str, list[Form]] | None = None

    def note_written(self, mentions: Iterable[MentionRecord]) -> None:
        for m in sorted(mentions, key=lambda m: m.mention_id):
            if m.proposed_class and m.extractor in ("pattern", "gazetteer"):
                self._written.setdefault(
                    (m.proposed_class, normalise(m.surface_form)), m.surface_form
                )

    def add(
        self, key: str, class_name: str, extra_ids: Iterable[str] = (), name: str | None = None
    ) -> str:
        """Register the entity with primary external ID ``key``; returns its entity_id."""
        self.classes.setdefault(key, class_name)
        known = self.external[key]
        for external in (key, *extra_ids):
            if external not in known:
                known.append(external)
        if name:
            self.names.setdefault(key, name)
        return ids.entity_id(class_name, key)

    def display_ids(self, reading: Reading, target: str) -> list[str]:
        """For a class with no secondary key (Brand), the display name a mention
        used is the entity's other external ID: the warehouse row's name when
        the case identified it, else the name as written."""
        form = reading.form
        identifiers = self.config.classes.get(reading.class_name or "")
        if form is None or form.kind != "display" or identifiers is None or identifiers.secondary:
            return []
        if target not in form.instances:
            return []
        return [
            ids.external_id(
                self.source, identifiers.ossie_element, {form.columns: reading.mention.surface_form}
            )
        ]

    def entity_id(self, key: str) -> str:
        return ids.entity_id(self.classes[key], key)

    def _forms(self, instance: str) -> list[Form]:
        if self._forms_by_instance is None:
            index: dict[str, list[Form]] = defaultdict(list)
            needed = set(self.classes)
            for form in self.gazetteer.forms.values():
                if form.partial or form.kind == "template":
                    continue
                for i in form.instances:
                    if i in needed:
                        index[i].append(form)
            self._forms_by_instance = dict(index)
        return self._forms_by_instance.get(instance, [])

    def records(self, run: CrawlRunRecord) -> list[EntityRecord]:
        result = []
        for key in sorted(self.classes):
            class_name = self.classes[key]
            externals = list(self.external[key])
            name = None
            secondary = None
            identifiers = self.config.classes.get(class_name)
            for form in sorted(self._forms(key), key=lambda f: (f.kind, f.columns, f.surface)):
                written = self._written.get((class_name, form.surface), form.surface)
                if form.kind == "key" and identifiers is not None:
                    external = ids.external_id(
                        self.source, identifiers.ossie_element, {form.columns: written}
                    )
                    if external not in externals:
                        externals.append(external)
                    if secondary is None:
                        secondary = written
                elif form.kind == "display" and name is None:
                    name = self.names.get(key, written)
            canonical = name or self.names.get(key) or _fallback_name(key, class_name, externals)
            if name and secondary:
                canonical = f"{name} ({secondary})"
            result.append(
                EntityRecord(
                    crawl_run_id=run.crawl_run_id,
                    entity_id=self.entity_id(key),
                    ontology_class=class_name,
                    canonical_name=canonical,
                    external_ids=externals,
                    ontology_version=run.ontology_version,
                )
            )
        return result


def _fallback_name(key: str, class_name: str, externals: Sequence[str]) -> str:
    documents = [e for e in externals if e.startswith(DOCUMENT_SOURCE + ".")]
    if documents:
        values = [next(iter(key_value(d).values()), d) for d in documents]
        return values[0] if len(values) == 1 else f"{values[0]} ({', '.join(values[1:])})"
    parts = key_value(key)
    return (
        f"{class_name} " + " ".join(f"{k}={v}" for k, v in sorted(parts.items())) if parts else key
    )


def about_target(
    asset_links: Sequence[Link],
    segment_types: dict[str, str],
    case_entity: str | None,
    about: About | None = None,
) -> str | None:
    """Step 11's About: the case's own entity when known; else the entity named
    in the document's title; else the most mentioned, ties by class priority
    (``about`` is the settings' section of that name)."""
    about = about or About()
    order = list(about.class_priority)
    if case_entity is not None:
        return case_entity
    same_as = [l for l in asset_links if l.link_type == "SameAs"]
    if not same_as:
        return None

    def priority(class_name: str) -> int:
        return order.index(class_name) if class_name in order else len(order)

    titled = [
        l for l in same_as if segment_types.get(l.mention.segment_id) in about.title_segments
    ]
    if titled:
        return min(titled, key=lambda l: (priority(l.class_name), l.target)).target
    counts = Counter(l.target for l in same_as)
    class_of = {l.target: l.class_name for l in same_as}
    return min(counts, key=lambda t: (-counts[t], priority(class_of[t]), t))


# --- the whole step --------------------------------------------------------------------


def resolve(
    run: CrawlRunRecord,
    assets: Sequence[AssetRecord],
    segments: Sequence[SegmentRecord],
    mentions: Sequence[MentionRecord],
    gazetteer: Gazetteer,
    config: ResolutionConfig,
    settings: CrawlerSettings,
    warehouse_cursor: Callable[[], Any] | None = None,
) -> Resolution:
    """Entities, links and relationships for one run's mentions. Joint resolution
    needs ``warehouse_cursor`` (a read-only cursor factory); without it, cases
    are still clustered and candidates decided by their tiers."""
    rules = Rules(settings)
    reader = Reader(gazetteer, config, rules)
    mentions = sorted(mentions, key=lambda m: m.mention_id)
    readings = read_all(mentions, reader)
    clusters = cluster_assets(assets, mentions, rules, gazetteer)
    by_asset: dict[str, list[Reading]] = defaultdict(list)
    for reading in readings.values():
        by_asset[reading.mention.asset_id].append(reading)
    anchor_plans = plans(config)
    cursor = warehouse_cursor() if warehouse_cursor is not None and anchor_plans else None

    book = EntityBook(gazetteer, config, config.database)
    book.note_written(mentions)
    links: dict[str, list[Link]] = {}
    case_entity: dict[str, str] = {}  # cluster -> the key of the entity the case is about
    matches: dict[str, tuple[Match, list[str]]] = {}
    queried = 0
    for cluster_id, members in clusters.items():
        cluster_readings = [r for a in members for r in by_asset.get(a, [])]
        owner, documents, ambiguous = document_entities(cluster_readings, rules)
        document_class = {
            r.document_id: r.document_class or ""
            for r in sorted(cluster_readings, key=lambda r: r.mention.mention_id)
            if r.document_id
        }
        match: Match | None = None
        evidence: list[str] = []
        if cursor is not None and not ambiguous:
            for plan in anchor_plans:  # the first anchor whose row the documents identify
                match, constraints, did_query = resolve_cluster(plan, cluster_readings, cursor)
                queried += int(did_query)
                evidence = sorted(constraints.segments)
                if match is not None:
                    break
        if match is not None and match.anchor.key is not None:
            document_ids = sorted(owner, key=lambda d: _document_rank(d, rules))
            book.add(match.anchor.key, match.anchor.class_name, document_ids)
            for record in match.related:
                if record.key:
                    book.add(record.key, record.class_name)
            for record in match.records:
                for class_name, (external, _) in record.links.items():
                    book.add(external, class_name)
            for class_name, (external, shown, display_ids) in match.colocated.items():
                book.add(external, class_name, display_ids, shown)
            matches[cluster_id] = (match, evidence)
            case_entity[cluster_id] = match.anchor.key
            links.update(promote(cluster_readings, match, evidence))
        else:
            for key, members_ids in documents.items():
                book.add(key, document_class[key], members_ids)
            if len(documents) == 1:
                case_entity[cluster_id] = next(iter(documents))
        for reading in cluster_readings:
            m = reading.mention
            if m.mention_id in links:
                continue
            if reading.document_id and reading.document_id in owner:
                owned_by = owner[reading.document_id]
                links[m.mention_id] = [
                    Link(
                        m,
                        owned_by,
                        document_class[owned_by],
                        "SameAs",
                        "exact_key",
                        1.0,
                        [m.segment_id],
                    )
                ]
                continue
            decided = decide_links(reading, config, rules.tuning)
            if decided:
                links[m.mention_id] = decided
    links.update(contextual_links(list(readings.values()), links, clusters))
    for mention_id, found in links.items():
        for link in found:
            book.add(
                link.target, link.class_name, book.display_ids(readings[mention_id], link.target)
            )

    link_records: list[EntityLinkRecord] = []
    for mention_id in sorted(links):
        for link in links[mention_id]:
            entity_id = book.entity_id(link.target)
            link_records.append(
                EntityLinkRecord(
                    crawl_run_id=run.crawl_run_id,
                    link_id=ids.link_id(mention_id, entity_id),
                    mention_id=mention_id,
                    entity_id=entity_id,
                    link_type=link.link_type,
                    resolved_by=link.resolved_by,
                    score=link.score,
                    evidence_segment_ids=sorted(set(link.evidence)),
                    ontology_version=run.ontology_version,
                )
            )

    relationships = derive_relationships(
        run, assets, segments, links, clusters, case_entity, matches, book, rules.about
    )
    LOGGER.info(
        "resolution: %d clusters, %d queried, %d resolved uniquely",
        len(clusters),
        queried,
        len(matches),
    )
    return Resolution(
        entities=book.records(run),
        links=link_records,
        relationships=relationships,
        clusters=clusters,
        queried=queried,
        resolved=len(matches),
    )


def derive_relationships(
    run: CrawlRunRecord,
    assets: Sequence[AssetRecord],
    segments: Sequence[SegmentRecord],
    links: dict[str, list[Link]],
    clusters: dict[str, list[str]],
    case_entity: dict[str, str],
    matches: dict[str, tuple[Match, list[str]]],
    book: EntityBook,
    about: About | None = None,
) -> list[RelationshipRecord]:
    """Step 11: Mentions and About per asset, structural edges per identified row."""
    records: dict[str, RelationshipRecord] = {}

    def add(
        rtype: str,
        skind: str,
        sid: str,
        tkind: str,
        tid: str,
        by: str,
        confidence: float,
        evidence: Iterable[str],
    ) -> None:
        rid = ids.relationship_id(rtype, sid, tid)
        existing = records.get(rid)
        if existing is not None and existing.confidence >= confidence:
            return
        records[rid] = RelationshipRecord(
            crawl_run_id=run.crawl_run_id,
            relationship_id=rid,
            relationship_type=rtype,
            source_kind=skind,
            source_id=sid,
            target_kind=tkind,
            target_id=tid,
            resolved_by=by,
            confidence=confidence,
            evidence_segment_ids=sorted(set(evidence)),
            ontology_version=run.ontology_version,
        )

    segment_types = {s.segment_id: s.segment_type for s in segments}
    cluster_of = {a: c for c, members in clusters.items() for a in members}
    by_asset: dict[str, list[Link]] = defaultdict(list)
    for found in links.values():
        for link in found:
            by_asset[link.mention.asset_id].append(link)
    for asset in sorted(assets, key=lambda a: a.asset_id):
        asset_links = by_asset.get(asset.asset_id, [])
        for link in sorted(asset_links, key=lambda l: (l.target, -l.score, l.mention.mention_id)):
            if link.link_type == "SameAs":
                add(
                    "Mentions",
                    "asset",
                    asset.asset_id,
                    "entity",
                    book.entity_id(link.target),
                    link.resolved_by,
                    link.score,
                    link.evidence,
                )
        cluster_id = cluster_of.get(asset.asset_id, "")
        target = about_target(asset_links, segment_types, case_entity.get(cluster_id), about)
        if target is not None and target in book.classes:
            evidence = [l.mention.segment_id for l in asset_links if l.target == target]
            if cluster_id in matches:
                by = "joint"
            elif target.startswith(DOCUMENT_SOURCE + "."):
                by = "exact_key"
            else:
                by = "heuristic"  # the subject's entity, else the most mentioned
            add(
                "About",
                "asset",
                asset.asset_id,
                "entity",
                book.entity_id(target),
                by,
                1.0,
                evidence,
            )
    for cluster_id in sorted(matches):
        match, evidence = matches[cluster_id]
        anchor = match.anchor
        assert anchor.key is not None
        anchor_entity = book.entity_id(anchor.key)
        for record in match.related:
            if not record.key or not record.edge:
                continue
            record_entity = book.entity_id(record.key)
            add(record.edge, "entity", anchor_entity, "entity", record_entity, STRUCTURED, 1.0, evidence)
            for class_name, (external, edge) in record.links.items():
                if record.share_anchor_entities:
                    # The documents name one party and place for the whole case: a
                    # related row's own foreign keys stand in only for a class the
                    # anchor row does not name.
                    external = anchor.links.get(class_name, (external, edge))[0]
                add(
                    edge,
                    "entity",
                    record_entity,
                    "entity",
                    book.entity_id(external),
                    STRUCTURED,
                    1.0,
                    evidence,
                )
        for external, edge in anchor.links.values():
            add(
                edge,
                "entity",
                anchor_entity,
                "entity",
                book.entity_id(external),
                STRUCTURED,
                1.0,
                evidence,
            )
    return [records[rid] for rid in sorted(records)]


__all__ = [
    "AnchorPlan",
    "Candidate",
    "Constraints",
    "Link",
    "Match",
    "Reader",
    "Reading",
    "Resolution",
    "build_query",
    "decide_links",
    "decide_rows",
    "gather_constraints",
    "key_value",
    "promote",
    "read_all",
    "resolve",
    "resolve_cluster",
    "row_match",
]
