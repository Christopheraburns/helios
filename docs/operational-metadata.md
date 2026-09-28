# Operational metadata

Helios stores organizations, principals, memberships, DataSource definitions,
Models, Model-to-DataSource references, and RBAC grants in SQLite. This is
separate from:

- discovery and profiling outputs under `runs/`;
- semantic and ontology artifacts under `models/`;
- glossary content in Atlas;
- connection credentials in Workbench environment/secret configuration.

SQLite is used through the standard library and is hidden behind
`helios_core.metadata.MetadataRepository`. The implementation enables foreign
keys and a busy timeout. The default journal mode is `DELETE`, because WAL
shared-memory files are unsafe when Cloudera Applications on different hosts
access the same project filesystem. Set `HELIOS_SQLITE_JOURNAL_MODE=WAL` only
for a verified single-host deployment. Resource ownership is checked in the
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

The API Application is the schema migration and audit-retention owner. MCP and
Jobs require an already initialized, healthy database and do not run migrations
or retention cleanup. They still read authorization state and append activity,
so SQLite remains an interim deployment choice: do not horizontally scale
Helios or run it on a project filesystem without reliable cross-host POSIX
locking. Move the repository implementation to a transactional shared database
before multi-replica or high-concurrency production use. Components depend on
the repository interface so that migration does not change API, MCP, job, or
authorization call sites.

## Integrity and recovery

API and MCP readiness execute a read-only SQLite quick check. They return HTTP
503 with `metadata_repository: unavailable` when corruption is detected. The
Applications log the finding but never silently delete or recreate metadata.

To repair metadata:

1. Stop the Helios API and MCP Applications and all Helios Jobs.
2. Run a read-only diagnosis from a Workbench Session:

   ```bash
   python - <<'PY'
   from helios_core.metadata import SQLiteMetadataRepository
   repository = SQLiteMetadataRepository()
   print(repository.path)
   print(repository.integrity_check())
   print(repository.integrity_check(thorough=True))
   PY
   ```

3. If diagnostics identify only a rebuildable non-constraint index, run:

   ```bash
   python -m helios_core.metadata.repair \
     --rebuild-index audit_events_principal_session_time_idx
   ```

4. If index rebuilding fails because the b-tree cannot be traversed, logically
   recover all readable tables into a clean database:

   ```bash
   python -m helios_core.metadata.repair --recover
   ```

Both repair modes refuse a non-empty WAL, create a timestamped raw backup under
`state/backups/`, and require quick and full integrity checks to pass. Recovery
atomically replaces the database only after validation. Never delete
`helios.db` as a repair shortcut: it contains organizations, grants, models,
conversations, and audit history.

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
Application's operational log without failing the user's operation. Repeated
identical persistence failures are logged at most once per minute with a
suppressed-event count, preventing a failing probe from flooding logs.

Events are retained for 30 days by default. Set
`HELIOS_AUDIT_RETENTION_DAYS` to an integer from 1 through 3650. Expired records
are purged when API, MCP, and job processes initialize. The Activity Logs UI and
`/api/v1/audit/*` APIs show users only their own events. A Principal with
`organization.manage` can explicitly select organization-wide activity, but
cannot inspect another organization.

Product-visible audit events do not replace process logs. Uvicorn, UI server,
MCP, job, startup, and audit-write errors continue to go to stdout/stderr and
are viewed through Cloudera AI Application or Job logs.
