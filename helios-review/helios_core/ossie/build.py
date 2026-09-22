"""
Build an Apache Ossie semantic model document from a reviewed helios proposal.

Mapping (Ossie core spec 0.2.0.dev0, schema vendored as ossie-schema.json):

  dataset            -> datasets[].name = physical table, source = database.table
                        business name goes into ai_context.synonyms (the spec has no dataset label)
  field              -> fields[].name = column, expression ANSI_SQL = column, label = business name
                        role time      -> dimension.is_time = true
                        role dimension / attribute / foreign_key / identifier -> dimension.is_time = false
                        role measure   -> no `dimension` block (Ossie has no measure flag)
  relationship       -> relationships[] (accepted only), unique name per from-column
  metric             -> metrics[], expression re-qualified as dataset.column
  provenance         -> custom_extensions [{vendor_name: HELIOS, data: <json>}] on every element:
                        role, confidence, source, glossary links, refers_to, run id.

Nothing in here talks to Atlas or the warehouse; proposed glossary terms are published separately.
"""
from __future__ import annotations

import json
import os
import re

import jsonschema

SPEC_VERSION = "0.2.0.dev0"
VENDOR = "HELIOS"
DIALECT = "ANSI_SQL"
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "ossie-schema.json")
_SQL_WORDS = {"group_concat", "date_trunc", "date_part", "date_add", "date_sub", "current_date", "current_timestamp",
              "is_null", "not_null", "if_null", "if_not"}

_TYPE_MAP = [
    (("bigint", "int", "smallint", "tinyint"), "Integer"),
    (("decimal", "numeric"), "Decimal"),
    (("double", "float", "real"), "Float"),
    (("boolean", "bool"), "Boolean"),
    (("timestamp",), "DateTime"),
    (("date",), "Date"),
    (("string", "varchar", "char", "text"), "String"),
]


def datatype(engine_type: str | None) -> str:
    t = (engine_type or "").lower()
    for prefixes, dt in _TYPE_MAP:
        if t.startswith(prefixes):
            return dt
    return "Opaque"


def slug(s: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z]+", "_", s.strip()).strip("_").lower()
    return re.sub(r"_+", "_", s) or "unnamed"


def _ext(data: dict) -> list[dict]:
    clean = {k: v for k, v in data.items() if v not in (None, [], "", {})}
    return [{"vendor_name": VENDOR, "data": json.dumps(clean, separators=(",", ":"))}]


def _table_name(table: str) -> str:
    return table.split(".")[-1]


def _qualify(expression: str, table: str, columns: set[str]) -> str:
    """Prefix bare column references with the dataset name, e.g. SUM(ss_net_paid) -> SUM(store_sales.ss_net_paid)."""
    ds = _table_name(table)
    pattern = re.compile(r"(?<![\w.])(" + "|".join(re.escape(c) for c in sorted(columns, key=len, reverse=True)) + r")(?![\w(])")
    return pattern.sub(lambda m: f"{ds}.{m.group(1)}", expression) if columns else expression


def _pk_and_unique(d: dict) -> tuple[list[str], list[list[str]]]:
    """Surrogate key becomes the primary key; other single-column keys become unique keys."""
    pks = list(d.get("primary_key") or [])
    if not pks:
        return [], []
    sk = [c for c in pks if c.endswith("_sk")]
    primary = sk[:1] or pks[:1]
    unique = [[c] for c in pks if c not in primary]
    return primary, unique


def build_dataset(d: dict, run_id: str | None) -> dict:
    name = _table_name(d["table"])
    primary, unique = _pk_and_unique(d)
    fields = []
    for f in d["fields"]:
        role = f.get("role", "attribute")
        field = {
            "name": f["column"],
            "expression": {"dialects": [{"dialect": DIALECT, "expression": f["column"]}]},
            "label": f.get("name") or f["column"],
            "description": f.get("description") or "",
            "datatype": datatype(f.get("type")),
        }
        if role == "time":
            field["dimension"] = {"is_time": True}
        elif role != "measure":
            field["dimension"] = {"is_time": False}
        terms = list(f.get("glossary_terms") or [])
        if terms:
            field["ai_context"] = {"synonyms": terms}
        field["custom_extensions"] = _ext({
            "role": role, "engine_type": f.get("type"), "confidence": f.get("confidence"),
            "refers_to": f.get("refers_to"), "glossary_terms": terms,
        })
        fields.append(field)

    ds = {
        "name": name,
        "source": d["table"],
        "description": d.get("description") or "",
        "fields": fields,
        "custom_extensions": _ext({"kind": d.get("kind"), "confidence": d.get("confidence"), "run_id": run_id}),
    }
    if primary:
        ds["primary_key"] = primary
    if unique:
        ds["unique_keys"] = unique
    if d.get("name") and d["name"].lower() != name.replace("_", " "):
        ds["ai_context"] = {"synonyms": [d["name"]]}
    return ds


# When a dataset has several join paths to the same target (catalog_sales -> date_dim via cs_sold_date_sk and
# cs_ship_date_sk), one is the default the compiler takes unless the request pins another. Ossie has no notion
# of a default join, so helios records it in its extension. Preference order for the from-column:
_DEFAULT_HINTS = ("sold_date", "sold_time", "returned_date", "returned_time", "return_time", "_date_sk", "_time_sk",
                  "bill_customer", "bill_cdemo", "bill_hdemo", "bill_addr", "refunded_customer", "refunded_cdemo",
                  "refunded_hdemo", "refunded_addr", "current_", "item_sk", "customer_sk", "store_sk")


def _default_score(col: str) -> int:
    low = col.lower()
    for i, h in enumerate(_DEFAULT_HINTS):
        if h in low:
            return len(_DEFAULT_HINTS) - i
    return 0


def build_relationships(rels: list[dict], default_overrides: dict | None = None) -> list[dict]:
    """default_overrides: {(from_table, to_table): relationship_name} chosen by the reviewer; otherwise heuristic."""
    default_overrides = default_overrides or {}
    out, seen = [], set()
    for r in rels:
        if not r.get("accepted", True):
            continue
        frm, to = _table_name(r["from"]), _table_name(r["to"])
        name = f"{frm}__{r['from_column']}__{to}"
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name, "from": frm, "to": to,
            "from_columns": [r["from_column"]], "to_columns": [r["to_column"]],
            "_meta": {"source": r.get("source"), "confidence": r.get("confidence"),
                      "reason": r.get("reason"), "match_ratio": r.get("match_ratio")},
        })
    groups: dict[tuple, list[dict]] = {}
    for rel in out:
        groups.setdefault((rel["from"], rel["to"]), []).append(rel)
    for key, group in groups.items():
        chosen = default_overrides.get(key)
        if chosen and any(g["name"] == chosen for g in group):
            winner = chosen
        else:
            winner = max(group, key=lambda g: (_default_score(g["from_columns"][0]), -len(g["from_columns"][0])))["name"]
        for g in group:
            g["_meta"]["is_default"] = g["name"] == winner
    for rel in out:
        rel["custom_extensions"] = _ext(rel.pop("_meta"))
    return out


def build_metrics(metrics: list[dict], datasets: list[dict]) -> tuple[list[dict], list[str]]:
    cols_by_table = {d["table"]: {f["column"] for f in d["fields"]} for d in datasets}
    all_cols = {c for cs in cols_by_table.values() for c in cs}
    out, seen, problems = [], set(), []
    for m in metrics:
        cols = cols_by_table.get(m["dataset"], set())
        referenced = set(re.findall(r"\b[a-z][a-z0-9_]*\b", m["expression"].lower()))
        foreign = (referenced & all_cols) - cols
        if foreign:
            problems.append(f"{m['name']}: references columns outside {m['dataset']}: {sorted(foreign)}")
            continue
        unknown = {t for t in referenced if "_" in t and t not in all_cols and t not in _SQL_WORDS}
        if unknown:
            problems.append(f"{m['name']}: unknown columns {sorted(unknown)}")
            continue
        name = slug(m["name"])
        if name in seen:
            problems.append(f"{m['name']}: duplicate metric name {name}")
            continue
        seen.add(name)
        out.append({
            "name": name,
            "expression": {"dialects": [{"dialect": DIALECT, "expression": _qualify(m["expression"], m["dataset"], cols)}]},
            "description": m.get("description") or "",
            "datatype": "Float" if "/" in m["expression"] else "Decimal",
            "ai_context": {"synonyms": [m["name"]]},
            "custom_extensions": _ext({"dataset": m["dataset"], "source": m.get("source"),
                                       "confidence": m.get("confidence")}),
        })
    return out, problems


def build(proposal: dict, model_name: str, description: str = "", run_id: str | None = None) -> tuple[dict, list[str]]:
    """Return (ossie_document, problems). problems are non-fatal things dropped along the way."""
    datasets = [build_dataset(d, run_id) for d in proposal["datasets"]]
    relationships = build_relationships(proposal.get("relationships", []))
    metrics, problems = build_metrics(proposal.get("metrics", []), proposal["datasets"])
    doc = {
        "version": SPEC_VERSION,
        "name": model_name,
        "description": description,
        "ai_context": {"instructions": "Semantic model generated by helios from warehouse evidence and reviewed by a human. "
                                       "Field labels are business names; custom_extensions carry helios provenance."},
        "datasets": datasets,
        "relationships": relationships,
        "metrics": metrics,
        "custom_extensions": _ext({"run_id": run_id, "llm": proposal.get("llm"), "review": proposal.get("review")}),
    }
    return doc, problems


def preflight(proposal: dict) -> dict:
    """Problems build() would report for the proposal as it stands, keyed by metric name, for the review page."""
    _, problems = build_metrics(proposal.get("metrics", []), proposal["datasets"])
    return {p.split(":", 1)[0]: p.split(":", 1)[1].strip() for p in problems}


def validate(doc: dict) -> list[str]:
    """Validate against the vendored Ossie schema plus referential checks the schema cannot express."""
    with open(SCHEMA_PATH) as f:
        schema = json.load(f)
    errors = [f"schema: {e.json_path}: {e.message}" for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]
    names = {d["name"] for d in doc["datasets"]}
    cols = {d["name"]: {f["name"] for f in d.get("fields", [])} for d in doc["datasets"]}
    for r in doc.get("relationships", []):
        for side in ("from", "to"):
            if r[side] not in names:
                errors.append(f"relationship {r['name']}: unknown dataset {r[side]}")
            else:
                for c in r[f"{side}_columns"]:
                    if c not in cols[r[side]]:
                        errors.append(f"relationship {r['name']}: {r[side]} has no column {c}")
    for m in doc.get("metrics", []):
        for ds, col in re.findall(r"\b([a-z][a-z0-9_]*)\.([a-z][a-z0-9_]*)\b", m["expression"]["dialects"][0]["expression"]):
            if ds not in cols or col not in cols[ds]:
                errors.append(f"metric {m['name']}: unknown reference {ds}.{col}")
    return errors


def dump_yaml(doc: dict) -> str:
    import yaml
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
