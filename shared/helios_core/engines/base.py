"""Engine adapter interface. Every SQL engine helios talks to implements this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]

    def dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


class Engine(ABC):
    name: str = "engine"
    sqlglot_dialect: str = ""

    @abstractmethod
    def query(self, sql: str, limit: int | None = 1000) -> QueryResult: ...

    @abstractmethod
    def ping(self) -> bool: ...

    def databases(self) -> list[str]:
        return [r[0] for r in self.query("SHOW DATABASES").rows]

    def tables(self, database: str) -> list[str]:
        return [r[0] for r in self.query(f"SHOW TABLES IN {database}").rows]

    def columns(self, database: str, table: str) -> list[dict]:
        res = self.query(f"DESCRIBE {database}.{table}")
        return [{"name": r[0], "type": r[1], "comment": r[2] if len(r) > 2 else ""} for r in res.rows]
