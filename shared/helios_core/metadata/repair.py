"""Backup-first repair utilities for Helios operational metadata.

Run only after stopping Helios API, MCP, and metadata-writing Jobs:

    python -m helios_core.metadata.repair --rebuild-index INDEX_NAME
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .sqlite import SQLiteMetadataRepository, default_database_path


def backup_database(path: Path, backup_root: Path | None = None) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"metadata database not found: {path}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = (backup_root or path.parent / "backups") / timestamp
    destination.mkdir(parents=True, exist_ok=False)
    for source in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            shutil.copy2(source, destination / source.name)
        except FileNotFoundError:
            if source == path:
                raise
    return destination


def rebuild_index(path: Path, index_name: str) -> Path:
    wal = Path(f"{path}-wal")
    active = [wal] if wal.exists() and wal.stat().st_size else []
    if active:
        names = ", ".join(str(item) for item in active)
        raise RuntimeError(
            "SQLite WAL sidecars are not empty. Stop every Helios Application "
            f"and Job before repair: {names}"
        )

    backup = backup_database(path)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        row = connection.execute(
            "SELECT type, sql FROM sqlite_master WHERE name = ?",
            (index_name,),
        ).fetchone()
        if row is None or row[0] != "index" or not row[1]:
            raise ValueError(f"SQLite index does not exist: {index_name}")
        create_sql = str(row[1])
        escaped = index_name.replace('"', '""')
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute(f'DROP INDEX "{escaped}"')
        connection.execute(create_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    repository = SQLiteMetadataRepository(path)
    quick = repository.integrity_check()
    thorough = repository.integrity_check(thorough=True)
    if quick != ("ok",) or thorough != ("ok",):
        raise RuntimeError(
            "index rebuild completed but integrity checks still fail; "
            f"quick_check={quick!r}, integrity_check={thorough!r}. "
            f"Restore or recover from {backup}"
        )
    return backup


def recover_database(path: Path) -> Path:
    wal = Path(f"{path}-wal")
    if wal.exists() and wal.stat().st_size:
        raise RuntimeError(
            f"SQLite WAL is not empty. Stop every Helios Application and Job before recovery: {wal}"
        )
    backup = backup_database(path)
    recovered = path.with_name(f".{path.name}.recovered")
    if recovered.exists():
        recovered.unlink()

    source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(recovered)
    try:
        target.execute("PRAGMA foreign_keys = OFF")
        target.execute("PRAGMA journal_mode = DELETE")
        schema = source.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL
              AND name NOT LIKE 'sqlite_%'
            ORDER BY
              CASE type
                WHEN 'table' THEN 0
                WHEN 'index' THEN 1
                WHEN 'view' THEN 2
                WHEN 'trigger' THEN 3
                ELSE 4
              END,
              name
            """
        ).fetchall()
        tables = [row for row in schema if row[0] == "table"]
        for _, _, sql in tables:
            target.execute(sql)
        for _, name, _ in tables:
            escaped = name.replace('"', '""')
            columns = source.execute(f'PRAGMA table_info("{escaped}")').fetchall()
            placeholders = ", ".join("?" for _ in columns)
            insert = f'INSERT INTO "{escaped}" VALUES ({placeholders})'
            cursor = source.execute(f'SELECT * FROM "{escaped}"')
            while rows := cursor.fetchmany(500):
                target.executemany(insert, rows)
        for kind, _, sql in schema:
            if kind != "table":
                target.execute(sql)
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()

    repository = SQLiteMetadataRepository(recovered)
    quick = repository.integrity_check()
    thorough = repository.integrity_check(thorough=True)
    if quick != ("ok",) or thorough != ("ok",):
        raise RuntimeError(
            "logical recovery failed integrity checks; "
            f"quick_check={quick!r}, integrity_check={thorough!r}. "
            f"Original backup is at {backup}"
        )

    for sidecar in (Path(f"{path}-wal"), Path(f"{path}-shm")):
        if sidecar.exists():
            sidecar.unlink()
    os.replace(recovered, path)
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up and repair Helios SQLite metadata.")
    parser.add_argument(
        "--database",
        default=default_database_path(),
        help="metadata database path (defaults to HELIOS_METADATA_DB)",
    )
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--rebuild-index",
        help="confirmed corrupt index to rebuild",
    )
    operation.add_argument(
        "--recover",
        action="store_true",
        help="logically copy tables into a clean database and recreate indexes",
    )
    args = parser.parse_args()
    path = Path(args.database).resolve()
    if args.recover:
        backup = recover_database(path)
        print("recovered database into a clean SQLite file")
    else:
        backup = rebuild_index(path, args.rebuild_index)
        print(f"rebuilt {args.rebuild_index}")
    print(f"backup: {backup}")
    print("quick_check: ok")
    print("integrity_check: ok")


if __name__ == "__main__":
    main()
