"""
Harvest: snapshot the warehouse catalog, the Atlas glossary and available query history.
Output is a plain dict (serialised to harvest.json) that profile and propose consume.
"""
from __future__ import annotations

import glob
import os
import re
from datetime import datetime, timezone

from ..atlas import AtlasClient, AtlasError
from ..engines.base import Engine


class Harvester:
    def __init__(self, engine: Engine, atlas: AtlasClient | None = None):
        self.engine = engine
        self.atlas = atlas

    # ------------------------------------------------------------ engine side
    def row_count(self, db: str, table: str) -> int | None:
        """Prefer table stats (cheap); fall back to COUNT(*) when stats are missing (-1)."""
        try:
            res = self.engine.query(f"SHOW TABLE STATS {db}.{table}")
            if "#Rows" in res.columns:
                rows = [r[res.columns.index("#Rows")] for r in res.rows]
                total = sum(int(x) for x in rows if x is not None and int(x) >= 0)
                if total > 0 or all(int(x) == 0 for x in rows if x is not None):
                    return total
        except Exception:  # noqa: BLE001 - stats are optional
            pass
        try:
            return int(self.engine.query(f"SELECT COUNT(*) FROM {db}.{table}").rows[0][0])
        except Exception:  # noqa: BLE001
            return None

    def harvest_tables(self, databases: list[str]) -> list[dict]:
        out = []
        for db in databases:
            for t in self.engine.tables(db):
                cols = self.engine.columns(db, t)
                out.append({"database": db, "table": t, "columns": cols, "row_count": self.row_count(db, t)})
        return out

    # ------------------------------------------------------------ atlas side
    def harvest_glossary(self, glossary_names: list[str] | None = None) -> list[dict]:
        if self.atlas is None:
            return []
        terms_out = []
        for g in self.atlas.list_glossaries():
            if glossary_names and g["name"] not in glossary_names:
                continue
            for t in self.atlas.list_terms(g["guid"]):
                cols = []
                try:
                    for e in self.atlas.assigned_entities(t["guid"]):
                        qn = e.get("displayText") or ""
                        cols.append(qn)
                except AtlasError:
                    pass
                terms_out.append({"glossary": g["name"], "name": t["name"],
                                  "short_description": t.get("shortDescription") or "",
                                  "long_description": t.get("longDescription") or "",
                                  "abbreviation": t.get("abbreviation") or "",
                                  "columns": cols})
        return terms_out

    # ------------------------------------------------------------ query history
    # Statements that are not evidence of what people ask the warehouse: connection checks, metadata
    # commands, and helios's own profiling and harvesting queries.
    _NOISE = re.compile(
        r"^\s*(select\s+1\b|show\b|describe\b|explain\b|set\b|use\b|refresh\b|invalidate\b|compute\s+stats\b)"
        r"|\bndv\s*\(|\bcount\s*\(\s*distinct\b.*\bfrom\b[^,]*$|sys\.impala_query_log|\bhelios_meta\b"
        r"|select\s+distinct\s+`?\w+`?\s+as\s+k\s+from",  # profiler overlap check
        re.IGNORECASE | re.DOTALL)
    _AGGREGATE = re.compile(r"\b(group\s+by|sum\s*\(|avg\s*\(|count\s*\(|min\s*\(|max\s*\()", re.IGNORECASE)

    @staticmethod
    def _normalize(sql: str) -> str:
        """Collapse whitespace and literals so repeated dashboard queries count once."""
        s = re.sub(r"--[^\n]*", " ", sql)
        s = re.sub(r"/\*.*?\*/", " ", s, flags=re.DOTALL)
        s = re.sub(r"'[^']*'", "'?'", s)
        s = re.sub(r"\b\d+(\.\d+)?\b", "?", s)
        return re.sub(r"\s+", " ", s).lower().strip(" ;")

    def _useful(self, sql: str, table_names: set[str]) -> bool:
        if not sql or self._NOISE.search(sql):
            return False
        if not self._AGGREGATE.search(sql):
            return False  # only aggregations are metric evidence
        low = sql.lower()
        return any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in table_names)

    def harvest_queries(self, tables: list[dict], query_dir: str | None = None,
                        limit: int = 500, log_window: int = 5000) -> dict:
        """Aggregation queries over the harvested tables, from the engine's query log and/or a folder of .sql files.

        The log is read recent-first over a wide window and then filtered, because on a quiet warehouse the most
        recent N statements are connection checks (`SELECT 1`) and helios's own profiling."""
        table_names = {t["table"].lower() for t in tables}
        raw, sources, stats = [], [], {}
        try:
            res = self.engine.query(
                f"SELECT sql FROM sys.impala_query_log WHERE query_type = 'QUERY' "
                f"ORDER BY start_time_utc DESC LIMIT {log_window}", log_window)
            log = [r[0] for r in res.rows if r[0]]
            raw += [("sys.impala_query_log", q) for q in log]
            sources.append("sys.impala_query_log")
            stats["log_statements_read"] = len(log)
        except Exception as e:  # noqa: BLE001 - not every VW exposes the log
            sources.append(f"sys.impala_query_log unavailable: {str(e)[:120]}")
        if query_dir and os.path.isdir(query_dir):
            n = 0
            for path in sorted(glob.glob(os.path.join(query_dir, "*.sql"))):
                with open(path) as f:
                    for stmt in re.split(r";\s*\n", f.read()):
                        if stmt.strip():
                            raw.append((os.path.basename(path), stmt.strip()))
                            n += 1
            sources.append(query_dir)
            stats["file_statements_read"] = n

        seen, queries, origins = set(), [], []
        for origin, sql in raw:
            if not self._useful(sql, table_names):
                continue
            key = self._normalize(sql)
            if key in seen:
                continue
            seen.add(key)
            queries.append(sql.strip())
            origins.append(origin)
            if len(queries) >= limit:
                break
        stats.update({"kept": len(queries), "dropped_noise_or_unrelated": len(raw) - len(queries)})
        return {"sources": sources, "stats": stats, "statements": queries, "origins": origins}

    # ------------------------------------------------------------ all together
    def run(self, databases: list[str], glossaries: list[str] | None = None, query_dir: str | None = None) -> dict:
        tables = self.harvest_tables(databases)
        return {
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "engine": self.engine.name,
            "databases": databases,
            "tables": tables,
            "glossary_terms": self.harvest_glossary(glossaries),
            "queries": self.harvest_queries(tables, query_dir),
        }