# helios

**Semantic models of your warehouse, and governed answers from them.**

Helios is a set of Python libraries. It inspects a warehouse, proposes a semantic model of it, and answers questions through that model. The model covers datasets, fields, metrics and relationships, in the open [Apache Ossie](https://github.com/apache/ossie) format, and a person reviews it before it is published. To answer a question, Helios compiles the metrics and dimensions asked for into SQL for your engine. It then runs that SQL as the person asking, so the warehouse's own permissions apply.

Helios is not an application. A host installs the libraries and decides how they are served:

- a program or notebook through the `Helios` facade;
- the `helios` command;
- one developer's coding agent through `helios mcp`;
- a shared, governed deployment through Daedalus, which serves the same tools to many users.

---

## Why

Text-to-SQL against a bare warehouse fails for predictable reasons. The model doesn't know that `ss_net_paid` is revenue. It doesn't know that `store_sales` joins to `date_dim` on the sold date rather than the ship date, or that "gross margin" excludes returns. Those facts exist in people's heads, in BI tools and in old query logs, but nowhere a machine can read them.

Helios extracts and structures them. Answering a question then becomes a lookup against a reviewed model rather than a guess at SQL.

## Design principles

| Principle | What it means in practice |
|---|---|
| **Propose, don't decide.** | Helios drafts every element with a confidence score and a rationale. Nothing is published until a person accepts it. |
| **Evidence first, LLM second.** | Deterministic analysis does most of the work: naming conventions, key-overlap statistics, verified foreign keys and query history. An LLM handles judgement: names, descriptions, ambiguous joins and metric candidates. |
| **Open formats.** | The semantic model is Ossie YAML/JSON, the ontology is LinkML, and a glossary can live in Atlas. No Helios-specific format is the source of truth. |
| **Engine-agnostic.** | The compiler builds one SQL AST and transpiles it with SQLGlot. Engines, catalogs and settings profiles are plugins. |
| **The caller's identity.** | A query runs as the person asking: impersonated, or with their token. Without that identity, the default data policy refuses the query. Helios grants no data access of its own. |
| **Libraries, not a platform.** | No web server, no user database, no deployment of its own. Identity, authorization and serving belong to the host. |

## The libraries

| Package | What it is |
|---|---|
| [`helios-semantic`](packages/helios-semantic) | **The one to install.** The `Helios` facade over the libraries below, and the extras. |
| [`helios-cortex`](packages/helios-cortex) | The kernel, with no Cloudera dependency: settings (`helios.toml`), the Ossie model, the compiler, engines (DuckDB, SQLAlchemy, Trino), the model registry (SQLite, Postgres), the artifact store (local or object storage), the ontology, the LLM client, the plugin system and the `helios` command. Its stability promises are in [CONTRACTS.md](packages/helios-cortex/CONTRACTS.md). |
| [`helios-model`](packages/helios-model) | Model discovery: harvest, profile, propose, review and publish (`helios harvest`, `profile`, `propose`, `publish`). |
| [`helios-serve`](packages/helios-serve) | The tools as plain functions, and their registry (`helios.tools`): `list_models`, `describe_model`, `search_semantics`, `describe`, `compile_query`, `run_query` and `explain_lineage`. Also `health`, and the evaluation behind `helios eval`. |
| [`helios-mcp`](packages/helios-mcp) | The tools over MCP on stdio, for one developer's local agent: `helios mcp`, plus `helios mcp --print-config claude-code\|cursor\|goose\|vibe`. It is local, single-user and unauthenticated. |
| [`helios-cloudera`](packages/helios-cloudera) | The Cloudera plugins: the Impala engine (with `impersonate` and `token` identity modes), the Atlas catalog and glossary, and the Cloudera AI Workbench settings profile. |

Each package depends on `helios-cortex` and nothing above it; [import-linter contracts](pyproject.toml) keep it that way. A package's plugins register through entry points: `helios.engines`, `helios.catalogs`, `helios.profiles`, `helios.commands` and `helios.tools`. Installing a package adds its engines, commands and tools; nothing else needs configuring.

## Installing

Once the packages are on PyPI (burndown Phase 9):

```sh
pip install helios-semantic                     # DuckDB and SQLite, on a laptop
pip install "helios-semantic[cloudera,llm]"     # Impala and Atlas, and an LLM to propose models
pip install helios-mcp                          # a local MCP server for your coding agent
```

Until then, install them from a checkout of this repository:

```sh
pip install ./packages/helios-cortex ./packages/helios-model ./packages/helios-serve ./packages/helios-semantic
pip install ./packages/helios-cloudera ./packages/helios-mcp     # as needed
```

The extras are `cloudera`, `llm`, `postgres`, `trino`, `s3`, `azure`, `gcs`, `ozone`, `ontology` and `all`; see [helios-semantic's README](packages/helios-semantic/README.md).

## Using it

Configuration is one `helios.toml`, overridden by `HELIOS_*` environment variables. `helios init` writes a starter file. Connections, catalogs, the registry, artifact storage and the LLM are all named there:

```toml
data_dir = "helios-data"

[connections.default]
engine = "duckdb"
path = "warehouse.duckdb"
```

Build a model, then publish it:

```sh
helios init
helios harvest --source warehouse --database shop --model shop
helios profile && helios propose          # propose needs [llm]
helios publish --accept-pending
```

Ask it questions from Python:

```python
from helios_semantic import Helios

helios = Helios.from_settings()
helios.search("revenue by region")
helios.query(["revenue"], ["shop.customers.c_region"]).records()
```

Or let a coding agent ask them: `helios mcp --print-config claude-code` prints the configuration to add to the agent ([helios-mcp's README](packages/helios-mcp/README.md)).

**Who runs the queries.** A program with no caller runs queries with the credentials it was configured with. That suits a laptop, a notebook or `helios mcp`. A host serving several people passes each request's `Caller`, and the warehouse runs the query as that person and enforces their permissions. Daedalus does this for shared deployments, and helios-serve's README shows it.

**On Cloudera AI Workbench**, install `helios-semantic[cloudera]` into the project's runtime and run `helios --profile workbench`. The profile reads the Workbench and Cloudera variables (`IMPALA_HOST`, `WORKLOAD_USER`, `ATLAS_*`, `INFERENCE_*`) into the settings; see [helios-cloudera's README](packages/helios-cloudera/README.md).

## The ontology

Under [`ontology/`](ontology/), a LinkML ontology describes the domain: its concepts, how they relate, and its synonyms and hierarchies. It comes in layers: a core, industry packs such as retail, and customer extensions. Mappings tie the ontology's classes to datasets and fields in an Ossie model. `helios_cortex.ontology` parses the layers into a versioned graph (the `ontology` extra).

## Sample environment

Helios is developed against TPC-DS:
- **Locally:** a small DuckDB star, which the integration tests use (see [tests/integration](tests/integration/README.md)).
- **On Cloudera:** a full TPC-DS warehouse in a Cloudera public cloud environment. The platform services are an SDX data lake (Hive Metastore, Atlas, Ranger, Knox), an Impala Virtual Warehouse, Cloudera AI Workbench, Cloudera AI Inference and S3 storage.

The repository's published TPC-DS model is [`models/published/tpcds.ossie.yaml`](models/published/tpcds.ossie.yaml).

**Data:** TPC-DS at scale factor 1 (about 1 GB, 24 tables). It is generated with DuckDB's `tpcds` extension, landed as Parquet in the data lake bucket, staged as external tables, and converted to Iceberg tables in the Impala database `tpcds`.

```
setup/start.py       generate TPC-DS Parquet with DuckDB (run in a Workbench session)
setup/gen_ddl.py     emit stage.sql and iceberg.sql from the generated table directories
setup/stage.sql      CREATE EXTERNAL TABLE ... LIKE PARQUET for each table (database tpcds_stage)
setup/iceberg.sql    CREATE TABLE ... STORED AS ICEBERG AS SELECT ... (database tpcds), COMPUTE STATS
```

A handful of TPC-DS benchmark queries (Impala dialect, from `cloudera/impala-tpcds-kit`) are run against the warehouse, so that Impala's query history holds realistic aggregation patterns for discovery to mine. The kit's literal filter values were generated for a 10 TB scale factor and may need adjusting at SF1.

**Business glossary:** an Atlas glossary named `TPCDS Retail`. It has 138 terms (entities, measures with dsdgen pricing formulas, dimension attributes and derived KPIs), linked to 393 columns. It is the reference: what Helios should approximate from a cold start.

```
Atlas_Glossary/build_glossary.py        generates the glossary CSVs from a term catalogue in code
Atlas_Glossary/assign_terms.py          imports a glossary CSV into Atlas and links terms to columns
Atlas_Glossary/tpcds_glossary_full.csv  all 138 terms, Atlas bulk-import format
Atlas_Glossary/tpcds_glossary_seed.csv  32 core terms: the "cold start" glossary for discovery experiments
Atlas_Glossary/tpcds_term_columns.csv   term → table.column assignments
```

```bash
export ATLAS_BASE="https://<datalake-host>/<datalake-name>/cdp-proxy-api/atlas/api/atlas/v2"
export ATLAS_USER=<workload-username>
export ATLAS_PASS='<workload-password>'
python3 assign_terms.py tpcds_glossary_full.csv                # import + assign
python3 assign_terms.py tpcds_glossary_full.csv --skip-import  # glossary already loaded; only assign
```

Two things to know about Atlas glossary import here:
- The REST import returns `409` if any term already exists; it doesn't update in place. To change definitions, delete the glossary and reload it.
- The `Synonyms` column is a term-to-term relationship rather than free text, so synonyms are folded into each term's long description.

The full glossary is what a well-governed warehouse would have, so loading it before a discovery run hands Helios the answer key. For evaluation, load the seed glossary under its own name and keep the full one hidden.

## Evaluation

`helios eval` runs a suite of questions through the tools with an LLM. It scores each answer against reference SQL, and reports accuracy, tool-call efficiency, recovery from errors and grounding. Comparing two variants shows whether a model change helped. The built-in suite is TPC-DS (`helios_serve/suites/tpcds-v1.json`, 20 questions); see [helios-serve's README](packages/helios-serve/README.md).

## Repository layout

```
helios/
├── packages/           the libraries (each its own pyproject.toml, src/ and tests/)
├── tests/integration/  tests that cross packages (the facade on the DuckDB sample, identity end to end)
├── apps/
│   ├── helios/crawler/        the document crawler, until it becomes helios-crawl (Phase 10)
│   ├── helios-ds-generation/  the Helios-DS synthetic data generator (outside the workspace)
│   └── helios-ds-evaluation/  its evaluation harness (outside the workspace)
├── models/published/   the published TPC-DS model
├── ontology/           the LinkML ontology: core, packs, customer extensions, mappings
├── setup/              scripts that build the TPC-DS sample environment
├── Atlas_Glossary/     the TPC-DS glossary generator and Atlas loader
├── scripts/            development checks (compile a request, Impala connectivity, LinkML)
└── docs/               design notes and burndowns
```

Developing Helios itself (the workspace, tests, CI and releases) is described in [CONTRIBUTING.md](CONTRIBUTING.md). The decomposition into these libraries is tracked in [docs/helios-decomposition-burndown.md](docs/helios-decomposition-burndown.md).

## References

- Apache Ossie: https://github.com/apache/ossie
- LinkML: https://linkml.io
- Cloudera Impala TPC-DS kit: https://github.com/cloudera/impala-tpcds-kit
- TPC-DS specification: http://www.tpc.org/tpcds/
