"""Steps 6-11 of the analysis (docs/crawler-analysis.md): resolve mentions to
warehouse instances, jointly per case, and derive relationships.

Per mention, **candidates** are scored by tier (step 6): ``exact_key`` for keys
the gazetteer knows, ``alias`` for names (score = the form's specificity),
``fuzzy`` for a labelled name with a typo (rapidfuzz against the names the asset
anchors). Ticket numbers, receipt tails and dates are not candidates but
*constraints*. Document IDs (an RMA number) name the case's Return before its
warehouse row is known.

Per **case cluster** (``cases.py``), the constraints are combined into one
parameterised query against the source's return table, joined along the
mapping's relationships (step 8). Exactly one row identifies the Return, the
Sale, and their customer, item, store and reason; every mention in the cluster
whose candidates (or whose name's instances) include an identified instance is
promoted to SameAs with ``resolved_by = "joint"``. No row, or several, promotes
nothing. The join between returns and sales on (ticket number, item), and the
date dimension, are the declared exceptions to "join paths come from the mapping":
TPC-DS has no foreign key for them.

Links are decided per tier threshold (step 10): SameAs at or above the tier's
threshold with no close runner-up, else PossiblySameAs for the top candidates.
A low-specificity name never becomes SameAs on its own. Contextual references
("the item") link when the asset, else its cluster, has exactly one resolved
instance of the class (step 9). Relationships (step 11): Mentions and About per
asset, and the structural edges of the identified warehouse row.

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

from helios_core.crawler.settings import CrawlerSettings
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
from helios_core.ontology.mapping import ClassIdentifiers, ResolutionConfig

from .cases import Rules, cluster_assets, parse_date
from .gazetteer import Form, Gazetteer, has_names, normalise

LOGGER = logging.getLogger(__name__)

MAX_CANDIDATES = 20  # a name shared by more instances is an unresolved alias
CLOSE = 0.05  # a runner-up within this of the best candidate blocks SameAs
LOW_SPECIFICITY_FACTOR = 0.5  # an ordinary-word name can never reach the alias threshold alone
TOP_POSSIBLE = 3
ROW_LIMIT = 5  # more consistent warehouse rows than this: promote nothing
DOCUMENT_SOURCE = "documents"
DOCUMENT_ID_NAMES = {"return_authorization": "rma", "support_case": "case"}
RETURN_CLASS = "Return"
SALE_CLASS = "Sale"
RETURN_OF = "ReturnOf"
STRUCTURED = "structured"
# The declared exceptions: TPC-DS joins dates through surrogate keys the mapping
# has no class for.
DATE_JOINS = {
    "store_returns": ("sr_returned_date_sk", "date_dim", "d_date_sk", "d_date"),
    "store_sales": ("ss_sold_date_sk", "date_dim", "d_date_sk", "d_date"),
}
CLASS_PRIORITY = ("Return", "Item", "Customer", "Sale", "Store", "Brand", "Reason")
_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
_KEY_VALUE = re.compile(r"^[^:]+:(.+)$")


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
    ticket: str | None = None
    ticket_tail: str | None = None
    date: date | None = None
    document_id: str | None = None  # documents.return:rma=RMA-..., a case's own ID


def key_value(external_id: str) -> dict[str, str]:
    """The key columns of an external ID: tpcds.customer:c_customer_sk=1 -> {c_customer_sk: 1}."""
    match = _KEY_VALUE.match(external_id)
    if match is None:
        return {}
    pairs = (part.split("=", 1) for part in match.group(1).split(",") if "=" in part)
    return {name: value for name, value in pairs}


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
        # Key columns of classes known by keys only (Sale, Return): ticket numbers.
        self.transaction_columns = {
            column
            for identifiers in config.classes.values()
            if not has_names(identifiers)
            for column in (*identifiers.primary, *identifiers.secondary)
        }
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
            tail = mention.surface_form.strip()
            reading.ticket_tail = tail if tail.isdigit() else None
        elif rule.kind == "document_id":
            reading.document_id = self._document_id(mention.surface_form, rule.proposed_class)
        elif rule.kind == "value":
            reading.date = parse_date(mention.surface_form)
        return reading

    def _document_id(self, value: str, class_name: str | None) -> str | None:
        rule = self.rules.document_rule(value.strip())
        if rule is None or not class_name:
            return None
        name = DOCUMENT_ID_NAMES.get(rule.name, rule.name)
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
        elif set(columns) & self.transaction_columns and value.isdigit():
            reading.ticket = value

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
                factor = LOW_SPECIFICITY_FACTOR if form.low_specificity else 1.0
        reading.instances = instances
        if 0 < len(instances) <= MAX_CANDIDATES:
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


# --- the joint query against the warehouse -------------------------------------------------


@dataclass(frozen=True)
class JoinPath:
    column: str  # the foreign key on the fact table
    target: ClassIdentifiers
    edge: str  # the ontology relationship class


@dataclass(frozen=True)
class Colocated:
    """A class living on a joined table under another name (Brand on item): the
    joined row identifies it too, by its primary key and its display columns."""

    path: JoinPath  # the return table's path to the table
    target: ClassIdentifiers
    alias: str


@dataclass(frozen=True)
class JointSchema:
    """The return and sale tables and their join paths, from the mapping."""

    source: str
    returns: ClassIdentifiers
    sales: ClassIdentifiers
    return_paths: tuple[JoinPath, ...]
    sale_paths: tuple[JoinPath, ...]
    colocated: tuple[Colocated, ...]
    return_date: tuple[str, str, str, str] | None
    sale_date: tuple[str, str, str, str] | None

    @classmethod
    def from_config(cls, config: ResolutionConfig, source: str = "tpcds") -> JointSchema | None:
        returns, sales = config.classes.get(RETURN_CLASS), config.classes.get(SALE_CLASS)
        if returns is None or sales is None or len(returns.primary) != len(sales.primary):
            return None
        return_paths = _paths(config, returns.ossie_element)
        colocated = []
        for path in return_paths:
            for identifiers in sorted(config.classes.values(), key=lambda c: c.class_name):
                if (
                    identifiers.ossie_element == path.target.ossie_element
                    and identifiers.class_name != path.target.class_name
                    and len(identifiers.primary) == 1
                ):
                    colocated.append(Colocated(path, identifiers, f"j{len(colocated)}"))
        schema = cls(
            source=source,
            returns=returns,
            sales=sales,
            return_paths=return_paths,
            sale_paths=_paths(config, sales.ossie_element),
            colocated=tuple(colocated),
            return_date=DATE_JOINS.get(returns.ossie_element),
            sale_date=DATE_JOINS.get(sales.ossie_element),
        )
        schema.validate()
        return schema

    def validate(self) -> None:
        names = [self.source, self.returns.ossie_element, self.sales.ossie_element]
        names += [*self.returns.primary, *self.sales.primary, *self.returns.secondary]
        names += [p.column for p in (*self.return_paths, *self.sale_paths)]
        for path in self.return_paths:
            names += [path.target.ossie_element, *path.target.primary]
        for c in self.colocated:
            names += [*c.target.primary, *c.target.display]
        for join in (self.return_date, self.sale_date):
            names += list(join or ())
        for name in names:
            if not _IDENTIFIER.match(name):
                raise ValueError(f"not a plain SQL identifier: {name!r}")

    @property
    def ticket_column(self) -> str | None:
        return self.returns.secondary[0] if self.returns.secondary else None

    def path_for(self, class_name: str) -> JoinPath | None:
        for path in self.return_paths:
            if path.target.class_name == class_name:
                return path
        return None


def _paths(config: ResolutionConfig, table: str) -> tuple[JoinPath, ...]:
    """The mapping's relationships "<table>__<column>__<target>" leaving ``table``."""
    paths = []
    for name, edge in sorted(config.relationships.items()):
        parts = name.split("__")
        if len(parts) != 3 or parts[0] != table:
            continue
        column, target_table = parts[1], parts[2]
        target = _class_of_table(config, target_table, column)
        if target is not None:
            paths.append(JoinPath(column, target, edge))
    return tuple(paths)


def _class_of_table(config: ResolutionConfig, table: str, column: str) -> ClassIdentifiers | None:
    """The class whose instances the foreign key names: among the classes mapped
    to ``table`` (Item and Brand both live on item), the one whose single primary
    column matches the foreign key after their table prefixes (sr_item_sk and
    i_item_sk both end in item_sk)."""
    candidates = [c for c in config.classes.values() if c.ossie_element == table]
    suffix = column.split("_", 1)[-1]
    for identifiers in candidates:
        if len(identifiers.primary) == 1 and identifiers.primary[0].split("_", 1)[-1] == suffix:
            return identifiers
    return candidates[0] if len(candidates) == 1 else None


@dataclass
class Constraints:
    """What a cluster's documents say about its warehouse row."""

    instances: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    tickets: set[str] = field(default_factory=set)
    tails: set[str] = field(default_factory=set)
    dates: set[date] = field(default_factory=set)
    segments: set[str] = field(default_factory=set)

    def kinds(self, schema: JointSchema) -> int:
        joined = sum(1 for c, i in self.instances.items() if i and schema.path_for(c) is not None)
        return joined + int(bool(self.tickets)) + int(bool(self.tails))

    def queryable(self, schema: JointSchema) -> bool:
        return bool(self.tickets) or self.kinds(schema) >= 2


def gather_constraints(readings: Iterable[Reading]) -> Constraints:
    """Candidates per class (exact keys when there are any, else aliases),
    tickets, receipt tails and dates, with the segments that carried them."""
    constraints = Constraints()
    keyed: dict[str, set[str]] = defaultdict(set)
    aliased: dict[str, set[str]] = defaultdict(set)
    sources: dict[str, set[str]] = defaultdict(set)
    for reading in readings:
        segment = reading.mention.segment_id
        for candidate in reading.candidates:
            bucket = keyed if candidate.tier == "exact_key" else aliased
            bucket[candidate.class_name].add(candidate.instance)
            sources[candidate.class_name].add(segment)
        if reading.ticket:
            constraints.tickets.add(reading.ticket)
            constraints.segments.add(segment)
        if reading.ticket_tail:
            constraints.tails.add(reading.ticket_tail)
            constraints.segments.add(segment)
        if reading.date:
            constraints.dates.add(reading.date)
    for class_name in sorted(set(keyed) | set(aliased)):
        constraints.instances[class_name] = keyed.get(class_name) or aliased[class_name]
        constraints.segments.update(sources[class_name])
    return constraints


def build_query(schema: JointSchema, constraints: Constraints) -> tuple[str, list[Any]]:
    """One parameterised SELECT over the return table joined to the sale table
    (on the paired primary keys), its join paths and the date dimension, with a
    WHERE clause per constraint. Identifiers come from the mapping (validated);
    document values are bound as parameters."""
    r, s = "r", "s"
    select = [f"{r}.{c}" for c in schema.returns.primary]
    select += [f"{r}.{p.column}" for p in schema.return_paths]
    select += [f"{s}.{c}" for c in schema.sales.primary]
    select += [f"{s}.{p.column}" for p in schema.sale_paths]
    for c in schema.colocated:
        select += [f"{c.alias}.{column}" for column in (*c.target.primary, *c.target.display)]
    sql = f"SELECT {', '.join(select)}"
    sql += f", {'dr.' + schema.return_date[3] if schema.return_date else 'NULL'}"
    sql += f", {'ds.' + schema.sale_date[3] if schema.sale_date else 'NULL'}"
    sql += f" FROM {schema.source}.{schema.returns.ossie_element} {r}"
    on = " AND ".join(
        f"{s}.{sp} = {r}.{rp}"
        for rp, sp in zip(schema.returns.primary, schema.sales.primary, strict=True)
    )
    sql += f" LEFT JOIN {schema.source}.{schema.sales.ossie_element} {s} ON {on}"
    for c in schema.colocated:
        table, key = c.path.target.ossie_element, c.path.target.primary[0]
        sql += (
            f" LEFT JOIN {schema.source}.{table} {c.alias} ON {c.alias}.{key} = {r}.{c.path.column}"
        )
    if schema.return_date:
        fk, table, key, _ = schema.return_date
        sql += f" LEFT JOIN {schema.source}.{table} dr ON dr.{key} = {r}.{fk}"
    if schema.sale_date:
        fk, table, key, _ = schema.sale_date
        sql += f" LEFT JOIN {schema.source}.{table} ds ON ds.{key} = {s}.{fk}"
    where: list[str] = []
    params: list[Any] = []
    for class_name in sorted(constraints.instances):
        path = schema.path_for(class_name)
        instances = constraints.instances[class_name]
        if path is None or not instances:
            continue
        values = sorted(_primary_value(path.target, i) for i in instances)
        where.append(f"{r}.{path.column} IN ({', '.join('?' for _ in values)})")
        params += values
    ticket = schema.ticket_column
    if ticket and constraints.tickets:
        values = sorted(int(t) for t in constraints.tickets)
        where.append(f"{r}.{ticket} IN ({', '.join('?' for _ in values)})")
        params += values
    if ticket:
        for tail in sorted(constraints.tails):
            where.append(f"MOD({r}.{ticket}, ?) = ?")
            params += [10 ** len(tail), int(tail)]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {ROW_LIMIT + 1}"
    return sql, params


def _primary_value(identifiers: ClassIdentifiers, external_id: str) -> Any:
    value = key_value(external_id).get(identifiers.primary[0], "")
    return int(value) if value.lstrip("-").isdigit() else value


@dataclass
class Match:
    """The one warehouse row consistent with a cluster."""

    return_key: str
    sale_key: str | None
    return_links: dict[str, tuple[str, str]]  # class -> (external id, edge)
    sale_links: dict[str, tuple[str, str]]
    return_date: str | None
    sale_date: str | None
    colocated: dict[str, tuple[str, str, list[str]]] = field(default_factory=dict)
    # class -> (external id, display name, display-based external ids), e.g. Brand

    @property
    def instances(self) -> set[str]:
        found = {
            external for external, _ in (*self.return_links.values(), *self.sale_links.values())
        }
        found.add(self.return_key)
        if self.sale_key:
            found.add(self.sale_key)
        found.update(external for external, _, _ in self.colocated.values())
        return found


def _as_key(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _date_text(value: Any) -> str | None:
    return str(value)[:10] if value is not None else None


def row_match(schema: JointSchema, row: Sequence[Any]) -> Match:
    values = list(row)
    n_rp, n_rl = len(schema.returns.primary), len(schema.return_paths)
    n_sp, n_sl = len(schema.sales.primary), len(schema.sale_paths)
    return_primary = values[:n_rp]
    return_fks = values[n_rp : n_rp + n_rl]
    sale_primary = values[n_rp + n_rl : n_rp + n_rl + n_sp]
    sale_fks = values[n_rp + n_rl + n_sp : n_rp + n_rl + n_sp + n_sl]
    rest = values[n_rp + n_rl + n_sp + n_sl : -2]
    return_date, sale_date = values[-2], values[-1]
    colocated: dict[str, tuple[str, str, list[str]]] = {}
    for c in schema.colocated:
        width = len(c.target.primary) + len(c.target.display)
        own, rest = rest[:width], rest[width:]
        if own and own[0] is not None:
            shown = [str(v) for v in own[1:] if v not in (None, "")]
            display_ids = [
                ids.external_id(schema.source, c.target.ossie_element, {column: str(v)})
                for column, v in zip(c.target.display, own[1:], strict=True)
                if v not in (None, "")
            ]
            colocated[c.target.class_name] = (
                ids.external_id(
                    schema.source, c.target.ossie_element, {c.target.primary[0]: _as_key(own[0])}
                ),
                " ".join(shown),
                display_ids,
            )

    def external(identifiers: ClassIdentifiers, key: Any) -> str:
        return ids.external_id(
            schema.source, identifiers.ossie_element, {identifiers.primary[0]: _as_key(key)}
        )

    return_key = ids.external_id(
        schema.source,
        schema.returns.ossie_element,
        dict(zip(schema.returns.primary, (_as_key(v) for v in return_primary), strict=True)),
    )
    sale_key = None
    if all(v is not None for v in sale_primary):
        sale_key = ids.external_id(
            schema.source,
            schema.sales.ossie_element,
            dict(zip(schema.sales.primary, (_as_key(v) for v in sale_primary), strict=True)),
        )
    return Match(
        return_key=return_key,
        sale_key=sale_key,
        return_links={
            p.target.class_name: (external(p.target, v), p.edge)
            for p, v in zip(schema.return_paths, return_fks, strict=True)
            if v is not None
        },
        sale_links={
            p.target.class_name: (external(p.target, v), p.edge)
            for p, v in zip(schema.sale_paths, sale_fks, strict=True)
            if v is not None and sale_key is not None
        },
        return_date=_date_text(return_date),
        sale_date=_date_text(sale_date),
        colocated=colocated,
    )


def decide_rows(
    schema: JointSchema, rows: Sequence[Sequence[Any]], dates: set[date]
) -> Match | None:
    """Exactly one row identifies the case. Several rows are settled by the dates
    the documents mention (a return or sale date) when exactly one agrees."""
    matches = [row_match(schema, row) for row in rows[: ROW_LIMIT + 1]]
    if len(matches) == 1:
        return matches[0]
    if 1 < len(matches) <= ROW_LIMIT and dates:
        wanted = {d.isoformat() for d in dates}
        agreeing = [m for m in matches if m.return_date in wanted or m.sale_date in wanted]
        if len(agreeing) == 1:
            return agreeing[0]
    return None


def resolve_cluster(
    schema: JointSchema, readings: Sequence[Reading], cursor: Any
) -> tuple[Match | None, Constraints, bool]:
    """(match, constraints, queried) for one cluster: at most one query."""
    constraints = gather_constraints(readings)
    if not constraints.queryable(schema):
        return None, constraints, False
    sql, params = build_query(schema, constraints)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return decide_rows(schema, rows, constraints.dates), constraints, True


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


def decide_links(reading: Reading, config: ResolutionConfig) -> list[Link]:
    """Step 10 for a mention the joint query did not settle."""
    if not reading.candidates:
        return []
    ranked = sorted(reading.candidates, key=lambda c: (-c.score, c.class_name, c.instance))
    best = ranked[0]
    runner_up = ranked[1].score if len(ranked) > 1 else 0.0
    evidence = [reading.mention.segment_id]
    if best.score >= threshold_of(best.tier, config) and best.score - runner_up > CLOSE:
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
        for c in ranked[:TOP_POSSIBLE]
    ]


def _document_rank(external_id: str, rules: Rules) -> tuple[int, str]:
    """RMA numbers before support-case numbers: the settings' identifier order."""
    name = next(iter(key_value(external_id)), "")
    for i, rule in enumerate(rules.strong):
        if DOCUMENT_ID_NAMES.get(rule, rule) == name:
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
    identified instance becomes SameAs (joint). The return row's own instances
    win over the sale row's when a name could mean either."""
    links: dict[str, list[Link]] = {}
    identified = match.instances
    preferred = {external for external, _ in match.return_links.values()} | {match.return_key}
    return_ticket = key_value(match.return_key)
    ticket_value = next((v for k, v in return_ticket.items() if "ticket" in k), None)
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
            named.add(colocated[0])  # the row's brand name, as the document wrote it
        hit = named & identified
        if hit:
            target = min(hit & preferred) if hit & preferred else min(hit)
            class_name = class_of.get(target, class_name)
        elif reading.document_id:
            target, class_name = match.return_key, RETURN_CLASS
        elif ticket_value is not None and (reading.ticket or reading.ticket_tail):
            value = reading.ticket or reading.ticket_tail or ""
            consistent = ticket_value == value if reading.ticket else ticket_value.endswith(value)
            if consistent:
                if m.proposed_class == RETURN_CLASS:
                    target, class_name = match.return_key, RETURN_CLASS
                elif match.sale_key:
                    target, class_name = match.sale_key, SALE_CLASS
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
    asset_links: Sequence[Link], segment_types: dict[str, str], case_return: str | None
) -> str | None:
    """Step 11's About: the case's Return when known; else the entity named in
    the subject or title; else the most mentioned, ties by class priority."""
    if case_return is not None:
        return case_return
    same_as = [l for l in asset_links if l.link_type == "SameAs"]
    if not same_as:
        return None

    def priority(class_name: str) -> int:
        return (
            CLASS_PRIORITY.index(class_name)
            if class_name in CLASS_PRIORITY
            else len(CLASS_PRIORITY)
        )

    titled = [l for l in same_as if segment_types.get(l.mention.segment_id) == "email_subject"]
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
    source_schema: str = "tpcds",
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
    schema = JointSchema.from_config(config, source_schema)
    cursor = warehouse_cursor() if warehouse_cursor is not None and schema is not None else None

    book = EntityBook(gazetteer, config, source_schema)
    book.note_written(mentions)
    links: dict[str, list[Link]] = {}
    case_return: dict[str, str] = {}  # cluster -> Return entity key
    matches: dict[str, tuple[Match, list[str]]] = {}
    queried = 0
    for cluster_id, members in clusters.items():
        cluster_readings = [r for a in members for r in by_asset.get(a, [])]
        owner, documents, ambiguous = document_entities(cluster_readings, rules)
        match: Match | None = None
        evidence: list[str] = []
        if cursor is not None and schema is not None and not ambiguous:
            match, constraints, did_query = resolve_cluster(schema, cluster_readings, cursor)
            queried += int(did_query)
            evidence = sorted(constraints.segments)
        if match is not None:
            document_ids = sorted(owner, key=lambda d: _document_rank(d, rules))
            book.add(match.return_key, RETURN_CLASS, document_ids)
            if match.sale_key:
                book.add(match.sale_key, SALE_CLASS)
            for class_name, (external, _) in (
                *match.return_links.items(),
                *match.sale_links.items(),
            ):
                book.add(external, class_name)
            for class_name, (external, shown, display_ids) in match.colocated.items():
                book.add(external, class_name, display_ids, shown)
            matches[cluster_id] = (match, evidence)
            case_return[cluster_id] = match.return_key
            links.update(promote(cluster_readings, match, evidence))
        else:
            for key, members_ids in documents.items():
                book.add(key, RETURN_CLASS, members_ids)
            if len(documents) == 1:
                case_return[cluster_id] = next(iter(documents))
        for reading in cluster_readings:
            m = reading.mention
            if m.mention_id in links:
                continue
            if reading.document_id and reading.document_id in owner:
                links[m.mention_id] = [
                    Link(
                        m,
                        owner[reading.document_id],
                        RETURN_CLASS,
                        "SameAs",
                        "exact_key",
                        1.0,
                        [m.segment_id],
                    )
                ]
                continue
            decided = decide_links(reading, config)
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
        run, assets, segments, links, clusters, case_return, matches, book
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
    case_return: dict[str, str],
    matches: dict[str, tuple[Match, list[str]]],
    book: EntityBook,
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
        target = about_target(asset_links, segment_types, case_return.get(cluster_id))
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
        return_entity = book.entity_id(match.return_key)
        if match.sale_key:
            sale_entity = book.entity_id(match.sale_key)
            add(
                RETURN_OF, "entity", return_entity, "entity", sale_entity, STRUCTURED, 1.0, evidence
            )
            for class_name, (external, edge) in match.sale_links.items():
                # The documents name one customer and one store for the purchase and
                # the return alike: the sale's party and place are the case's. The
                # sale row's own foreign keys (a purchase by someone else, elsewhere)
                # stand in only for a class the return row does not name.
                external = match.return_links.get(class_name, (external, edge))[0]
                add(
                    edge,
                    "entity",
                    sale_entity,
                    "entity",
                    book.entity_id(external),
                    STRUCTURED,
                    1.0,
                    evidence,
                )
        for external, edge in match.return_links.values():
            add(
                edge,
                "entity",
                return_entity,
                "entity",
                book.entity_id(external),
                STRUCTURED,
                1.0,
                evidence,
            )
    return [records[rid] for rid in sorted(records)]


__all__ = [
    "Candidate",
    "Constraints",
    "JointSchema",
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
]
