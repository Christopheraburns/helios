"""Alembic environment for the registry schema. SqlRegistry.upgrade() passes the
connection in config.attributes; there is no offline (SQL script) mode."""
from alembic import context

from helios_core.registry.sql import VERSION_TABLE, metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run registry migrations through SqlRegistry.upgrade()")

context.configure(
    connection=connection,
    target_metadata=metadata,
    version_table=VERSION_TABLE,
    render_as_batch=connection.dialect.name == "sqlite",  # SQLite alters tables by copying them
)
with context.begin_transaction():
    context.run_migrations()
