"""Record anchors: finding the one warehouse row a case is about (step 8).

An *anchor* is a class whose row a group of documents describes, as the mapping
declares it (``helios_core.ontology.mapping.Anchor``): how values found in the
documents constrain that row (lookups, and the entities the row's foreign keys
name), its date, the classes living on joined tables, and the related records
joined to it. Nothing here knows which classes those are.

From one anchor this module builds one bounded, parameterised query:

    SELECT <anchor key>, <anchor foreign keys>,
           <each related record's key and foreign keys>,
           <colocated classes' key and name columns>, <one date per record or NULL>
    FROM <anchor table> r
    LEFT JOIN <related table> s ON <key pairs>      -- per related record
    LEFT JOIN <colocated table> jN ON ...           -- per colocated class
    LEFT JOIN <date table> dr|ds ON ...             -- per record with a joined date
    WHERE <one condition per kind of evidence>
    LIMIT <row_limit + 1>

Identifiers come from the mapping and are validated as plain names; every
value from a document is a bound parameter.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from helios_core.index import ids
from helios_core.ontology.mapping import (
    Anchor,
    ClassIdentifiers,
    DateSource,
    Lookup,
    ResolutionConfig,
)

if TYPE_CHECKING:
    from .resolution import Reading

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
ANCHOR_ALIAS = "r"


@dataclass(frozen=True)
class JoinPath:
    column: str  # the foreign key on the record's table
    target: ClassIdentifiers
    edge: str  # the ontology relationship class


@dataclass(frozen=True)
class Colocated:
    """A class living on a joined table under another name: the joined row
    identifies it too, by its primary key and its display columns."""

    path: JoinPath  # the anchor's path to the table
    target: ClassIdentifiers
    alias: str


@dataclass(frozen=True)
class RecordPlan:
    """One record of the query: the anchor, or a record related to it."""

    identifiers: ClassIdentifiers
    alias: str
    date_alias: str
    paths: tuple[JoinPath, ...]
    date: DateSource | None
    on: tuple[tuple[str, str], ...] = ()  # (anchor column, this record's column)
    edge: str | None = None  # relationship from the anchor to this record
    share_anchor_entities: bool = False

    @property
    def class_name(self) -> str:
        return self.identifiers.class_name


@dataclass(frozen=True)
class AnchorPlan:
    """An anchor resolved against the mapping's classes, ready to query."""

    source: str
    anchor: RecordPlan
    related: tuple[RecordPlan, ...]
    colocated: tuple[Colocated, ...]
    lookups: tuple[Lookup, ...]
    extra_columns: tuple[str, ...]  # lookup columns the anchor's key and joins do not select
    row_limit: int
    min_constraint_kinds: int

    @classmethod
    def from_anchor(cls, config: ResolutionConfig, anchor: Anchor) -> AnchorPlan:
        def identifiers(class_name: str) -> ClassIdentifiers:
            found = config.classes.get(class_name)
            if found is None:
                raise ValueError(f"anchor {anchor.class_name}: class {class_name} is not mapped")
            return found

        def paths(joins: Iterable[Any]) -> tuple[JoinPath, ...]:
            return tuple(JoinPath(j.column, identifiers(j.class_name), j.edge) for j in joins)

        own = RecordPlan(
            identifiers(anchor.class_name), ANCHOR_ALIAS, "dr", paths(anchor.joins), anchor.date
        )
        related = tuple(
            RecordPlan(
                identifiers(r.class_name),
                "s" if i == 0 else f"s{i}",
                "ds" if i == 0 else f"ds{i}",
                paths(r.joins),
                r.date,
                tuple(r.join_on),
                r.edge,
                r.share_anchor_entities,
            )
            for i, r in enumerate(anchor.related)
        )
        colocated = []
        for item in anchor.colocated:
            path = next((p for p in own.paths if p.target.class_name == item.via), None)
            if path is None:
                raise ValueError(
                    f"anchor {anchor.class_name}: colocated class {item.class_name} is via "
                    f"{item.via}, which is not one of the anchor's joins"
                )
            colocated.append(Colocated(path, identifiers(item.class_name), f"j{len(colocated)}"))
        selected = {*own.identifiers.primary, *(p.column for p in own.paths)}
        extra = tuple(dict.fromkeys(l.column for l in anchor.lookups if l.column not in selected))
        plan = cls(
            source=config.database,
            anchor=own,
            related=related,
            colocated=tuple(colocated),
            lookups=tuple(anchor.lookups),
            extra_columns=extra,
            row_limit=anchor.row_limit,
            min_constraint_kinds=anchor.min_constraint_kinds,
        )
        plan.validate()
        return plan

    @property
    def records(self) -> tuple[RecordPlan, ...]:
        return (self.anchor, *self.related)

    def validate(self) -> None:
        names = [self.source, *self.extra_columns, *(l.column for l in self.lookups)]
        for record in self.records:
            names += [record.identifiers.ossie_element, *record.identifiers.primary]
            names += [p.column for p in record.paths]
            names += [c for pair in record.on for c in pair]
            for path in record.paths:
                names += [path.target.ossie_element, *path.target.primary]
            if record.date is not None:
                names += [v for v in vars(record.date).values() if isinstance(v, str)]
        for c in self.colocated:
            names += [*c.target.primary, *c.target.display]
        for name in names:
            if not _IDENTIFIER.match(name):
                raise ValueError(f"not a plain SQL identifier: {name!r}")

    def path_for(self, class_name: str) -> JoinPath | None:
        """The anchor's own foreign key naming ``class_name``, if it has one."""
        for path in self.anchor.paths:
            if path.target.class_name == class_name:
                return path
        return None


def plans(config: ResolutionConfig) -> list[AnchorPlan]:
    """The mapping's anchors, in the order cases are tried against them."""
    return [AnchorPlan.from_anchor(config, anchor) for anchor in config.anchors]


def lookups_for(
    config: ResolutionConfig, columns: Iterable[str], match: str
) -> list[Lookup]:
    """The lookups a key value found for ``columns`` can feed."""
    wanted = set(columns)
    return [
        lookup
        for anchor in config.anchors
        for lookup in anchor.lookups
        if lookup.match == match and wanted & set(lookup.source_columns or [lookup.column])
    ]


# --- what a case's documents say ---------------------------------------------------------


@dataclass
class Constraints:
    """What a cluster's documents say about its warehouse row."""

    instances: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    # (column, match) -> values found for that lookup
    lookups: dict[tuple[str, str], set[str]] = field(default_factory=lambda: defaultdict(set))
    dates: set[date] = field(default_factory=set)
    segments: set[str] = field(default_factory=set)

    def kinds(self, plan: AnchorPlan) -> int:
        joined = sum(1 for c, i in self.instances.items() if i and plan.path_for(c) is not None)
        return joined + sum(1 for l in plan.lookups if self.lookups.get((l.column, l.match)))

    def queryable(self, plan: AnchorPlan) -> bool:
        """An exact lookup value is enough alone; otherwise several kinds must agree."""
        exact = any(
            l.match == "equals" and self.lookups.get((l.column, l.match)) for l in plan.lookups
        )
        return exact or self.kinds(plan) >= plan.min_constraint_kinds


def gather_constraints(readings: Iterable[Reading]) -> Constraints:
    """Candidates per class (exact keys when there are any, else aliases),
    lookup values and dates, with the segments that carried them."""
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
        for found in reading.lookups:
            constraints.lookups[(found.column, found.match)].add(found.value)
            constraints.segments.add(segment)
        if reading.date:
            constraints.dates.add(reading.date)
    for class_name in sorted(set(keyed) | set(aliased)):
        constraints.instances[class_name] = keyed.get(class_name) or aliased[class_name]
        constraints.segments.update(sources[class_name])
    return constraints


# --- the query -----------------------------------------------------------------------------


def key_value(external_id: str) -> dict[str, str]:
    """The key columns of an external ID: db.table:a=1,b=2 -> {a: 1, b: 2}."""
    _, _, rendered = external_id.partition(":")
    pairs = (part.split("=", 1) for part in rendered.split(",") if "=" in part)
    return {name: value for name, value in pairs}


def _primary_value(identifiers: ClassIdentifiers, external_id: str) -> Any:
    value = key_value(external_id).get(identifiers.primary[0], "")
    return int(value) if value.lstrip("-").isdigit() else value


def _date_select(record: RecordPlan) -> str:
    if record.date is None:
        return "NULL"
    if record.date.table:
        return f"{record.date_alias}.{record.date.value}"
    return f"{record.alias}.{record.date.column}"


def build_query(plan: AnchorPlan, constraints: Constraints) -> tuple[str, list[Any]]:
    """One parameterised SELECT over the anchor's table joined to its related
    records, its colocated classes' tables and the date tables, with a WHERE
    clause per constraint."""
    r = plan.anchor.alias
    select: list[str] = []
    for record in plan.records:
        select += [f"{record.alias}.{c}" for c in record.identifiers.primary]
        select += [f"{record.alias}.{p.column}" for p in record.paths]
    for c in plan.colocated:
        select += [f"{c.alias}.{column}" for column in (*c.target.primary, *c.target.display)]
    sql = f"SELECT {', '.join(select)}"
    for record in plan.records:
        sql += f", {_date_select(record)}"
    for column in plan.extra_columns:
        sql += f", {r}.{column}"
    sql += f" FROM {plan.source}.{plan.anchor.identifiers.ossie_element} {r}"
    for record in plan.related:
        on = " AND ".join(f"{record.alias}.{theirs} = {r}.{ours}" for ours, theirs in record.on)
        sql += f" LEFT JOIN {plan.source}.{record.identifiers.ossie_element} {record.alias} ON {on}"
    for c in plan.colocated:
        table, key = c.path.target.ossie_element, c.path.target.primary[0]
        sql += (
            f" LEFT JOIN {plan.source}.{table} {c.alias} ON {c.alias}.{key} = {r}.{c.path.column}"
        )
    for record in plan.records:
        if record.date is not None and record.date.table:
            d = record.date
            sql += (
                f" LEFT JOIN {plan.source}.{d.table} {record.date_alias} "
                f"ON {record.date_alias}.{d.key} = {record.alias}.{d.column}"
            )
    where: list[str] = []
    params: list[Any] = []
    for class_name in sorted(constraints.instances):
        path = plan.path_for(class_name)
        instances = constraints.instances[class_name]
        if path is None or not instances:
            continue
        values = sorted(_primary_value(path.target, i) for i in instances)
        where.append(f"{r}.{path.column} IN ({', '.join('?' for _ in values)})")
        params += values
    for lookup in plan.lookups:
        found = constraints.lookups.get((lookup.column, lookup.match))
        if not found:
            continue
        numeric = lookup.type == "integer"
        if lookup.match == "equals":
            values = sorted(int(v) for v in found) if numeric else sorted(found)
            where.append(f"{r}.{lookup.column} IN ({', '.join('?' for _ in values)})")
            params += values
        else:
            for tail in sorted(found):
                if numeric:
                    where.append(f"MOD({r}.{lookup.column}, ?) = ?")
                    params += [10 ** len(tail), int(tail)]
                else:
                    where.append(f"{r}.{lookup.column} LIKE ?")
                    params.append(f"%{tail}")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" LIMIT {plan.row_limit + 1}"
    return sql, params


# --- the row -------------------------------------------------------------------------------


@dataclass
class Record:
    """One record of the identified row: the anchor's own, or a related one."""

    class_name: str
    key: str | None  # its external ID; None when the related row does not exist
    links: dict[str, tuple[str, str]]  # class -> (external id, edge)
    date: str | None
    edge: str | None = None  # relationship from the anchor to this record
    share_anchor_entities: bool = False


@dataclass
class Match:
    """The one warehouse row consistent with a cluster."""

    anchor: Record
    related: list[Record] = field(default_factory=list)
    colocated: dict[str, tuple[str, str, list[str]]] = field(default_factory=dict)
    # class -> (external id, display name, display-based external ids)
    values: dict[str, str] = field(default_factory=dict)  # anchor column -> its value, as text

    @property
    def records(self) -> list[Record]:
        return [self.anchor, *self.related]

    @property
    def instances(self) -> set[str]:
        found = {external for record in self.records for external, _ in record.links.values()}
        found.update(record.key for record in self.records if record.key)
        found.update(external for external, _, _ in self.colocated.values())
        return found


def _as_key(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _date_text(value: Any) -> str | None:
    return str(value)[:10] if value is not None else None


def row_match(plan: AnchorPlan, row: Sequence[Any]) -> Match:
    values = list(row)
    at = 0

    def take(count: int) -> list[Any]:
        nonlocal at
        taken = values[at : at + count]
        at += count
        return taken

    def external(identifiers: ClassIdentifiers, key: Any) -> str:
        return ids.external_id(
            plan.source, identifiers.ossie_element, {identifiers.primary[0]: _as_key(key)}
        )

    parts = [
        (record, take(len(record.identifiers.primary)), take(len(record.paths)))
        for record in plan.records
    ]
    colocated: dict[str, tuple[str, str, list[str]]] = {}
    for c in plan.colocated:
        own = take(len(c.target.primary) + len(c.target.display))
        if own and own[0] is not None:
            shown = [str(v) for v in own[1:] if v not in (None, "")]
            display_ids = [
                ids.external_id(plan.source, c.target.ossie_element, {column: str(v)})
                for column, v in zip(c.target.display, own[1:], strict=True)
                if v not in (None, "")
            ]
            colocated[c.target.class_name] = (
                ids.external_id(
                    plan.source, c.target.ossie_element, {c.target.primary[0]: _as_key(own[0])}
                ),
                " ".join(shown),
                display_ids,
            )
    dates = take(len(plan.records))
    extras = take(len(plan.extra_columns))

    records: list[Record] = []
    for (record, primary, foreign), day in zip(parts, dates, strict=True):
        present = record is plan.anchor or all(v is not None for v in primary)
        key = (
            ids.external_id(
                plan.source,
                record.identifiers.ossie_element,
                dict(zip(record.identifiers.primary, (_as_key(v) for v in primary), strict=True)),
            )
            if present
            else None
        )
        records.append(
            Record(
                class_name=record.class_name,
                key=key,
                links={
                    p.target.class_name: (external(p.target, v), p.edge)
                    for p, v in zip(record.paths, foreign, strict=True)
                    if v is not None and present
                },
                date=_date_text(day),
                edge=record.edge,
                share_anchor_entities=record.share_anchor_entities,
            )
        )
    anchor_primary, anchor_foreign = parts[0][1], parts[0][2]
    row_values = dict(zip(plan.anchor.identifiers.primary, anchor_primary, strict=True))
    row_values.update(zip((p.column for p in plan.anchor.paths), anchor_foreign, strict=True))
    row_values.update(zip(plan.extra_columns, extras, strict=True))
    return Match(
        anchor=records[0],
        related=records[1:],
        colocated=colocated,
        values={c: str(_as_key(v)) for c, v in row_values.items() if v is not None},
    )


def decide_rows(plan: AnchorPlan, rows: Sequence[Sequence[Any]], dates: set[date]) -> Match | None:
    """Exactly one row identifies the case. Several rows are settled by the dates
    the documents mention when exactly one row's records carry one of them."""
    matches = [row_match(plan, row) for row in rows[: plan.row_limit + 1]]
    if len(matches) == 1:
        return matches[0]
    if 1 < len(matches) <= plan.row_limit and dates:
        wanted = {d.isoformat() for d in dates}
        agreeing = [m for m in matches if any(r.date in wanted for r in m.records)]
        if len(agreeing) == 1:
            return agreeing[0]
    return None


def resolve_cluster(
    plan: AnchorPlan, readings: Sequence[Reading], cursor: Any
) -> tuple[Match | None, Constraints, bool]:
    """(match, constraints, queried) for one cluster: at most one query."""
    constraints = gather_constraints(readings)
    if not constraints.queryable(plan):
        return None, constraints, False
    sql, params = build_query(plan, constraints)
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    return decide_rows(plan, rows, constraints.dates), constraints, True


@dataclass(frozen=True)
class LookupValue:
    """A value a mention carries for one of an anchor's lookups."""

    column: str
    match: str
    value: str
    identifies: str | None = None
