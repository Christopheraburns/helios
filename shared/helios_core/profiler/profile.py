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
    def _stem(col: str) -> list[str]:
        """Meaningful tokens of a key column: drop the table prefix token and stop words. ss_customer_sk -> [customer]."""
        toks = [t for t in _tokens(col) if t not in STOP_TOKENS]
        prefix = col.lower().split("_")[0]
        return [t for t in toks if t != prefix] or toks

    @classmethod
    def _name_match(cls, fk_col: str, fk_table: str, pk_table: str, pk_col: str | None = None) -> float:
        """Score how well a foreign-key column name points at a target table.

        1.0  the column's stem *is* the table name or the target key's stem (ss_customer_sk -> customer.c_customer_sk)
        0.9  the stem abbreviates the table name (ss_addr_sk -> customer_address, ss_promo_sk -> promotion)
        0.8  the stem is an abbreviation (cs_bill_cdemo_sk -> customer_demographics)
        0.7  the stem shares a token with the table name but is not the whole of it
             (ss_customer_sk -> customer_demographics, wp_web_page_id -> catalog_page)
        0.0  no name evidence
        A shared token used to score 1.0, which made customer_demographics tie with customer for every
        *_customer_sk column; the dense cd_demo_sk range then satisfied the data check and won the tie."""
        toks = cls._stem(fk_col)
        ptoks = [t for t in _tokens(pk_table) if t not in STOP_TOKENS]
        if not toks or not ptoks:
            return 0.0
        joined, pjoined = "".join(toks), "".join(ptoks)
        if joined == pjoined:
            return 1.0
        if pk_col and cls._stem(pk_col) == toks:
            return 1.0
        # contraction: every stem token abbreviates a table token and at least one is a real abbreviation
        # (addr -> address, promo -> promotion); an identical token is not a contraction
        if all(any(pt.startswith(t) and len(t) >= 3 for pt in ptoks) for t in toks) \
                and any(t not in ptoks for t in toks):
            return 0.9
        for t in toks:
            if len(ptoks) >= 2 and t.startswith(ptoks[0][0]) and ptoks[-1].startswith(t[1:]) and len(t) >= 4:
                return 0.8
        if any(t == p for t in toks for p in ptoks):
            return 0.7
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
            own_stem = [t for t in _tokens(table) if t not in STOP_TOKENS]
            for col, s in p["stats"]["columns"].items():
                if col in pk_cols or not col.lower().endswith(KEY_SUFFIXES) or s["ndv"] == 0:
                    continue
                if own_stem in (self._stem(col), [t for t in _tokens(col) if t not in STOP_TOKENS]):
                    # the table's own business identifier (wp_web_page_id in web_page, s_store_id in store):
                    # it identifies rows here, it does not refer anywhere else
                    continue
                named = []
                for ptkey, pcol, ptype, prows in pks:
                    if ptkey == tkey:
                        continue
                    score = self._name_match(col, table, ptkey.split(".", 1)[1], pcol)
                    if score > 0 and _family(s["type"]) == ptype:
                        named.append((score, ptkey, pcol, prows))
                if named:
                    named.sort(key=lambda x: (-x[0], x[3]))
                    for score, ptkey, pcol, prows in named[:3]:
                        cands.append({"from": tkey, "from_column": col, "to": ptkey, "to_column": pcol,
                                      "name_score": score, "to_rows": prows})
                else:
                    for ptkey, pcol, ptype, prows in pks:
                        if ptkey != tkey and _family(s["type"]) == ptype and s["ndv"] <= prows:
                            cands.append({"from": tkey, "from_column": col, "to": ptkey, "to_column": pcol,
                                          "name_score": 0.0, "to_rows": prows})
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
            # data evidence first, then name evidence, then the tighter target: a key that fits entirely inside
            # a 100k-row table and also inside a 1.9M-row table almost certainly belongs to the smaller one
            rank = (m["match_ratio"], m["name_score"], -m.get("to_rows", 0))
            if k not in best or rank > (best[k]["match_ratio"], best[k]["name_score"], -best[k].get("to_rows", 0)):
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