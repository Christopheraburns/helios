"""Engine adapter interface. Every SQL engine helios talks to implements this."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from ..caller import Caller, IdentityError, IdentityMode


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]

    def dicts(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]


class Engine(ABC):
    """A SQL engine. ``query(sql)`` with no caller runs as the connection's own
    (service) identity, for Helios's own work: harvesting, profiling, its index.
    ``query(sql, caller)`` runs a caller's query under ``identity_mode``, which the
    host picks per connection from the modes the engine supports."""

    name: str = "engine"
    sqlglot_dialect: str = ""
    identity_modes: ClassVar[frozenset[IdentityMode]] = frozenset({IdentityMode.SERVICE})

    def __init__(self, identity_mode: IdentityMode | str = IdentityMode.SERVICE):
        mode = IdentityMode(identity_mode)
        if mode not in self.identity_modes:
            supported = ", ".join(sorted(m.value for m in self.identity_modes))
            raise ValueError(f"the {self.name} engine does not support identity mode {mode.value!r} (supported: {supported})")
        self.identity_mode = mode

    def check_caller(self, caller: Caller | None) -> None:
        """Raise IdentityError when ``caller`` lacks what ``identity_mode`` needs."""
        if caller is None or self.identity_mode is IdentityMode.SERVICE:
            return
        if self.identity_mode is IdentityMode.IMPERSONATE and caller.username is None:
            raise IdentityError("impersonate mode needs the caller's username")
        if self.identity_mode is IdentityMode.TOKEN and caller.token is None:
            raise IdentityError("token mode needs the caller's token")

    @abstractmethod
    def query(self, sql: str, caller: Caller | None = None, *, limit: int | None = 1000) -> QueryResult: ...

    @abstractmethod
    def ping(self) -> bool: ...

    def databases(self) -> list[str]:
        return [r[0] for r in self.query("SHOW DATABASES").rows]

    def tables(self, database: str) -> list[str]:
        return [r[0] for r in self.query(f"SHOW TABLES IN {database}").rows]

    def columns(self, database: str, table: str) -> list[dict]:
        res = self.query(f"DESCRIBE {database}.{table}")
        return [{"name": r[0], "type": r[1], "comment": r[2] if len(r) > 2 else ""} for r in res.rows]
