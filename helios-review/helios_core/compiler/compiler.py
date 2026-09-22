"""
Compile a semantic request against a published model into SQL for a target engine.

    request = SemanticRequest(
        metrics=["Store Sales Revenue"],
        dimensions=["date_dim.d_year", "s_state"],
        filters=[Filter("d_year", "=", 2001), Filter("i_category", "in", ["Music", "Books"])],
        order_by=["-Store Sales Revenue"], limit=100)
    sql = Compiler(model).compile(request, dialect="hive")

How it works
- every metric names its fact dataset; a request's metrics must share one fact (v1 — cross-fact requests come later)
- measures are ad-hoc aggregates over a field when there is no metric for it: Measure("ss_ext_sales_price", "sum")
- dimensions and filter fields resolve to dataset.field via the model (qualified name, unique column, or unique label)
- the join graph is walked from the fact to every dataset touched; default relationships are taken unless the
  request pins another with `via={"date_dim": "catalog_sales__cs_ship_date_sk__date_dim"}`
- the query is assembled as a SQLGlot AST and rendered in the engine's dialect; Impala-specific fixes are applied
  on top of the Hive rendering
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from ..ossie.model import Field, Metric, Relationship, SemanticModel

OPS = {"=", "!=", "<>", ">", ">=", "<", "<=", "in", "not in", "between", "like", "is null", "is not null"}
AGGS = {"sum", "avg", "min", "max", "count", "count_distinct"}


@dataclass
class Filter:
    field: str
    op: str
    value: object = None


@dataclass
class Measure:
    field: str
    agg: str = "sum"
    alias: str | None = None


@dataclass
class SemanticRequest:
    metrics: list[str] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[Filter] = field(default_factory=list)
    via: dict[str, str] = field(default_factory=dict)      # {dataset: relationship_name}
    order_by: list[str] = field(default_factory=list)      # "-name" for descending
    limit: int | None = None
    dataset: str | None = None                             # fact dataset, only needed when no metric implies one

    @classmethod
    def from_dict(cls, d: dict) -> "SemanticRequest":
        return cls(
            metrics=list(d.get("metrics") or []),
            measures=[Measure(**m) if isinstance(m, dict) else Measure(m) for m in d.get("measures") or []],
            dimensions=list(d.get("dimensions") or []),
            filters=[Filter(**f) if isinstance(f, dict) else Filter(*f) for f in d.get("filters") or []],
            via=dict(d.get("via") or {}), order_by=list(d.get("order_by") or []),
            limit=d.get("limit"), dataset=d.get("dataset"))


@dataclass
class Compiled:
    sql: str
    dialect: str
    fact: str
    joins: list[str]
    columns: list[str]


class CompileError(ValueError):
    pass


class Compiler:
    def __init__(self, model: SemanticModel):
        self.model = model

    # ------------------------------------------------------------ resolution
    def _fact(self, req: SemanticRequest, metrics: list[Metric], measures: list[Field]) -> str:
        facts = {m.dataset for m in metrics} | {f.dataset for f in measures}
        if req.dataset:
            facts.add(req.dataset)
        if not facts:
            # dimensions-only request: use the dataset of the first dimension
            if req.dimensions:
                return self.model.field(req.dimensions[0]).dataset
            raise CompileError("request has no metrics, measures or dimensions")
        if len(facts) > 1:
            raise CompileError(f"metrics span several fact datasets ({', '.join(sorted(facts))}); one fact per request in this version")
        return facts.pop()

    def _alias(self, name: str) -> str:
        return name

    # ------------------------------------------------------------ compile
    def plan(self, req: SemanticRequest) -> tuple[str, list[Relationship], list[Metric], list[Field], list[Field], list[tuple[Field, Filter]]]:
        metrics = [self.model.metric(m) for m in req.metrics]
        measures = [self.model.field(m.field) for m in req.measures]
        dims = [self.model.field(d) for d in req.dimensions]
        filters = [(self.model.field(f.field), f) for f in req.filters]
        for f, flt in filters:
            if flt.op.lower() not in OPS:
                raise CompileError(f"unsupported operator {flt.op!r}")
        fact = self._fact(req, metrics, measures)
        needed = {f.dataset for f in dims} | {f.dataset for f, _ in filters}
        needed.discard(fact)
        joins: list[Relationship] = []
        joined = {fact}
        for ds in sorted(needed):
            for rel in self.model.join_path(fact, ds, req.via):
                if rel.name not in {j.name for j in joins}:
                    joins.append(rel)
                    joined.add(rel.other(fact) if fact in (rel.from_dataset, rel.to_dataset) else rel.to_dataset)
        return fact, joins, metrics, measures, dims, filters

    def compile(self, req: SemanticRequest, dialect: str = "hive") -> Compiled:
        fact, joins, metrics, measures, dims, filters = self.plan(req)
        m = self.model

        def table(ds: str) -> exp.Expression:
            src = m.datasets[ds].source
            return exp.to_table(src).as_(self._alias(ds))

        def col(f: Field) -> exp.Expression:
            # field expressions are ANSI, relative to the dataset; qualify bare columns with the alias
            e = sqlglot.parse_one(f.expression, read=None) if f.expression != f.name else exp.column(f.name, table=self._alias(f.dataset))
            for c in e.find_all(exp.Column):
                if not c.table:
                    c.set("table", exp.to_identifier(self._alias(f.dataset)))
            return e

        select: list[exp.Expression] = []
        out_cols: list[str] = []
        for d in dims:
            select.append(col(d).as_(d.name))
            out_cols.append(d.name)
        for mt in metrics:
            select.append(sqlglot.parse_one(mt.expression, read=None).as_(_slug(mt.name)))
            out_cols.append(_slug(mt.name))
        for ms, f in zip(req.measures, measures):
            agg = ms.agg.lower()
            if agg not in AGGS:
                raise CompileError(f"unsupported aggregate {ms.agg!r}")
            inner = col(f)
            e = exp.Count(this=exp.Distinct(expressions=[inner])) if agg == "count_distinct" else \
                getattr(exp, agg.capitalize())(this=inner)
            alias = ms.alias or f"{agg}_{f.name}"
            select.append(e.as_(alias))
            out_cols.append(alias)

        q = exp.select(*select).from_(table(fact))
        for rel in joins:
            # join the side not yet in the query
            new_side = rel.to_dataset if rel.from_dataset in _tables_in(q) else rel.from_dataset
            on = exp.and_(*[exp.EQ(this=exp.column(fc, table=rel.from_dataset), expression=exp.column(tc, table=rel.to_dataset))
                            for fc, tc in zip(rel.from_columns, rel.to_columns)])
            q = q.join(table(new_side), on=on, join_type="inner")

        for f, flt in filters:
            q = q.where(_condition(col(f), flt))
        if dims and (metrics or measures):
            q = q.group_by(*[col(d) for d in dims])
        for o in req.order_by:
            desc = o.startswith("-")
            name = o.lstrip("+-")
            try:
                target = _slug(m.metric(name).name)
            except KeyError:
                try:
                    target = m.field(name).name
                except KeyError:
                    target = name
            if target not in out_cols:
                raise CompileError(f"order_by {name!r} is not in the select list ({', '.join(out_cols)})")
            q = q.order_by(exp.Ordered(this=exp.column(target), desc=desc))
        if req.limit:
            q = q.limit(req.limit)

        sql = q.sql(dialect=dialect, pretty=True)
        if dialect in ("hive", "impala"):
            sql = _impala_fixups(sql)
        return Compiled(sql=sql, dialect=dialect, fact=fact, joins=[r.name for r in joins], columns=out_cols)


# ---------------------------------------------------------------- helpers
def _slug(s: str) -> str:
    import re
    return re.sub(r"[^0-9a-zA-Z]+", "_", s).strip("_").lower()


def _tables_in(q: exp.Select) -> set[str]:
    names = set()
    for t in q.find_all(exp.Table):
        names.add(t.alias_or_name)
    return names


def _literal(v):
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float)):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


def _condition(column: exp.Expression, flt: Filter) -> exp.Expression:
    op = flt.op.lower()
    v = flt.value
    if op == "=":
        return exp.EQ(this=column, expression=_literal(v))
    if op in ("!=", "<>"):
        return exp.NEQ(this=column, expression=_literal(v))
    if op == ">":
        return exp.GT(this=column, expression=_literal(v))
    if op == ">=":
        return exp.GTE(this=column, expression=_literal(v))
    if op == "<":
        return exp.LT(this=column, expression=_literal(v))
    if op == "<=":
        return exp.LTE(this=column, expression=_literal(v))
    if op == "in":
        return exp.In(this=column, expressions=[_literal(x) for x in (v if isinstance(v, (list, tuple)) else [v])])
    if op == "not in":
        return exp.Not(this=exp.In(this=column, expressions=[_literal(x) for x in v]))
    if op == "between":
        lo, hi = v
        return exp.Between(this=column, low=_literal(lo), high=_literal(hi))
    if op == "like":
        return exp.Like(this=column, expression=_literal(v))
    if op == "is null":
        return exp.Is(this=column, expression=exp.Null())
    if op == "is not null":
        return exp.Not(this=exp.Is(this=column, expression=exp.Null()))
    raise CompileError(f"unsupported operator {flt.op!r}")


def _impala_fixups(sql: str) -> str:
    """Differences between SQLGlot's Hive rendering and what Impala accepts."""
    return sql.replace("`", "")  # Impala accepts backticks but the harvested names never need quoting; keep the SQL readable
