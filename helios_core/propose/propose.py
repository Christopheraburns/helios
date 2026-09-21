"""
Propose: turn harvest + profile evidence into a draft — dataset descriptions, column roles,
glossary term proposals, relationship decisions and metric candidates — using an LLM for the
judgement calls. Every element carries a confidence and, where the LLM decided, a rationale.

The output is helios's own draft schema (propose.json). Conversion to the Ossie
document format is a separate step (helios_core.ossie) so the draft can also feed the
glossary and ontology layers.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..llm import LLMClient

ROLES = ("identifier", "foreign_key", "time", "measure", "dimension", "attribute")

SYSTEM_TABLE = """You are a data architect documenting a data warehouse for business users.
You are given one table: its columns with statistics, the business glossary terms already linked to columns,
and the verified relationships to other tables. Produce a business-facing description of the table and each column.

Rules:
- kind is one of: fact, dimension, bridge, lookup, other. Fact tables hold transactions or events with measures;
  dimension tables describe entities used to slice facts.
- role for each column is one of: identifier (a key that identifies the row), foreign_key (refers to another table),
  time (a date/time or a key to a date table), measure (a numeric quantity meant to be aggregated),
  dimension (a category or attribute used to group or filter), attribute (descriptive text not useful for grouping).
  A hint is given for each column; override it only when the evidence clearly says otherwise.
- name is a short business name in sentence case, e.g. "Net paid", "Customer".
- description is one or two sentences a business user would understand; state units and derivations when evident.
  Prefer the linked glossary term's definition when one exists.
- For a column with no glossary term linked, propose one in "term" ({"name": ..., "definition": ...}) when the
  column is a measure, a time, or a dimension a business user would ask about. Set "term" to null for keys,
  system columns and obscure attributes. Reuse the same term name for the same concept across tables
  (e.g. every quantity column is "Quantity sold").
- confidence is 0-1 for the table and for each column, reflecting how sure you are of the role and meaning."""

SYSTEM_RELATIONSHIPS = """You are a data architect. For each candidate relationship below, the data shows that every value
in the "from" column exists in the "to" column, but the column name does not obviously point at the target table.
Decide whether each is a real foreign-key relationship or a coincidence (small integer ranges often overlap by chance).
Use the table descriptions, column names and cardinalities. Return {"decisions": [{"index": i, "accept": true/false,
"confidence": 0-1, "reason": "..."}]} with one entry per candidate."""

SYSTEM_METRICS = """You are a data architect defining business metrics for a semantic layer.
You are given the fact tables with their measure columns and descriptions, and a sample of SQL queries users have run.
Propose the metrics a business user would ask for. For each, return:
  name (sentence case), description, dataset (the fact table), expression (an aggregate SQL expression over that
  table's columns, e.g. "SUM(ss_net_paid)" or "SUM(ss_net_profit) / SUM(ss_net_paid)"),
  source ("query_history" if the aggregation appears in the queries, else "derived"), confidence (0-1).
Include the obvious totals for every measure that represents money or quantity, ratios that are standard for the domain,
and anything the query history shows people computing. Do not invent columns. Return {"metrics": [...]}."""


class Proposer:
    def __init__(self, llm: LLMClient, max_queries: int = 40, query_chars: int = 1500):
        self.llm = llm
        self.max_queries = max_queries
        self.query_chars = query_chars

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _role_hint(col: str, stats: dict, pks: set, fks: set) -> str:
        t = (stats.get("type") or "").lower()
        if col in pks:
            return "identifier"
        if col in fks:
            return "time" if "date" in col or "time" in col else "foreign_key"
        if t.startswith(("date", "timestamp")):
            return "time"
        if t.startswith(("int", "bigint", "smallint", "tinyint", "double", "float", "decimal")):
            return "measure" if (stats.get("ndv") or 0) > 20 else "dimension"
        return "dimension" if (stats.get("ndv") or 0) <= 200 else "attribute"

    @staticmethod
    def _terms_by_column(harvest: dict) -> dict:
        out = {}
        for t in harvest.get("glossary_terms", []):
            for c in t["columns"]:
                out.setdefault(c.split("@")[0].split(".")[-1], []).append(t)
        return out

    # ------------------------------------------------------------ stage 1: tables
    def propose_table(self, table: dict, prof: dict, rels: list[dict], terms_by_col: dict) -> dict:
        key = f"{table['database']}.{table['table']}"
        pks = {pk["column"] for pk in prof.get("primary_keys", [])}
        fks = {r["from_column"] for r in rels if r["from"] == key}
        cols = []
        for c in table["columns"]:
            s = prof.get("stats", {}).get("columns", {}).get(c["name"], {})
            linked = terms_by_col.get(c["name"], [])
            cols.append({"column": c["name"], "type": c["type"], "null_rate": s.get("null_rate"),
                         "distinct": s.get("ndv_exact", s.get("ndv")), "min": s.get("min"), "max": s.get("max"),
                         "role_hint": self._role_hint(c["name"], s, pks, fks),
                         "refers_to": next((f"{r['to']}.{r['to_column']}" for r in rels
                                            if r["from"] == key and r["from_column"] == c["name"]), None),
                         "glossary_terms": [{"name": t["name"], "definition": t["short_description"]} for t in linked]})
        user = json.dumps({"table": key, "row_count": prof.get("stats", {}).get("row_count"),
                           "primary_key_candidates": sorted(pks), "columns": cols,
                           "referenced_by": [f"{r['from']}.{r['from_column']}" for r in rels if r["to"] == key]}, default=str)
        user += ('\n\nReturn {"name": ..., "kind": ..., "description": ..., "confidence": ..., '
                 '"fields": [{"column": ..., "name": ..., "role": ..., "description": ..., "term": {...}|null, "confidence": ...}]} '
                 'with one entry in fields for every column, in the same order.')
        out = self.llm.complete_json(SYSTEM_TABLE, user)
        fields_by_col = {f.get("column"): f for f in out.get("fields", []) if isinstance(f, dict)}
        fields = []
        for c in cols:
            f = fields_by_col.get(c["column"], {})
            role = f.get("role") if f.get("role") in ROLES else c["role_hint"]
            fields.append({"column": c["column"], "type": c["type"], "name": f.get("name") or c["column"], "role": role,
                           "description": f.get("description") or "", "refers_to": c["refers_to"],
                           "glossary_terms": [t["name"] for t in c["glossary_terms"]],
                           "proposed_term": f.get("term") if isinstance(f.get("term"), dict) and not c["glossary_terms"] else None,
                           "confidence": _conf(f.get("confidence"), 0.6)})
        return {"table": key, "name": out.get("name") or table["table"], "kind": out.get("kind") or "other",
                "description": out.get("description") or "", "primary_key": sorted(pks),
                "confidence": _conf(out.get("confidence"), 0.6), "fields": fields}

    # ------------------------------------------------------------ stage 2: suggested relationships
    def decide_relationships(self, suggested: list[dict], datasets: list[dict]) -> list[dict]:
        if not suggested:
            return []
        desc = {d["table"]: {"kind": d["kind"], "description": d["description"]} for d in datasets}
        user = json.dumps({"tables": desc, "candidates": [
            {"index": i, "from": f"{c['from']}.{c['from_column']}", "to": f"{c['to']}.{c['to_column']}",
             "distinct_values": c["distinct_values"], "match_ratio": c["match_ratio"]} for i, c in enumerate(suggested)]})
        out = self.llm.complete_json(SYSTEM_RELATIONSHIPS, user)
        decisions = {d.get("index"): d for d in out.get("decisions", []) if isinstance(d, dict)}
        result = []
        for i, c in enumerate(suggested):
            d = decisions.get(i, {})
            result.append({**c, "source": "llm_confirmed" if d.get("accept") else "llm_rejected",
                           "accepted": bool(d.get("accept")), "confidence": _conf(d.get("confidence"), 0.5),
                           "reason": d.get("reason", "")})
        return result

    # ------------------------------------------------------------ stage 3: metrics
    def propose_metrics(self, datasets: list[dict], queries: list[str]) -> list[dict]:
        facts = [{"table": d["table"], "name": d["name"], "description": d["description"],
                  "measures": [{"column": f["column"], "name": f["name"], "description": f["description"]}
                               for f in d["fields"] if f["role"] == "measure"]}
                 for d in datasets if d["kind"] == "fact" and any(f["role"] == "measure" for f in d["fields"])]
        if not facts:
            return []
        sample = [q[: self.query_chars] for q in queries[: self.max_queries]]
        out = self.llm.complete_json(SYSTEM_METRICS, json.dumps({"fact_tables": facts, "queries": sample}))
        valid_tables = {f["table"] for f in facts}
        metrics = []
        for m in out.get("metrics", []):
            if isinstance(m, dict) and m.get("dataset") in valid_tables and m.get("expression"):
                metrics.append({"name": m.get("name", ""), "description": m.get("description", ""), "dataset": m["dataset"],
                                "expression": m["expression"], "source": m.get("source", "derived"),
                                "confidence": _conf(m.get("confidence"), 0.5)})
        return metrics

    # ------------------------------------------------------------ all together
    def run(self, harvest: dict, profile: dict, log=print) -> dict:
        rels = profile.get("relationships", [])
        terms_by_col = self._terms_by_column(harvest)
        datasets = []
        for t in harvest["tables"]:
            key = f"{t['database']}.{t['table']}"
            log(f"  describing {key}")
            datasets.append(self.propose_table(t, profile.get("tables", {}).get(key, {}), rels, terms_by_col))

        log("  deciding suggested relationships")
        decided = self.decide_relationships(profile.get("suggested_relationships", []), datasets)
        relationships = [{**r, "source": "profile", "accepted": True, "confidence": 0.95} for r in rels] + decided

        log("  proposing metrics")
        metrics = self.propose_metrics(datasets, harvest.get("queries", {}).get("statements", []))

        # merge proposed glossary terms across tables by name
        terms: dict[str, dict] = {}
        for d in datasets:
            for f in d["fields"]:
                pt = f.get("proposed_term")
                if pt and pt.get("name"):
                    k = pt["name"].strip().lower()
                    e = terms.setdefault(k, {"name": pt["name"].strip(), "definition": pt.get("definition", ""),
                                             "columns": [], "confidence": f["confidence"]})
                    e["columns"].append(f"{d['table']}.{f['column']}")
                    e["confidence"] = max(e["confidence"], f["confidence"])

        return {"proposed_at": datetime.now(timezone.utc).isoformat(),
                "llm": {"provider": self.llm.provider, "model": self.llm.model, "calls": self.llm.calls},
                "datasets": datasets, "relationships": relationships, "metrics": metrics,
                "glossary_terms": sorted(terms.values(), key=lambda x: x["name"])}


def _conf(v, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return default
