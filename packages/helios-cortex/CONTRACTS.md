# helios-cortex contracts

This file is helios-cortex's public API. The names in the tables below are the semantic-versioning
boundary: other packages (helios-model, helios-serve, helios-mcp, plugins and host
applications) may depend on them, and they change only as the policy below allows. Everything
else in `helios_cortex` is internal, and can change or disappear in any release.

`tests/test_contracts.py` imports every name listed here from the module given, so the tables
cannot drift from the code.

## Stability levels

- **Stable.** Changed only under the deprecation policy.
- **Provisional.** Public, but a named burndown task will reshape it. Depend on it if you need
  to, and expect to update your code when that task lands. It becomes Stable afterwards.
- **Planned.** Not written yet. Listed so that the boundary is complete.

## Deprecation policy

helios-cortex is pre-1.0 (0.y.z).

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
| `helios_cortex.settings` | `Settings`, `ConnectionSettings`, `CatalogSettings`, `LLMSettings` | Stable | Resolution order, the config file and named connections and catalogs. A connection's `identity` (`service`, the default, `impersonate` or `token`) picks the engine identity mode. Core reads `HELIOS_*` and the LLM ecosystem's names (`ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`, `LLM_PROVIDER`). A subclass adds a platform's variables by overriding `profile_sources` (after the conventional environment, before the config file); helios-cloudera's `WorkbenchSettings` reads the Workbench and Cloudera names. `registry_url` (secret) names the model registry's database. `artifact_storage` (`location` plus filesystem options, secrets hidden) puts artifacts on object storage. |
| `helios_cortex.settings` | `load_settings`, `environment_caller_token` | Provisional | How a command builds its Settings (5.5): `load_settings(profile, config)` uses the settings profile named by `profile` or `HELIOS_PROFILE` (a `Settings` subclass registered under `helios.profiles`; never chosen from the environment it describes), else `Settings`; `config` replaces the config-file search. `environment_caller_token()` is `HELIOS_CALLER_TOKEN`, the only place a command takes a caller's token from. |

### Semantic model and compiler

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_cortex.ossie.model` | `SemanticModel`, `Dataset`, `Field`, `Relationship`, `Metric` | Stable | The in-memory form of a published Ossie model. |
| `helios_cortex.ossie` | `validate`, `dump_yaml`, `SPEC_VERSION` | Provisional | The Ossie format (`helios_cortex.ossie.format`): the spec version Helios writes, validation against the vendored schema plus referential checks, and YAML output. Building a document from a discovery proposal is helios-model's (`helios_model.build`). |
| `helios_cortex.compiler` | `Compiler`, `SemanticRequest`, `Filter`, `Measure`, `Compiled`, `CompileError`, `FilterValueTypeError` | Provisional | `compile(request, dialect)` needs a dialect: the engine's `dialect` or a SQLGlot dialect name; there is no default. |

### Ontology

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_cortex.ontology` | `OntologyGraph`, `GraphNode`, `GraphEdge`, `OntologyGraphError` | Stable | |
| `helios_cortex.ontology` | `SourceMapping`, `load_mapping`, `load_mappings` | Stable | |

### Index

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_cortex.index` | `IndexStore` | Stable | |
| `helios_cortex.index` | `OntologyVersionRecord`, `OntologyActivationRecord`, `CrawlRunRecord`, `AssetRecord`, `SegmentRecord`, `MentionRecord`, `EntityRecord`, `EntityLinkRecord`, `RelationshipRecord`, `ClaimRecord`, `ClaimEvidenceRecord`, `EvaluationRecord` | Stable | Rows of the `helios_index` tables. New optional fields are allowed. |

### Storage

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_cortex.artifacts` | `ArtifactStore`, `Activation`, `ActivationConflict`, `ArtifactError`, `ANY` | Provisional | On a local directory or object storage (`ArtifactStore.from_settings`: `[artifact_storage]` else `artifacts_dir`). Published revisions are immutable (named by their YAML's SHA-256, written once); the active revision is the newest entry of `models/<id>/active/`, and `activate(..., expected_sequence=)` is a compare-and-swap (`ActivationConflict`). `read_published_ossie` / `read_revision` work on every backend and verify hashes; methods returning a local `Path` work on a local directory only. Discovery runs live in the same storage under `runs/` (`ArtifactStore.runs`). |
| `helios_cortex.artifact_storage` | `ArtifactStorage`, `storage_for`, `LocalStorage` | Provisional | `storage_for(location, **options)`: a path or `file://`, `s3://` (`[s3]`), `abfs://` / `abfss://` / `az://` (`[azure]`), `gs://` (`[gcs]`), `ofs://` through Ozone HttpFS (`[ozone]`). The one conditional operation is `create` (write only if absent, atomically), on which immutability and the activation log are built. |
| `helios_cortex.runs` | `RunStore` | Provisional | Discovery runs, `<run_id>/{harvest,profile,propose,review}.json`: `write`, `load`, `run_ids`, `latest_run_id`, `list_runs`, `detail`. On any storage: `ArtifactStore.runs` (under `runs/`), `RunStore.from_settings` (with the artifacts on object storage, else `runs_dir`), or `RunStore(path)` for a local runs directory. `runs_dir` is a local path, None on object storage. |
| `helios_cortex.conformance` | `RegistryConformance`, `ArtifactStorageConformance` | Provisional | The tests every backend must pass, Helios's and third parties': subclass one and provide a `registry` or `storage` fixture returning an empty backend. The registry suite checks the `ModelRegistry` rules, including concurrent writers; the storage suite checks the operations (byte-exact content, listing at the prefix boundary, `create` atomic under 10 racing writers) and runs `ArtifactStore` and `RunStore` on the backend. Needs the `[testing]` extra (pytest). New tests may be added in minor releases; a backend that passed may then fail, which is the point. |
| `helios_cortex.registry` | `ModelRegistry`, `InMemoryRegistry`, `SqlRegistry`, `RegisteredModel`, `SemanticRevision`, `RegistryError`, `RegistryNotReady`, `RevisionConflict`, `DRAFT`, `PUBLISHED` | Provisional | What libraries need to find models: data sources, models (status, creation) and their immutable semantic revisions. Every backend keeps the rules in the module docstring and passes `RegistryConformance`. `InMemoryRegistry` is the reference; `SqlRegistry` stores the records in any SQLAlchemy database by URL (`Settings.registry_url`, default a SQLite file in the data directory), with Alembic migrations in the package (`helios registry upgrade`; version table `helios_registry_version`, tables prefixed `helios_`); connections are checked before use, so a database failover does not fail the next call). No ownership: which models a caller sees is the host's `ModelVisibility`. The old console keeps its own SQLite repository until Phase 8, sharing `SemanticRevision`. |
| `helios_cortex.domain` | `Model`, `DataSource`, `DataSourceReference` | Provisional | Registry records with no ownership (organizations left in 2.6). The registry stores them as they are. |

### Engines and LLM

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_cortex.engines` | `Engine`, `QueryResult`, `engine_for`, `SqlDialect`, `dialect_for` | Provisional | An engine plugin (`helios.engines`) is an `Engine` subclass whose `from_connection(ConnectionSettings)` validates the connection's options and honours its `identity`; `engine_for(connection)` loads the plugin a connection names and builds it. `query(sql, caller=None, *, limit)` runs as the service identity with no caller, and as the caller under the connection's identity mode otherwise; `identity_modes` lists the modes an engine supports. Plugins register under `helios.engines` (`helios_cortex.plugins`). An engine's `dialect` (a `SqlDialect`) renders the compiler's queries and Helios's own statements; plugins subclass it for what SQLGlot lacks (`render_query`, `count_distinct`, `identifier`). The catalog and statistics methods (`databases`, `tables`, `columns`, `row_count`, `recent_queries`) default to `information_schema` and `COUNT(*)`; each takes a keyword-only `caller=None` with the same meaning as `query`'s, so a harvest sees what its caller may see, and a plugin that overrides one must accept and honour it. |
| `helios_cortex.engines.duckdb_engine` | `DuckDBEngine`, `DuckDBOptions` | Provisional | `engine = "duckdb"`, option `path` (default `:memory:`); `service` mode only. |
| `helios_cortex.engines.sqlalchemy_engine` | `SqlAlchemyEngine`, `SqlAlchemyOptions` | Provisional | `engine = "sqlalchemy"`, option `url`; needs the `[sqlalchemy]` extra and the database's driver. `service` mode only; introspection through the SQLAlchemy inspector. |
| `helios_cortex.engines.trino_engine` | `TrinoEngine`, `TrinoOptions`, `TrinoIdentityError` | Provisional | `engine = "trino"`, options `host`, `port`, `catalog`, `schema`, `user`, `password` (basic authentication, https only), `http_scheme`, `verify`; needs the `[trino]` extra. All three identity modes: `service` (the service user), `impersonate` (authenticates as the service user, runs the session as the caller and checks `current_user`), `token` (the caller's JWT). |
| `helios_cortex.catalog` | `catalog_for`, `capabilities`, `GLOSSARY`, `LINEAGE`, `GlossaryCatalog`, `LineageCatalog`, `ColumnLineage`, `CatalogError` | Provisional | A catalog plugin (`helios.catalogs`) is a class whose `from_catalog(CatalogSettings)` builds it; `catalog_for` loads the one a `[catalogs.<name>]` entry names. Its capabilities are optional and detected from what it implements: `glossary` (`GlossaryCatalog`) and `lineage` (`LineageCatalog.column_lineage` → `ColumnLineage`); a plugin with neither is refused. Tables and columns come from the engine, so no catalog is needed to harvest. Atlas (helios-cloudera) offers both. Harvesting with a catalog is helios-model's (`helios_model.Catalog`, over `helios_model.harvest.TableHarvester`); the glossary is read with the catalog's own credentials, not the caller's. |
| `helios_cortex.llm` | `LLMClient`, `LLMError`, `LLMTimeoutError`, `llm_from_settings` | Stable | Needs the `[llm]` extra; without it, importing the module says so. |

### Identity and plugins

| Module | Names | Level | Notes |
|---|---|---|---|
| `helios_cortex` | `IdentityMode`, `IdentityError` | Stable | `service`, `impersonate`, `token`; `IdentityError` (a `PermissionError`) when a caller lacks the username or token its mode needs. |
| `helios_cortex.data_authorization` | `DataPolicy`, `DataResource`, `DataAction`, `EngineIdentityAuthorizer`, `PlatformDataAuthorizer`, `PlatformDataDecision`, `DataRestriction`, `DataRestrictionDecision`, `DataAuthorizationDecision`, `DataAuthorizationDenied` | Stable | Platform enforcement plus deny-only host restrictions. The default platform authorizer allows query execution only when `DataResource.identity_mode` runs it as the caller. |
| `helios_cortex.visibility` | `ModelVisibility`, `AllModels`, `ModelNotVisible`, `visible_models`, `require_visible` | Stable | The host decides which models a caller may list and use; the default shows every model in the registry. A hidden model raises the same `LookupError` as a missing one. |
| `helios_cortex.audit_sink` | `AuditRecord`, `AuditSink`, `LoggingAuditSink`, `record_safely`, `sql_fingerprint` | Stable | Libraries emit `AuditRecord`s (action, outcome, model, caller username, row count, SQL fingerprint, error); the host's sink stores them. The default logs to `helios_cortex.audit_sink`. New optional fields are allowed. |
| `helios_cortex` | `Caller` | Stable | The host's caller: `username`, `token` (left out of `repr`, never logged) and free-form `attributes`, all optional. Helios authenticates no one; the engine's identity mode (2.2) decides which field it uses. |
| `helios_cortex.plugins` | `load`, `installed`, `names`, `missing_configured`, `require_configured`, `PluginInfo`, `PluginError`, `PluginNotFound`, `PluginLoadError`, `ENGINES`, `CATALOGS`, `STORES`, `TOOLS`, `COMMANDS`, `PROFILES`, `GROUPS` | Provisional | Entry-point groups `helios.engines`, `helios.catalogs`, `helios.stores`, `helios.tools`, `helios.commands`, `helios.profiles`; the group names are Stable. What a plugin object must provide is set per group: engines in 3.5 (an `Engine` subclass with `from_connection`; see `helios_cortex.engines`), catalogs in 3.7 (a class with `from_catalog`; see `helios_cortex.catalog`), stores in 4.x, tools in 5.x, commands in 5.5 (see `helios_cortex.cli`), profiles in 5.5 (a `Settings` subclass; see `load_settings`). Settings that name a missing plugin still load; `require_configured` is the host's startup check. |
| `helios_cortex.cli` | `main`, `Command`, `CommandContext`, `CommandError`, `add_caller_argument` | Provisional | The `helios` command (5.5). `main(argv=None, *, out=None, stderr=None)` returns the exit status: 0, 1 for a `CommandError` (shown as one line, no traceback), 2 for a usage error. A `helios.commands` plugin, named by its subcommand, is a `Command` (or a class built with no arguments): `help`, `configure(parser)`, `run(args, context) -> int`. `CommandContext` gives it `settings` (built on first use, honouring `--profile` and `--config`), `caller(username)` (None, the service identity, unless `--as` or `HELIOS_CALLER_TOKEN` gives one) and `echo`/`out` for its result; progress goes through logging, which `main` sends to stderr while the command runs. helios-cortex's own commands are in `helios_cortex.commands`. |

## Internal

Everything not in a table above is internal. Most of it is leaving helios-cortex, which is
why it stays where it is instead of moving under `_internal/` and then moving again:

| Code | Where it goes |
|---|---|
| `ontology/parser.py`, the rest of `ontology/mapping.py`, `index/` modules other than the store and records | Reviewed for promotion when helios-crawl is built (Phase 10) |
| `_pending/` | helios-crawl (only crawl code is left: `crawler`, `crawler_settings`, `evidence`, `browse`); import-linter keeps other code from importing it |

## Typing

helios-cortex ships `py.typed`. The type annotations on contract names are part of the contract.
