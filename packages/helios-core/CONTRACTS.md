# helios-core contracts

This file is helios-core's public API. The names in the tables below are the semantic-versioning
boundary: other packages (helios-model, helios-serve, helios-mcp, plugins and host
applications) may depend on them, and they change only as the policy below allows. Everything
else in `helios_core` is internal, and can change or disappear in any release.

`tests/test_contracts.py` imports every name listed here from the module given, so the tables
cannot drift from the code.

## Stability levels

- **Stable.** Changed only under the deprecation policy.
- **Provisional.** Public, but a named burndown task will reshape it. Depend on it if you need
  to, and expect to update your code when that task lands. It becomes Stable afterwards.
- **Planned.** Not written yet. Listed so that the boundary is complete.

## Deprecation policy

helios-core is pre-1.0 (0.y.z).

- **While pre-1.0:** a release that removes or changes a Stable name bumps the minor version (y).
  Where it can, the old spelling keeps working for one more minor release and emits a
  `DeprecationWarning` naming its replacement. Patch releases (z) never change a Stable name.
- **From 1.0:** standard semantic versioning. A Stable name is deprecated in a minor release
  (with a `DeprecationWarning` and a changelog entry) and removed no earlier than the next
  major release.
- **What counts as a change:** removing or renaming a name, removing a parameter or field,
  making an optional parameter required, narrowing an accepted type, or changing the meaning
  of a return value. Adding optional parameters, fields or methods is not a change.
- **Provisional names:** may change in any minor release. The changelog says how to migrate.

## Contract

### Configuration

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.settings` | `Settings`, `ConnectionSettings`, `CatalogSettings`, `LLMSettings` | Stable | Resolution order, the config file and named connections and catalogs. A connection's `identity` (`service`, the default, `impersonate` or `token`) picks the engine identity mode. The legacy environment names move to helios-cloudera in 3.4; `HELIOS_*` names stay. |

### Semantic model and compiler

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.ossie.model` | `SemanticModel`, `Dataset`, `Field`, `Relationship`, `Metric` | Stable | The in-memory form of a published Ossie model. |
| `helios_core.compiler` | `Compiler`, `SemanticRequest`, `Filter`, `Measure`, `Compiled`, `CompileError`, `FilterValueTypeError` | Provisional | Dialect hooks move to engine plugins in 3.6. |

### Ontology

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.ontology` | `OntologyGraph`, `GraphNode`, `GraphEdge`, `OntologyGraphError` | Stable | |
| `helios_core.ontology` | `SourceMapping`, `load_mapping`, `load_mappings` | Stable | |

### Index

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.index` | `IndexStore` | Stable | |
| `helios_core.index` | `OntologyVersionRecord`, `OntologyActivationRecord`, `CrawlRunRecord`, `AssetRecord`, `SegmentRecord`, `MentionRecord`, `EntityRecord`, `EntityLinkRecord`, `RelationshipRecord`, `ClaimRecord`, `ClaimEvidenceRecord`, `EvaluationRecord` | Stable | Rows of the `helios_index` tables. New optional fields are allowed. |

### Storage

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.artifacts` | `ArtifactStore` | Provisional | Object-storage backends selected by URL in 4.3. |
| `helios_core.metadata` | `MetadataRepository`, `SemanticRevision` | Provisional | The registry protocol. 4.1 splits out a model registry (models, data sources, semantic revisions); the conversation, trace and evaluation parts leave core. |
| `helios_core.domain` | `Model`, `DataSource`, `DataSourceReference` | Provisional | Organization ownership leaves with identity in Phase 2. |

### Engines and LLM

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.engines` | `Engine`, `QueryResult` | Provisional | `query(sql, caller=None, *, limit)` runs as the service identity with no caller, and as the caller under the connection's identity mode otherwise; `identity_modes` lists the modes an engine supports. Plugins register under `helios.engines` in 3.1. |
| `helios_core.llm` | `LLMClient`, `LLMError`, `LLMTimeoutError`, `llm_from_settings` | Stable | |

### Identity and plugins

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core` | `IdentityMode`, `IdentityError` | Stable | `service`, `impersonate`, `token`; `IdentityError` (a `PermissionError`) when a caller lacks the username or token its mode needs. |
| `helios_core.data_authorization` | `DataPolicy`, `DataResource`, `DataAction`, `EngineIdentityAuthorizer`, `PlatformDataAuthorizer`, `PlatformDataDecision`, `DataRestriction`, `DataRestrictionDecision`, `DataAuthorizationDecision`, `DataAuthorizationDenied` | Stable | Platform enforcement plus deny-only host restrictions. The default platform authorizer allows query execution only when `DataResource.identity_mode` runs it as the caller. |
| `helios_core` | `Caller` | Stable | The host's caller: `username`, `token` (left out of `repr`, never logged) and free-form `attributes`, all optional. Helios authenticates no one; the engine's identity mode (2.2) decides which field it uses. |
| `helios_core.plugins` | entry-point groups `helios.engines`, `helios.catalogs`, `helios.stores`, `helios.tools`, `helios.commands` | Planned | 3.1. |

## Internal

Everything not in a table above is internal. Most of it is leaving helios-core, which is
why it stays where it is instead of moving under `_internal/` and then moving again:

| Code | Where it goes |
|---|---|
| `authz/`, `authorization.py`, `rbac.py`, `identity.py`, `delegation.py`, `metadata/users.py` | The old console, then deleted (2.6, Phase 8) |
| `audit.py` | The `AuditSink` hook (2.5) |
| `engines/impala.py`, `engines/impala_diagnose.py`, `atlas/` | helios-cloudera (3.3) |
| `engines/duckdb_engine.py` | Stays as the built-in DuckDB engine plugin (3.5) |
| `metadata/sqlite.py`, `metadata/migrations.py`, `metadata/repair.py`, `metadata/setup.py`, `tracing.py` | The SQLAlchemy registry backend; console tables leave core (4.1, 4.2) |
| `runs.py` | Onto `ArtifactStore` (4.4) |
| `catalog/`, `profiler/`, `propose/`, `ossie/build.py` | helios-model (5.3) |
| `ontology/parser.py`, the rest of `ontology/mapping.py`, `index/` modules other than the store and records | Reviewed for promotion when helios-crawl is built (Phase 10) |
| `_pending/` | helios-model, helios-serve and helios-crawl; import-linter keeps other code from importing it |

## Typing

helios-core ships `py.typed`. The type annotations on contract names are part of the contract.
