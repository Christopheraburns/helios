"""Bolt access to the local Memgraph instance.

Only this module talks Bolt. Callers pass fixed Cypher with parameters; no
caller-supplied query text ever reaches the database.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

BOLT_URI = os.environ.get(
    "HELIOS_GRAPH_BOLT_URI",
    f"bolt://127.0.0.1:{os.environ.get('HELIOS_GRAPH_BOLT_PORT', '7687')}",
)


class BoltClient(Protocol):
    """Minimal surface the gateway needs from a Bolt driver."""

    driver_name: str

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


class _Neo4jClient:
    """Memgraph's recommended driver: the official neo4j Python package."""

    driver_name = "neo4j"

    def __init__(self, uri: str) -> None:
        from neo4j import GraphDatabase

        # Memgraph Community runs without authentication; Bolt still wants a tuple.
        self._driver = GraphDatabase.driver(uri, auth=("", ""))

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._driver.session() as session:
            return [record.data() for record in session.run(cypher, parameters or {})]

    def close(self) -> None:
        self._driver.close()


class _MgclientClient:
    """Memgraph's own driver, used when the image ships pymgclient instead."""

    driver_name = "mgclient"

    def __init__(self, uri: str) -> None:
        import mgclient

        host, _, port = uri.removeprefix("bolt://").partition(":")
        self._connection = mgclient.connect(host=host, port=int(port or 7687))
        self._connection.autocommit = True

    def query(self, cypher: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        cursor = self._connection.cursor()
        cursor.execute(cypher, parameters or {})
        rows = cursor.fetchall()
        columns = [description.name for description in cursor.description or []]
        return [dict(zip(columns, row)) for row in rows]

    def close(self) -> None:
        self._connection.close()


def connect(uri: str = BOLT_URI) -> BoltClient:
    """Open a Bolt client using whichever driver the runtime image provides."""
    errors: list[str] = []
    for factory in (_Neo4jClient, _MgclientClient):
        try:
            return factory(uri)
        except ImportError as exc:
            errors.append(f"{factory.driver_name}: {exc}")
    raise RuntimeError(
        "no Bolt driver available (tried neo4j, mgclient): " + "; ".join(errors)
    )
