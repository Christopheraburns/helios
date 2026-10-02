"""Rows of a lakehouse table as documents (DS-5), read through Impala as the
crawler identity. Each row is one asset whose content is a small JSON document
(``application/x-helios-row+json``: key and text columns); each text column
becomes one segment. Only validated identifiers reach SQL; filter values are
bound parameters, so no free SQL is ever run.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from helios_core.crawler.sources import TableRowsScope

from .base import Connector, SourceAsset

ROW_MIME = "application/x-helios-row+json"


def row_document(table: str, key: dict[str, Any], texts: dict[str, Any]) -> bytes:
    return json.dumps(
        {"table": table, "row_key": key, "text": texts},
        default=str,  # keeps column order
    ).encode()


class TableRowsConnector(Connector):
    TYPE = "table_rows"

    def __init__(self, source_id: str, scope: dict[str, Any], cursor: Callable[[], Any]):
        super().__init__(source_id)
        self.scope = TableRowsScope.model_validate(scope)
        self._cursor = cursor
        self._documents: dict[str, bytes] = {}

    def query(self) -> tuple[str, list[Any]]:
        s = self.scope
        columns = list(
            dict.fromkeys(
                [*s.key_columns, *s.text_columns]
                + ([s.timestamp_column] if s.timestamp_column else [])
            )
        )
        sql = f"SELECT {', '.join(columns)} FROM {s.table}"
        params = list(s.filters.values())
        if s.filters:
            sql += " WHERE " + " AND ".join(f"{column} = ?" for column in s.filters)
        sql += f" ORDER BY {', '.join(s.key_columns)} LIMIT {int(s.max_rows)}"
        return sql, params

    def list_assets(self) -> list[SourceAsset]:
        s = self.scope
        sql, params = self.query()
        cursor = self._cursor()
        cursor.execute(sql, params or None)
        names = [d[0] for d in cursor.description]
        assets = []
        self._documents = {}
        for row in cursor.fetchall():
            values = dict(zip(names, row, strict=True))
            key = {c: values[c] for c in s.key_columns}
            texts = {c: values[c] for c in s.text_columns}
            document = row_document(s.table, key, texts)
            asset_id = ",".join(f"{c}={key[c]}" for c in s.key_columns)
            self._documents[asset_id] = document
            timestamp = values.get(s.timestamp_column) if s.timestamp_column else None
            assets.append(
                SourceAsset(
                    asset_id=asset_id,
                    source=self.source_id,
                    mime_type=ROW_MIME,
                    locator={"connector_type": "table_rows", "table": s.table, "key": key},
                    version=hashlib.sha256(document).hexdigest(),
                    size_bytes=len(document),
                    sha256=hashlib.sha256(document).hexdigest(),
                    semantic_timestamp=None if timestamp is None else str(timestamp),
                )
            )
        return assets

    def _read(self, asset: SourceAsset) -> bytes | None:
        # Rows are read whole when listing; the content is the row document itself.
        return self._documents.get(asset.asset_id)
