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
| `helios_core.settings` | `Settings`, `ConnectionSettings`, `CatalogSettings`, `LLMSettings` | Stable | Resolution order, the config file and named connections and catalogs. A connection's `identity` (`service`, the default, `impersonate` or `token`) picks the engine identity mode. Core reads `HELIOS_*` and the LLM ecosystem's names (`ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `LLM_PROVIDER`). A subclass adds a platform's variables by overriding `profile_sources` (after the conventional environment, before the config file); helios-cloudera's `WorkbenchSettings` reads the Workbench and Cloudera names. |

### Semantic model and compiler

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.ossie.model` | `SemanticModel`, `Dataset`, `Field`, `Relationship`, `Metric` | Stable | The in-memory form of a published Ossie model. |
| `helios_core.compiler` | `Compiler`, `SemanticRequest`, `Filter`, `Measure`, `Compiled`, `CompileError`, `FilterValueTypeError` | Provisional | `compile(request, dialect)` needs a dialect: the engine's `dialect` or a SQLGlot dialect name; there is no default. |

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
| `helios_core.registry` | the model registry protocol (models, data sources, semantic revisions) | Planned | 4.1. The old console's SQLite repository moved to `apps/helios/console/metadata` in 2.6. |
| `helios_core.domain` | `Model`, `DataSource`, `DataSourceReference` | Provisional | Registry records with no ownership (organizations left in 2.6). 4.1 may reshape them with the registry. |

### Engines and LLM

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core.engines` | `Engine`, `QueryResult`, `engine_for`, `SqlDialect`, `dialect_for` | Provisional | An engine plugin (`helios.engines`) is an `Engine` subclass whose `from_connection(ConnectionSettings)` validates the connection's options and honours its `identity`; `engine_for(connection)` loads the plugin a connection names and builds it. `query(sql, caller=None, *, limit)` runs as the service identity with no caller, and as the caller under the connection's identity mode otherwise; `identity_modes` lists the modes an engine supports. Plugins register under `helios.engines` (`helios_core.plugins`). An engine's `dialect` (a `SqlDialect`) renders the compiler's queries and Helios's own statements; plugins subclass it for what SQLGlot lacks (`render_query`, `count_distinct`, `identifier`). The catalog and statistics methods (`databases`, `tables`, `columns`, `row_count`, `recent_queries`) default to `information_schema` and `COUNT(*)`. |
| `helios_core.engines.duckdb_engine` | `DuckDBEngine`, `DuckDBOptions` | Provisional | `engine = "duckdb"`, option `path` (default `:memory:`); `service` mode only. |
| `helios_core.engines.sqlalchemy_engine` | `SqlAlchemyEngine`, `SqlAlchemyOptions` | Provisional | `engine = "sqlalchemy"`, option `url`; needs the `[sqlalchemy]` extra and the database's driver. `service` mode only; introspection through the SQLAlchemy inspector. |
| `helios_core.engines.trino_engine` | `TrinoEngine`, `TrinoOptions`, `TrinoIdentityError` | Provisional | `engine = "trino"`, options `host`, `port`, `catalog`, `schema`, `user`, `password` (basic authentication, https only), `http_scheme`, `verify`; needs the `[trino]` extra. All three identity modes: `service` (the service user), `impersonate` (authenticates as the service user, runs the session as the caller and checks `current_user`), `token` (the caller's JWT). |
| `helios_core.catalog` | `catalog_for`, `capabilities`, `GLOSSARY`, `LINEAGE`, `GlossaryCatalog`, `LineageCatalog`, `ColumnLineage`, `CatalogError` | Provisional | A catalog plugin (`helios.catalogs`) is a class whose `from_catalog(CatalogSettings)` builds it; `catalog_for` loads the one a `[catalogs.<name>]` entry names. Its capabilities are optional and detected from what it implements: `glossary` (`GlossaryCatalog`) and `lineage` (`LineageCatalog.column_lineage` → `ColumnLineage`); a plugin with neither is refused. Tables and columns come from the engine, so no catalog is needed to harvest. Atlas (helios-cloudera) offers both. `Harvester` itself moves to helios-model in 5.3. |
| `helios_core.llm` | `LLMClient`, `LLMError`, `LLMTimeoutError`, `llm_from_settings` | Stable | Needs the `[llm]` extra; without it, importing the module says so. |

### Identity and plugins

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_core` | `IdentityMode`, `IdentityError` | Stable | `service`, `impersonate`, `token`; `IdentityError` (a `PermissionError`) when a caller lacks the username or token its mode needs. |
| `helios_core.data_authorization` | `DataPolicy`, `DataResource`, `DataAction`, `EngineIdentityAuthorizer`, `PlatformDataAuthorizer`, `PlatformDataDecision`, `DataRestriction`, `DataRestrictionDecision`, `DataAuthorizationDecision`, `DataAuthorizationDenied` | Stable | Platform enforcement plus deny-only host restrictions. The default platform authorizer allows query execution only when `DataResource.identity_mode` runs it as the caller. |
| `helios_core.visibility` | `ModelVisibility`, `AllModels`, `ModelNotVisible`, `visible_models`, `require_visible` | Stable | The host decides which models a caller may list and use; the default shows every model in the registry. A hidden model raises the same `LookupError` as a missing one. |
| `helios_core.audit_sink` | `AuditRecord`, `AuditSink`, `LoggingAuditSink`, `record_safely`, `sql_fingerprint` | Stable | Libraries emit `AuditRecord`s (action, outcome, model, caller username, row count, SQL fingerprint, error); the host's sink stores them. The default logs to `helios_core.audit_sink`. New optional fields are allowed. |
| `helios_core` | `Caller` | Stable | The host's caller: `username`, `token` (left out of `repr`, never logged) and free-form `attributes`, all optional. Helios authenticates no one; the engine's identity mode (2.2) decides which field it uses. |
| `helios_core.plugins` | `load`, `installed`, `names`, `missing_configured`, `require_configured`, `PluginInfo`, `PluginError`, `PluginNotFound`, `PluginLoadError`, `ENGINES`, `CATALOGS`, `STORES`, `TOOLS`, `COMMANDS`, `GROUPS` | Provisional | Entry-point groups `helios.engines`, `helios.catalogs`, `helios.stores`, `helios.tools`, `helios.commands`; the group names are Stable. What a plugin object must provide is set per group: engines in 3.5 (an `Engine` subclass with `from_connection`; see `helios_core.engines`), catalogs in 3.7 (a class with `from_catalog`; see `helios_core.catalog`), stores in 4.x, tools in 5.x, commands in 5.5. Settings that name a missing plugin still load; `require_configured` is the host's startup check. |

## Internal

Everything not in a table above is internal. Most of it is leaving helios-core, which is
why it stays where it is instead of moving under `_internal/` and then moving again:

| Code | Where it goes |
|---|---|
| `runs.py` | Onto `ArtifactStore` (4.4) |
| `catalog/harvest.py`, `profiler/`, `propose/`, `ossie/build.py` | helios-model (5.3) |
| `ontology/parser.py`, the rest of `ontology/mapping.py`, `index/` modules other than the store and records | Reviewed for promotion when helios-crawl is built (Phase 10) |
| `_pending/` | helios-model, helios-serve and helios-crawl; import-linter keeps other code from importing it |

## Typing

helios-core ships `py.typed`. The type annotations on contract names are part of the contract.
