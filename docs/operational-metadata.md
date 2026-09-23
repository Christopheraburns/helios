# Operational metadata

Helios stores organizations, principals, memberships, DataSource definitions,
Models, Model-to-DataSource references, and RBAC grants in SQLite. This is
separate from:

- discovery and profiling outputs under `runs/`;
- semantic and ontology artifacts under `models/`;
- glossary content in Atlas;
- connection credentials in Workbench environment/secret configuration.

SQLite is used through the standard library and is hidden behind
`helios_core.metadata.MetadataRepository`. The initial implementation enables
foreign keys, WAL mode, and a busy timeout. Resource ownership is checked in the
repository and constrained in the schema; a Model cannot reference a DataSource
from another organization.

The default database is `state/helios.db` under `HELIOS_ROOT`. Set
`HELIOS_METADATA_DB` to override it. The console creates/upgrades the schema on
startup. It can also be prepared explicitly:

```bash
python -m helios_core.metadata.setup
```

Migrations are append-only entries in `helios_core.metadata.migrations`.
`schema_migrations` records applied versions. Add a new numbered migration rather
than modifying one that has shipped.

The SQLite implementation is appropriate for initial Workbench deployment and
metadata volume. Components should depend on the repository interface, not
SQLite queries, so a future shared database can replace it without changing API,
MCP, job, or authorization call sites.
