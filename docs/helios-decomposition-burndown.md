# Helios decomposition — burndown

Source: `docs/Helios decomposition plan.pdf` (2026-10-29), revised by the decisions below
(2026-10-29). Where the two disagree, this file wins.

Goal: Helios becomes a general-use set of Python libraries in one uv workspace (`helios-core`,
`helios-model`, `helios-serve`, `helios-mcp`, `helios-cloudera`, and later `helios-crawl`). An
everyday developer installs them from PyPI and runs them on a laptop; a host project installs
them on Cloudera AI Workbench like any other package. Daedalus serves Helios's tools over MCP to
shared, governed deployments.

Estimates are engineer-days for one engineer who knows the codebase. Every phase keeps the test
suite green. The old app in `apps/helios` is scaffolding whose tests protect the logic while it
moves; it is not kept deployable (decision 9).

## Decisions

1. **Libraries only.** No Helios package ships a Workbench Application, launcher, Job script or
   runtime image. The host project owns those and passes configuration in; libraries never read
   `CDSW_*` or assume `/home/cdsw`.
2. **No MCP server application in Helios.** helios-serve exposes the tools as plain functions
   plus their metadata. For shared deployments, Daedalus serves them over MCP through a built-in
   pack (`services/factory/packs/helios.py`). Helios drops the bearer-token gate, the signed
   principal header, `server/discover` and the JWT login path. (Amended by 8.)
3. **Pluggable storage, not Postgres-only.** Registry rows go through any SQLAlchemy URL (SQLite for
   development, Postgres or another database the customer already runs for production). Model
   files go to a local directory or object storage (S3 / ADLS / GCS / Ozone). Backends are
   plugins.
4. **Helios does not manage identity.** The host passes an opaque caller (username and/or token)
   on each call; engines forward it to the platform, which enforces access (Ranger on Cloudera).
   Modes, named generically: `token` pass-through (preferred), `impersonate` (a service account
   acting as the user; Cloudera's `doAs`) and `service` (the local default). Helios's own users,
   organizations, roles and grants leave the libraries and are deleted with the old console.
   Which models a caller may see is a host-supplied hook.
5. **Plugin architecture.** Capabilities register through Python entry points (`helios.engines`,
   `helios.catalogs`, `helios.stores`, `helios.tools`, `helios.commands`, later `helios.sources` /
   `helios.retrievers`). Structured data (relational, lakehouse) ships now.
6. **Unstructured is deferred.** helios-crawl becomes the first unstructured plugin package and
   is built after the general-use release. Until then crawl code stays in `apps/helios/crawler`,
   and the evidence tools are not part of helios-serve.
7. **General use first; Workbench is a deployment profile.** The base install runs on a laptop
   with DuckDB and any SQLAlchemy database, and installs only light dependencies. Cloudera
   integrations (Impala, Atlas, the Workbench settings profile, Cloudera AI Inference defaults)
   live in an optional `helios-cloudera` plugin (`pip install "helios[cloudera]"`). CI guarantees
   Workbench keeps working.
8. **Local MCP adapter.** `helios-mcp` serves the tool registry over stdio for one developer's
   local agent (Claude Code, Cursor, goose, Mistral Vibe). No HTTP, no authentication, no
   deployment; Daedalus remains the path for anything shared.
9. **The monolith is not redeployed.** Helios reaches Workbench through a new host application
   that pip-installs it. `apps/helios` (console, UI, MCP server, graph gateway, Workbench
   launchers), `jobs/` and `runtime/` are scaffolding: their tests keep passing while the logic
   moves, nobody keeps them deployable, and Phase 8 deletes them.

| Phase | Tasks | Est. (days) | Gate |
|---|---|---|---|
| 0 Hygiene + build | 11 | 4.25 | Clean-clone `uv run pytest` green; token purged |
| 1 Core kernel | 11 | 6.1 | Standalone install; no env read / file I/O on import; CONTRACTS.md |
| 2 Identity out of the libraries | 7 | 4.5 | No users/grants in core; engines run as the host's caller |
| 3 Plugins, engines, helios-cloudera | 7 | 7 | Base install has no Cloudera dependency; Impala works via the plugin |
| 4 Pluggable storage | 7 | 5.75 | Same tests pass on SQLite + local files and on Postgres + object storage |
| 5 helios-model | 7 | 4.35 | Harvest→publish round-trip test, no console import |
| 6 helios-serve + public API | 7 | 5.25 | Tool registry lists 7 tools; `Helios` facade documented |
| 7 helios-mcp (local stdio) | 5 | 2 | A local agent lists tools and runs a query on the sample model |
| D Daedalus pack (other repo) | 5 | 3.5 | A Daedalus MCP call runs `run_query` as the real user |
| S Talk-to-data skill | 6 | 1.7 | Skill validates and runs against helios-mcp and Daedalus |
| 8 Retire the monolith | 5 | 2.75 | `apps/helios` holds only the crawler; all tests live in packages |
| 9 General-use release | 9 | 5.75 | Quickstart runs verbatim in CI; on PyPI; Workbench CI job green |
| 10 helios-crawl (deferred) | 9 | 7.5 | Evidence tools appear only when helios-crawl is installed |
| **Total** | **96** | **~60.5** | |

---

## Phase 0 — Hygiene and a real build

- [x] **0.1** Purge `.cursor/mcp.json` from history with `git filter-repo`; coordinate the force-push with anyone holding a clone. — 0.25d *(done 2026-10-29; `main` force-pushed)*
- [x] **0.2** Add `.cursor/`, `runs/`, `run_archive/`, `.DS_Store`, `*.pptx` to `.gitignore`; untrack/delete `runs/`, `run_archive/`, `apps/helios/ui/dist/`, root `impala_check.py`, root `compiler/` (incl. `__init_.py`), `apps/helios/console/templates/review.htmnl`, committed `.DS_Store`, `docs/Helios-Overview.pptx`. — 0.25d *(`review.htmnl` was under `console/templates/`; run outputs, deck and `ui/dist` untracked, not deleted)*
- [x] **0.3** Add `LICENSE` (Apache-2.0, pending open question Q3). — 0.1d
- [x] **0.4** Root `pyproject.toml` with `[tool.uv.workspace]`; remove the Poetry-syntax `helios-core @ {path=...}`; generate `uv.lock`. — 0.25d *(root project also installs `apps/` until Phase 8; `apps/helios/pyproject.toml` removed; helios-ds apps stay outside the workspace)*
- [x] **0.5** Move `shared/helios_core` → `packages/helios-core/src/helios_core`; fix its `pyproject.toml` (`packages = ["src/helios_core"]`, version 0.1.0); `__version__` reads `importlib.metadata`. — 0.5d *(dropped unused `pyarrow`/`sqlalchemy`; `lancedb`/`sentence-transformers` moved to an `evidence` extra)*
- [x] **0.6** Rename `apps/helios/mcp` → `apps/helios/mcp_server` (stops shadowing the MCP SDK); update imports and launchers. — 0.25d
- [x] **0.7** Remove `sys.path.insert` from `jobs/` and `scripts/`; use `uv sync` + editable installs. — 0.5d *(Workbench launchers and jobs keep a path shim pointing at `packages/helios-core/src` until Phase 8; `scripts/` inserts never reached core and are gone)*
- [x] **0.8** CI workflow: `uv sync`, ruff, pytest for the whole tree. — 0.4d *(`.github/workflows/helios.yml`)*
- [x] **0.9** CI job that publishes wheels to the internal index on tag. — 0.25d *(needs `HELIOS_INDEX_URL` / `HELIOS_INDEX_TOKEN` secrets)*
- [x] **0.10** Fix the 11 pre-existing failures listed in `KNOWN_FAILURES` (`apps/helios/tests/conftest.py`): ontology publish tests send no auth, two stale assertions in `test_talk_to_data`, and an MCP model lookup that needs `HELIOS_ROOT` (lands with 1.4). — 1d *(all fixed in the tests, no product changes; the MCP lookup test now points at the repo's models itself, so it no longer waits for 1.4; `KNOWN_FAILURES` removed)*
- [x] **0.11** Widen ruff to the classic default set (E4/E7/E9/F) and clear the ~67 findings, mostly E741 and unused imports. — 0.5d *(re-exports in `atlas`, `catalog`, `profiler`, `propose` declared in `__all__` instead of deleted; `graph.py` loop variable no longer shadows `dataclasses.field`; an assertion-free ontology parser test now asserts what its docstring claims)*

**Gate:** `uv run pytest` passes from a clean clone with no path hacks. *(Met: 582 passed, 2 skipped (LanceDB and Memgraph not installed), no expected failures; ruff clean.)*

## Phase 1 — helios-core becomes a kernel

- [x] **1.1** Create `helios_core/_pending/`; move out the feature code core reaches into: `publication.py`, `review.py`, `graph.py` (→ model, Phase 5), `health.py` (→ serve, Phase 6), and the unstructured pieces `crawler/`, `index/crawler_settings.py`, `index/evidence.py` (→ crawl, Phase 10). — 0.5d *(also moved `index/browse.py`, which only the crawler uses; imports inside moved modules are now absolute; `index` no longer re-exports `crawler_settings`; result: no core module outside `_pending` imports it)*
- [x] **1.2** import-linter config: core imports no sibling package; nothing imports `apps.*`; no new edges into `_pending`. — 0.25d *(in the root `pyproject.toml`, run by `uv run lint-imports` in CI; stricter than planned: no core module outside `_pending` may import it at all; both contracts verified to fail on planted imports)*
- [x] **1.3** `helios_core/settings.py` (pydantic-settings), resolved in a fixed order: arguments in code → `HELIOS_*` environment variables → config file (`helios.toml` or `[tool.helios]` in `pyproject.toml`) → defaults. Local defaults use `platformdirs` user directories; no `CDSW_*` reads and no `/home/cdsw` (the Workbench mapping is 3.4). — 0.75d *(done with one deviation: a `ConventionalEnvironment` source sits between `HELIOS_*` and the config file and reads the names Helios has always used, including the Workbench data root, so wiring Settings in at 1.4 changes nothing on Workbench; its Cloudera part moves out in 3.4. `HELIOS_CONFIG` or `Settings.from_file()` select a config file explicitly. Warehouses and catalogs are generic named entries, not Impala/Atlas sections: `[connections.<name>]` with `engine = "…"` (keyed by `DataSource.connection_ref`) and `[catalogs.<name>]` with `catalog = "…"`; core validates only the plugin name, each plugin validates its own options (`ImpalaOptions` / `impala_config_for`, `AtlasOptions` / `atlas_config_for`); `IMPALA_*` and `ATLAS_*` become the `default` connection and catalog. Parity tests compare Settings with the code it replaces in five environments)*
- [x] **1.4** Inject `Settings` (or its slice) into constructors, replacing ambient env reads in `runs.py` (incl. import-time read), `artifacts.py`, `metadata/sqlite.py`, `llm/client.py`, `audit.py`. Same behaviour, no store redesign (that is Phase 4). — 1.5d *(`runs.py` is now `RunStore(runs_dir)`; `ArtifactStore`, `SQLiteMetadataRepository` (path + journal mode) and `RunStore` take explicit values or `.from_settings()`; `llm_from_env()` → `llm_from_settings(settings.llm)`; `audit.purge_expired()` takes the retention; audit's secret redaction reads the environment through `settings.environment_secret_values()`. Apps and CLIs build `Settings()` where they used to read the environment, so behaviour is unchanged; the console and MCP server share `apps/helios/runtime.py`. Parity test now checks the new constructors against values recorded from the old code)*
- [ ] **1.5** Replace `config.atlas_config()` / `impala_config()` at their ~12 callers with `impala_config_for(settings.connections[…])` / `atlas_config_for(settings.catalogs[…])`; resolve each `DataSource.connection_ref` to a named connection, falling back to `default` for existing records; delete `config.py`; resolve `helios-ds-generation/impala.py` usage (Q5). — 0.75d
- [ ] **1.6** Replace `Path(__file__).parents[n]` in `crawler/__main__.py`, `console/assistant.py`, `console/ontology.py`, `console/evaluation.py` with `Settings` paths; regression test for the `BUILTIN_SUITE` path. — 0.25d *(off-by-one already fixed in Phase 0)*
- [ ] **1.7** CI check that `os.environ` / `os.getenv` appear only in `helios_core/settings.py` within packages. — 0.1d *(after 1.5 the only other readers are `_pending/evidence.py` (moves with helios-crawl), so the check excludes `_pending/`)*
- [ ] **1.8** Write `CONTRACTS.md` (SemanticModel, OntologyGraph, SourceMapping, `*Record`, IndexStore, ArtifactStore, Engine, registry protocol, Caller, Settings, LLMClient) as the semantic-versioning boundary, with a deprecation policy; ship `py.typed`; underscore or move everything else under `_internal/`. — 0.5d
- [ ] **1.9** Move the core and index/ontology tests to `packages/helios-core/tests`; DuckDB-backed index fixtures. — 0.75d
- [ ] **1.10** Test that `import helios_core` performs no env read and no file I/O. — 0.25d
- [ ] **1.11** Library code logs through named `logging` loggers instead of `print` (jobs, harvest, publish paths); no handlers configured by the library. — 0.5d

**Gate:** helios-core installs standalone; CONTRACTS.md merged; import-linter green.

## Phase 2 — Identity out of the libraries

- [ ] **2.1** Define `helios_core.Caller`: opaque, host-supplied, `username: str | None`, `token: str | None` (never logged), free-form `attributes`. Replaces `Principal` in every library signature. — 0.5d
- [ ] **2.2** Engine identity modes with generic names: `Engine.identity_modes` and `query(sql, caller)` supporting `service`, `impersonate` and `token`. Impala implements `impersonate` as `doAs` (keeping the `EFFECTIVE_USER()` check) and `token` as impyla JWT auth; DuckDB supports `service` only. The host picks the mode in Settings; `service` is the local default. — 1.5d
- [ ] **2.3** Keep `DataPolicy`'s shape: platform authorizer (allows only when the engine's mode enforces the caller's identity) plus deny-only restrictions the host supplies. Drop Helios grants from it. — 0.5d
- [ ] **2.4** `ModelVisibility` hook: the host decides which models a caller may list and use; default is "all models in the registry". — 0.5d
- [ ] **2.5** `AuditSink` hook: libraries emit structured events (tool, model, outcome, row count, SQL fingerprint); default sink writes to `logging`; the old console keeps its SQLite sink until Phase 8. — 0.5d
- [ ] **2.6** Move `authz/`, `rbac.py`, `identity.py`, `delegation.py`, `metadata/users.py` and the organization / principal / membership / grant tables from core into `apps/helios/console`, only so its tests keep passing until Phase 8 deletes it; no `cloudera-workbench:` identifiers remain in library code. — 0.75d
- [ ] **2.7** Tests: engine mode tests (impersonate effective-user check, token parameters, service), visibility and restriction hooks; move `persistent_auth_stack` tests with the console. — 0.25d

**Gate:** no users, organizations, roles or grants in `helios_core`; `run_query` runs as the host's caller in `impersonate` and `token` modes.

## Phase 3 — Plugins, engines and helios-cloudera

- [ ] **3.1** `helios_core.plugins`: load entry points by group, report what is installed, fail clearly when a configured plugin is missing. Groups: `helios.engines`, `helios.catalogs`, `helios.stores`, `helios.tools`, `helios.commands`. — 0.75d
- [ ] **3.2** Dependency layout: the base install is the compiler, semantic model, DuckDB and SQLite; everything else is an extra (`[cloudera]`, `[postgres]`, `[s3]`, `[azure]`, `[gcs]`, `[llm]`, later `[unstructured]`). CI test that `import helios_core` and the quickstart path work with no optional package installed. — 0.75d
- [ ] **3.3** Scaffold `packages/helios-cloudera`; move the Impala engine (with `ImpalaOptions`) and `impala_diagnose.py`, the Atlas catalog (with `AtlasOptions`), and Cloudera AI Inference defaults into it, registered as entry points under the names used in `engine = …` / `catalog = …`. impyla, thrift and SASL leave core's dependencies. — 1.5d
- [ ] **3.4** Workbench settings profile in helios-cloudera: move the Cloudera part of `helios_core.settings.ConventionalEnvironment` (`CDSW_*` data root, `WORKLOAD_USER` / `WORKLOAD_PASSWORD`, `IMPALA_*`, `ATLAS_*`, `INFERENCE_*`) into it, so existing deployments keep their variables; core keeps only `HELIOS_*` and the ecosystem names (`ANTHROPIC_API_KEY`, `MISTRAL_API_KEY`). — 0.5d
- [ ] **3.5** DuckDB engine as a core plugin (`engine = "duckdb"`, `path`); new `SqlAlchemyEngine` plugin (`engine = "sqlalchemy"`, `url`) for any SQLAlchemy dialect (Postgres, Snowflake, BigQuery, Trino, Databricks, MySQL), with catalog introspection through the SQLAlchemy inspector. — 1.5d
- [ ] **3.6** Compiler dialect hooks: replace `_impala_fixups` (`compiler/compiler.py:404`) with per-dialect hooks owned by engine plugins; remove the `"hive"` default so the engine supplies its dialect; golden tests compiling the sample requests for duckdb, postgres, snowflake, bigquery, trino and impala. — 1d
- [ ] **3.7** Default catalog plugin: harvest tables and columns through `information_schema` / the SQLAlchemy inspector; glossary and lineage become optional catalog capabilities (Atlas provides both). — 1d

**Gate:** the base install has no Cloudera dependency; the existing Impala and Atlas behaviour works through helios-cloudera.

## Phase 4 — Pluggable storage

- [ ] **4.1** Split `MetadataRepository` into what libraries need: a model registry (models, data sources, semantic revisions) in core; the old console's conversation / trace / evaluation tables move into `apps/helios` and are deleted with it. — 1d
- [ ] **4.2** SQLAlchemy registry backend selected by URL (`sqlite:///…`, `postgresql+psycopg://…`, any other dialect SQLAlchemy supports); Alembic migrations; the SQLite file in the user data directory stays the local default. — 1.5d
- [ ] **4.3** `ArtifactStore` backends selected by URL: local directory (`file://`) and object storage via fsspec (`s3://`, `abfss://`, `gs://`, `ofs://`); immutable published versions plus an "active version" pointer written with conditional writes (Q7). — 1.5d
- [ ] **4.4** Move `runs/` (harvest, profile, propose outputs) onto the same `ArtifactStore`. — 0.25d
- [ ] **4.5** Backend conformance suite: one set of tests every registry and artifact backend must pass. — 0.5d
- [ ] **4.6** CI: run the conformance suite against SQLite + local files on every PR, and Postgres + MinIO (S3) as service containers. — 0.5d
- [ ] **4.7** `docs/storage.md`: choosing backends, high-availability expectations (provider-managed database, object storage), connection URLs and secrets. — 0.5d

**Gate:** the same tests pass on SQLite + local files and on Postgres + object storage.

## Phase 5 — helios-model

- [ ] **5.1** Scaffold `packages/helios-model` (pyproject, `src/helios_model`, tests); import-linter contract (core + self only). — 0.25d
- [ ] **5.2** Write the harvest→profile→propose→publish round-trip test on the helios-ds tiny fixture (DuckDB, SQLite registry, local artifacts), asserting the published Ossie YAML and registry rows. Write it first: no facade without it. — 1d
- [ ] **5.3** Move `catalog`, `profiler`, `propose`, and `_pending/{publication,review,graph}.py` in (Atlas went to helios-cloudera in 3.3). — 0.5d
- [ ] **5.4** Facades `Catalog.harvest(source, caller)`, `Profiler.profile`, `Proposer.propose -> Proposals`, `Publisher.publish(proposals, caller) -> OntologyVersion`; `Proposals` typed with confidence + rationale. — 1d
- [ ] **5.5** One `helios` command whose subcommands come from the `helios.commands` entry point; helios-model registers `harvest`, `profile`, `propose`, `publish`; `helios init` writes a starter config file. Dissolve `jobs/_common.py` into Settings; Workbench Jobs call the same command. — 1d
- [ ] **5.6** Remove the emptied `_pending` model modules; confirm the round-trip test passes with no console import. — 0.35d
- [ ] **5.7** No demo assumptions in library defaults: remove `tpcds` defaults such as `HELIOS_DATABASES`; databases to harvest are always explicit. — 0.25d

## Phase 6 — helios-serve and the public API

- [ ] **6.1** Scaffold `packages/helios-serve`; import-linter contract; no `mcp` dependency. — 0.25d
- [ ] **6.2** Move the seven structured tools out of `mcp_server/server.py` into `helios_serve.tools` as plain functions `(caller, stores, **arguments) -> dict`: `list_models`, `describe_model`, `search_semantics`, `describe`, `compile_query`, `run_query`, `explain_lineage`. Keep `ModelStore`, the error codes and messages, and the impersonation diagnosis. — 1.5d
- [ ] **6.3** Tool registry over the `helios.tools` entry point: name, description, input schema from type hints, `effect` (`run_query` is the only one that executes SQL). `explain_lineage` registers only when a lineage-capable catalog is installed. Daedalus, helios-mcp and tests read this one source. — 0.5d
- [ ] **6.4** `health(stores) -> dict` (registry ping, model count) from `_pending/health.py` and the old `/healthz`. — 0.25d
- [ ] **6.5** `console/evaluation.py` → `helios_serve.evaluation` (library + `helios eval` subcommand) calling the tool functions directly instead of through an MCP client. — 1d
- [ ] **6.6** Umbrella package (name per Q8) that a developer installs: the `Helios` facade (`Helios.from_settings(...)` with `.models()`, `.compile(...)`, `.query(...)`, `.publish(...)`), the `helios` command entry point, and the extras from 3.2. — 1d
- [ ] **6.7** Tests: the tool tests from `test_talk_to_data`, `test_data_authorization`, `test_compiler` against the functions; the facade on the DuckDB sample; `test_talk_to_data`'s end-to-end parts → root `tests/integration`. — 0.75d

**Gate:** the registry lists 7 tools; helios-serve imports neither `mcp` nor the console; the `Helios` facade is documented.

## Phase 7 — helios-mcp, the local stdio adapter

- [ ] **7.1** Scaffold `packages/helios-mcp` (depends on helios-serve and the MCP SDK); registers a `helios mcp` subcommand. — 0.25d
- [ ] **7.2** Serve every registry tool over stdio: names, descriptions and input schemas from the registry, `readOnlyHint` from `effect`; the caller comes from Settings (`service` mode, or a token for `token` mode). — 0.75d
- [ ] **7.3** Reads the same `helios.toml` as the facade, so the quickstart's DuckDB sample works with no extra configuration. — 0.25d
- [ ] **7.4** Tests: an in-process MCP client lists the tools and runs `compile_query` and `run_query` on the sample model. — 0.5d
- [ ] **7.5** Docs: configuration snippets for Claude Code, Cursor, goose and Mistral Vibe; states plainly that it is local, single-user and unauthenticated, and that shared deployments use Daedalus. — 0.25d

**Gate:** a local agent lists the tools and runs a query against the sample model.

## Track D — Daedalus built-in pack (other repo)

- [ ] **D.1** `services/factory/packs/helios.py`: one `pack_tool` per registry entry, each mapping `ctx.caller` to `helios_core.Caller` and calling the helios-serve function; `effect` from the registry. — 1d
- [ ] **D.2** Pack configuration: Helios Settings (engine, identity mode, registry URL, artifact URL) from the Server Application's environment and secrets, through the helios-cloudera Workbench profile; stores built once per process. — 0.5d
- [ ] **D.3** Add `helios-serve` and `helios-cloudera` to `runtime/requirements.in` / `requirements.lock`; `smoke_check.py` imports the pack. — 0.5d
- [ ] **D.4** Document the two server shapes: full (all 7 tools) and read-only metadata (`search_semantics`, `describe`, `compile_query`; no SQL execution). — 0.25d
- [ ] **D.5** End to end: user → harness → agentgateway → Daedalus executor → helios-serve → Impala, in `impersonate` and `token` modes; Ranger sees the real user. — 1.25d

## Track S — talk-to-data skill (independent of the refactor)

- [ ] **S.1** Create `skills/talk-to-data/SKILL.md` from `apps/helios/console/conversation.py:320-345`: drop `{model_id!r}` and "never request another model"; name the MCP server `helios`; `compatibility` says it needs Helios tools from an MCP server named `helios` (helios-mcp locally, or Daedalus). — 0.25d
- [ ] **S.2** Move the TPC-DS key example and identifier-template rules to `references/entity-keys.md`; leave the evidence tools out until Phase 10. — 0.1d
- [ ] **S.3** Add `skills-ref validate ./skills/talk-to-data` to CI. — 0.1d
- [ ] **S.4** Run in Mistral Vibe, first against the old MCP server, then against helios-mcp (Phase 7) and the Daedalus server (D.1); write the setup steps down. — 0.5d
- [ ] **S.5** Smoke-test the same directory in Claude Code and goose. — 0.25d
- [ ] **S.6** Generate `references/semantic-models.md` from published Ossie models. — 0.5d

## Phase 8 — Retire the monolith

- [ ] **8.1** Sort `apps/helios/tests`: behaviour of library code that is still tested only there moves to the package tests or root `tests/integration`; tests of the console routes, UI static server, Workbench launchers, MCP transport and legacy agent are deleted with the app. Crawler tests stay for Phase 10. — 1d
- [ ] **8.2** Delete `apps/helios` except `crawler/` (Phase 10 moves it): console, MCP server, `runtime.py`, launchers, and the UI and graph gateway unless Q2 keeps them as examples. Delete `jobs/` (replaced by the `helios` command) and `runtime/` (host apps own their images); keep `scripts/` only where it still applies to the libraries. — 0.5d
- [ ] **8.3** Delete core code that only the old apps used: `tracing.py` / `TraceRecorder`, and anything left over from 2.6 and 4.1. — 0.5d
- [ ] **8.4** The root project stops installing `apps/` (a plain workspace root); `README.md` and `MONOREPO.md` describe the libraries and how a host installs them. — 0.25d
- [ ] **8.5** CI as a per-package matrix: ruff + mypy + pytest per package, import-linter, integration suite on PR, nightly against Impala; `CODEOWNERS`; tags `helios-<pkg>/vX.Y.Z`. — 0.5d

**Gate:** `apps/helios` holds only the crawler until Phase 10; every remaining test lives in a package or `tests/integration`.

## Phase 9 — General-use release

- [ ] **9.1** Sample model and data: the trimmed TPC-DS star (as in `tests/fixtures/tpcds-harvest.json`) plus its published model, loadable into DuckDB with `helios init --sample`. — 0.75d
- [ ] **9.2** README quickstart: install to first governed query on a laptop in under five minutes; a CI job runs the quickstart commands verbatim. — 0.5d
- [ ] **9.3** Move demo content out of the libraries: retail crawl presets, TPC-DS evaluation suites and sample models go to `examples/` or the sample data. — 0.5d
- [ ] **9.4** Settle and register the PyPI distribution names (Q8); the umbrella package carries the extras. — 0.25d
- [ ] **9.5** Publish to PyPI with trusted publishing, alongside the internal index for air-gapped customers; per-package version tags. — 0.5d
- [ ] **9.6** Docs: deployment guides for a laptop, a generic container host, Cloudera AI Workbench (helios-cloudera profile) and Daedalus; API reference generated from docstrings. — 1.5d
- [ ] **9.7** Project files: per-package `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, issue templates. — 0.5d
- [ ] **9.8** CI matrix for the base install: Python 3.11–3.13 on Linux, macOS and Windows. — 0.5d
- [ ] **9.9** Workbench guarantee: a CI job installs `helios[cloudera]` into the Cloudera runtime base image and runs a smoke test; nightly run against a real Impala warehouse. — 0.75d

**Gate:** the quickstart runs verbatim in CI; packages are on PyPI; the Workbench CI job is green.

## Phase 10 — helios-crawl, the first unstructured plugin (deferred)

- [ ] **10.1** Add `helios.sources` (connectors), `helios.retrievers` (evidence search) and `helios.embedders` groups; `[unstructured]` extra. — 0.5d
- [ ] **10.2** Before moving files: promote shared test helpers (`crawler_samples.py`, `shapes.py`, `second_domain.py`, baselines) to `helios_crawl.testing`; break `test_graph_index` → `test_crawler_evaluate`. — 1d
- [ ] **10.3** Scaffold `packages/helios-crawl`; move `apps/helios/crawler/*` and `_pending/{crawler, crawler_settings, evidence}`; `cml` connectors go to helios-cloudera. — 1d
- [ ] **10.4** LanceDB retriever and sentence-transformers embedder as plugins; the vector store location goes through Settings (local or object storage). — 1d
- [ ] **10.5** `search_evidence` and `entity_claims` registered as `helios.tools` from helios-crawl; Daedalus and helios-mcp expose them only when it is installed. — 0.75d
- [ ] **10.6** `Crawler.run(source, ontology_version, caller) -> CrawlRun`, `Resolver.resolve`, `Claims.extract`, `Evidence.embed`; `helios crawl --ontology-version` subcommand. — 1.5d
- [ ] **10.7** Connector protocol documented; `helios_ds`, `object_store`, `table_rows` ship as built-in sources. — 0.5d
- [ ] **10.8** Move the 177 crawler tests + `test_evidence`; green on DuckDB with no console or graph import. — 1d
- [ ] **10.9** Add the evidence tools back to the talk-to-data skill. — 0.25d

## Dropped from the original plan

- `mcp_app(settings)` ASGI factory, `server/discover`, the JWT login path in helios-serve and its parity test (old 4.3–4.6): Daedalus serves shared MCP and verifies callers; helios-mcp is local-only.
- Daedalus `forward_headers` / principal-assertion mode (old D.1): the pack receives the caller directly.
- helios-serve as a Workbench Application (old 4.9): libraries ship no applications.
- Rebuilding the console as a host of the libraries (old Phase 8), its model and crawl routers (old 5.6, 10.8), pointing it at Daedalus, loading its prompt from SKILL.md, and retiring its agent and conversation tables (old R.1–R.3): the monolith is deleted instead (decision 9).

## Open questions that block tasks

| # | Question | Blocks |
|---|---|---|
| Q1 | Does the target CDW Virtual Warehouse accept JWTs from the identity provider in front of Daedalus? | `token` mode in 2.2 and D.5 |
| Q2 | Does anyone need the React UI or the Memgraph graph gateway as an example host, or are they deleted with `apps/helios`? | 8.2 |
| Q3 | Apache-2.0 for all packages, or a different grant for helios-serve? | 0.3, 9.5 |
| Q5 | helios-ds-generation's `impala.py`: depend on helios-core or copy 40 lines? | 1.5 |
| Q6 | Is Daedalus's `jwt.sub` the same string as the Workbench / Ranger username? | `impersonate` mode in D.5 |
| Q7 | Which object stores must the "active version" pointer support (S3, ADLS, GCS and Ozone all have conditional writes, with different APIs)? | 4.3 |
| Q8 | PyPI distribution names: `helios` is very likely taken; what do the umbrella and plugin packages publish as? | 6.6, 9.4 |

## Notes from checking the plan against the repo

- `review.htmnl` was at `apps/helios/console/templates/`, not the repo root.
- `scripts/impala_check.py` exists alongside the old root `impala_check.py`; only the root one was deleted.
- `apps/helios-ds-generation/workbench/*.py` also use `sys.path.insert`; that app stays outside the workspace, so 0.7 left them.
