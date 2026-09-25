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

## Persistent audit events

Migration 4 adds the append-only `audit_events` store to the same metadata
database. It records security-relevant API requests and mutations, MCP tool
calls, job lifecycle events, and an allowlist of significant UI actions. A
browser-generated `X-Helios-Session-ID` and server-generated request ID correlate
events across components; neither value is accepted as identity. The
authenticated Cloudera Principal remains authoritative.

Audit details are deliberately bounded and redacted. Helios does not persist
credentials, cookies, authorization headers, request/response bodies, prompts,
answers, raw SQL, query rows, or stack traces in product-visible audit records.
Audit persistence is fail-open: a write failure is reported to the API
Application's operational log without failing the user's operation.

Events are retained for 30 days by default. Set
`HELIOS_AUDIT_RETENTION_DAYS` to an integer from 1 through 3650. Expired records
are purged when API, MCP, and job processes initialize. The Activity Logs UI and
`/api/v1/audit/*` APIs show users only their own events. A Principal with
`organization.manage` can explicitly select organization-wide activity, but
cannot inspect another organization.

Product-visible audit events do not replace process logs. Uvicorn, UI server,
MCP, job, startup, and audit-write errors continue to go to stdout/stderr and
are viewed through Cloudera AI Application or Job logs.
