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
    def harvest_queries(self, query_dir: str | None = None, limit: int = 500) -> dict:
        queries, sources = [], []
        try:
            res = self.engine.query(
                f"SELECT sql FROM sys.impala_query_log WHERE query_type = 'QUERY' ORDER BY start_time_utc DESC LIMIT {limit}", limit)
            queries += [r[0] for r in res.rows if r[0]]
            sources.append("sys.impala_query_log")
        except Exception as e:  # noqa: BLE001 - not every VW exposes the log
            sources.append(f"sys.impala_query_log unavailable: {str(e)[:120]}")
        if query_dir and os.path.isdir(query_dir):
            for path in sorted(glob.glob(os.path.join(query_dir, "*.sql"))):
                with open(path) as f:
                    for stmt in re.split(r";\s*\n", f.read()):
                        if stmt.strip():
                            queries.append(stmt.strip())
            sources.append(query_dir)
        return {"sources": sources, "statements": queries}

    # ------------------------------------------------------------ all together
    def run(self, databases: list[str], glossaries: list[str] | None = None, query_dir: str | None = None) -> dict:
        return {
            "harvested_at": datetime.now(timezone.utc).isoformat(),
            "engine": self.engine.name,
            "databases": databases,
            "tables": self.harvest_tables(databases),
            "glossary_terms": self.harvest_glossary(glossaries),
            "queries": self.harvest_queries(query_dir),
        }
