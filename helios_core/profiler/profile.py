"""
Profile: column statistics, key candidates and verified relationships.

Everything here is deterministic. It produces the evidence the LLM stage reasons over,
and on a conventionally named warehouse it finds most relationships on its own.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..engines.base import Engine

NUMERIC = ("int", "bigint", "smallint", "tinyint", "double", "float", "decimal", "real")
KEY_SUFFIXES = ("_sk", "_id", "_key", "id")
STOP_TOKENS = {"sk", "id", "key", "dim", "bill", "ship", "sold", "returned", "return", "refunded", "returning",
               "current", "first", "last", "start", "end", "open", "closed", "close", "creation", "access"}


def _base_type(t: str) -> str:
    return (t or "").lower().split("(")[0].strip()


def _family(t: str) -> str:
    b = _base_type(t)
    if b in ("int", "integer", "bigint", "smallint", "tinyint", "hugeint"):
        return "integer"
    if b in ("double", "float", "decimal", "real", "numeric"):
        return "decimal"
    if b in ("string", "varchar", "char", "text"):
        return "string"
    if b in ("date", "timestamp", "datetime", "timestamp with time zone"):
        return "temporal"
    return b


def _tokens(name: str) -> list[str]:
    return [x for x in re.split(r"[_\W]+", name.lower()) if x]


class Profiler:
    def __init__(self, engine: Engine, overlap_threshold: float = 0.95, max_pairs: int = 400):
        self.engine = engine
        self.overlap_threshold = overlap_threshold
        self.max_pairs = max_pairs

    # ------------------------------------------------------------ column stats
    def column_stats(self, db: str, table: str, columns: list[dict]) -> dict:
        """One aggregate query per table: count, non-null count, NDV, min, max for every column."""
        parts = ["COUNT(*) AS n_rows"]
        for c in columns:
            n, t = c["name"], _base_type(c["type"])
            parts.append(f"COUNT(`{n}`) AS `{n}__nn`")
            parts.append(f"NDV(`{n}`) AS `{n}__ndv`")
            if t not in ("boolean", "binary"):
                parts.append(f"MIN(`{n}`) AS `{n}__min`")
                parts.append(f"MAX(`{n}`) AS `{n}__max`")
        res = self.engine.query(f"SELECT {', '.join(parts)} FROM {db}.{table}")
        row = dict(zip(res.columns, res.rows[0]))
        n_rows = int(row["n_rows"])
        stats = {}
        for c in columns:
            n = c["name"]
            nn = int(row.get(f"{n}__nn") or 0)
            ndv = int(row.get(f"{n}__ndv") or 0)
            stats[n] = {"type": c["type"], "non_null": nn, "null_rate": round(1 - nn / n_rows, 4) if n_rows else None,
                        "ndv": ndv, "min": _jsonable(row.get(f"{n}__min")), "max": _jsonable(row.get(f"{n}__max"))}
        return {"row_count": n_rows, "columns": stats}

    # ------------------------------------------------------------ keys
    def primary_key_candidates(self, db: str, table: str, stats: dict) -> list[dict]:
        """Shortlist by name and approximate NDV, then verify with an exact COUNT(DISTINCT)."""
        n = stats["row_count"]
        out = []
        for name, s in stats["columns"].items():
            if not (n and s["non_null"] == n and s["ndv"] >= n * 0.85 and name.lower().endswith(KEY_SUFFIXES)):
                continue
            exact = int(self.engine.query(f"SELECT COUNT(DISTINCT `{name}`) FROM {db}.{table}").rows[0][0])
            s["ndv_exact"] = exact
            if exact == n:
                out.append({"column": name, "confidence": 0.95 if name.lower().endswith(("_sk", "_id")) else 0.8})
        return out

    @staticmethod
    def _name_match(fk_col: str, fk_table: str, pk_table: str) -> float:
        """Score how well a foreign-key column name points at a table name, e.g. ss_customer_sk -> customer."""
        toks = [t for t in _tokens(fk_col) if t not in STOP_TOKENS]
        ptoks = [t for t in _tokens(pk_table) if t not in STOP_TOKENS]
        if not toks or not ptoks:
            return 0.0
        # drop the fact table's own prefix token (ss, cs, ws, sr ...)
        prefix = fk_col.lower().split("_")[0]
        toks = [t for t in toks if t != prefix] or toks
        if any(t == p for t in toks for p in ptoks):
            return 1.0
        joined = "".join(toks)
        if joined == "".join(ptoks) or joined in "".join(ptoks):
            return 0.9
        # abbreviation: cdemo -> customer_demographics, hdemo -> household_demographics
        for t in toks:
            if len(ptoks) >= 2 and t.startswith(ptoks[0][0]) and ptoks[-1].startswith(t[1:]) and len(t) >= 4:
                return 0.8
        return 0.0

    def relationship_candidates(self, profiles: dict) -> list[dict]:
        """Pair every likely foreign-key column with primary-key candidates, by name first, then by type + cardinality."""
        pks = []
        for tkey, p in profiles.items():
            for pk in p["primary_keys"]:
                pks.append((tkey, pk["column"], _family(p["stats"]["columns"][pk["column"]]["type"]), p["stats"]["row_count"]))
        cands = []
        for tkey, p in profiles.items():
            db, table = tkey.split(".", 1)
            pk_cols = {pk["column"] for pk in p["primary_keys"]}
            for col, s in p["stats"]["columns"].items():
                if col in pk_cols or not col.lower().endswith(KEY_SUFFIXES) or s["ndv"] == 0:
                    continue
                named = []
                for ptkey, pcol, ptype, prows in pks:
                    if ptkey == tkey:
                        continue
                    score = self._name_match(col, table, ptkey.split(".", 1)[1])
                    if score > 0 and _family(s["type"]) == ptype:
                        named.append((score, ptkey, pcol))
                if named:
                    named.sort(reverse=True)
                    for score, ptkey, pcol in named[:2]:
                        cands.append({"from": tkey, "from_column": col, "to": ptkey, "to_column": pcol, "name_score": score})
                else:
                    for ptkey, pcol, ptype, prows in pks:
                        if ptkey != tkey and _family(s["type"]) == ptype and s["ndv"] <= prows:
                            cands.append({"from": tkey, "from_column": col, "to": ptkey, "to_column": pcol, "name_score": 0.0})
        return cands[: self.max_pairs]

    def measure_overlap(self, c: dict) -> dict:
        """Fraction of distinct non-null FK values that exist in the PK column."""
        sql = (f"SELECT COUNT(*) AS total, SUM(CASE WHEN b.`{c['to_column']}` IS NULL THEN 1 ELSE 0 END) AS unmatched "
               f"FROM (SELECT DISTINCT `{c['from_column']}` AS k FROM {c['from']} WHERE `{c['from_column']}` IS NOT NULL) a "
               f"LEFT JOIN {c['to']} b ON a.k = b.`{c['to_column']}`")
        total, unmatched = self.engine.query(sql).rows[0]
        total, unmatched = int(total or 0), int(unmatched or 0)
        return {**c, "distinct_values": total, "unmatched": unmatched,
                "match_ratio": round((total - unmatched) / total, 4) if total else 0.0}

    # ------------------------------------------------------------ all together
    def run(self, harvest: dict) -> dict:
        profiles = {}
        for t in harvest["tables"]:
            key = f"{t['database']}.{t['table']}"
            stats = self.column_stats(t["database"], t["table"], t["columns"])
            profiles[key] = {"stats": stats, "primary_keys": self.primary_key_candidates(t["database"], t["table"], stats)}
        measured = [self.measure_overlap(c) for c in self.relationship_candidates(profiles)]
        # keep the best target per FK column
        best: dict[tuple, dict] = {}
        for m in measured:
            k = (m["from"], m["from_column"])
            if k not in best or (m["match_ratio"], m["name_score"]) > (best[k]["match_ratio"], best[k]["name_score"]):
                best[k] = m
        ok = [m for m in best.values() if m["match_ratio"] >= self.overlap_threshold]
        # accepted: name evidence AND data evidence. suggested: data evidence only (for the LLM stage to judge).
        relationships = [m for m in ok if m["name_score"] > 0]
        suggested = [m for m in ok if m["name_score"] == 0]
        rejected = [m for m in best.values() if m["match_ratio"] < self.overlap_threshold]
        return {"profiled_at": datetime.now(timezone.utc).isoformat(), "engine": self.engine.name,
                "tables": profiles, "relationships": relationships,
                "suggested_relationships": suggested, "rejected_candidates": rejected}


def _jsonable(v):
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return str(v)
