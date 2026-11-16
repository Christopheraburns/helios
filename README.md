# helios

**Semantic models of your warehouse, and governed answers from them.**

Helios is a set of Python libraries. It inspects a warehouse, proposes a semantic model of it, and answers questions through that model. The model covers datasets, fields, metrics and relationships, in the open [Apache Ossie](https://github.com/apache/ossie) format, and a person reviews it before it is published. To answer a question, Helios compiles the metrics and dimensions asked for into SQL for your engine. It then runs that SQL as the person asking, so the warehouse's own permissions apply.

Helios is not an application. A host installs the libraries and decides how they are served:

- a program or notebook through the `Helios` facade;
- the `helios` command;
- one developer's coding agent through `helios mcp`;
- a shared, governed deployment through Daedalus, which serves the same tools to many users.

## Quickstart

On a laptop with Python 3.11 or newer, in a fresh virtual environment (`python -m venv .venv && source .venv/bin/activate`), from install to a first answer takes a few minutes. Until the packages are on PyPI, install them from a checkout instead of the `pip` line (see [Installing](#installing)).

<!-- quickstart: CI runs this block verbatim -->
```sh
pip install helios-semantic
helios init --sample
```

`helios init --sample` writes a `helios.toml` and builds a small TPC-DS warehouse beside it: `store_sales` and the six tables it joins to, in DuckDB. It also publishes the warehouse's semantic model: datasets, fields, the relationships between them, and metrics such as revenue and profit margin. Now ask it something, in Python:

<!-- quickstart: CI runs this block verbatim -->
```python
from helios_semantic import Helios, HeliosError

helios = Helios.from_settings()                      # reads ./helios.toml

# What can be asked: the model's metrics, fields and datasets, found by meaning.
for match in helios.search("store sales profit", limit=3):
    print(match["kind"], match["name"])

# Revenue and margin by state in 2002, from the published model: no SQL written.
result = helios.query(["store_sales_revenue", "store_sales_profit_margin"], ["tpcds.store.s_state"],
                      filters=[{"column": "tpcds.date_dim.d_year", "op": "=", "value": 2002}])
for row in result.records():
    print(row)
print(result.sql)                                    # the SQL Helios compiled from the model

# Only what the model defines can be asked for.
try:
    helios.query(["store_sales_profitt"])
except HeliosError as error:
    print(error.code, "-", error.message)
```

That's a governed query. The metrics, joins and filter types come from a model a person reviewed; Helios compiled the SQL, and the warehouse ran it. A name the model doesn't define is refused, with a hint to search. Next: build a model of your own warehouse ([Using it](#using-it)), give the sample to your coding agent (`pip install helios-mcp`, then `helios mcp --print-config claude-code`), or look at it in the [viewer](examples/viewer/README.md).

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

The extras are `cloudera`, `llm`, `mcp`, `postgres`, `trino`, `s3`, `azure`, `gcs`, `ozone`, `ontology` and `all`; see [helios-semantic's README](packages/helios-semantic/README.md).

## Using it

Configuration is one `helios.toml`, overridden by `HELIOS_*` environment variables. `helios init` writes a starter file. Connections, catalogs, the registry, artifact storage and the LLM are all named there:

```toml
data_dir = "helios-data"

[connections.default]
engine = "duckdb"
path = "warehouse.duckdb"
```

`helios init --sample` (the [Quickstart](#quickstart)) builds a sample instead: the TPC-DS store-sales star in `warehouse.duckdb`, 60,000 sales lines over 1998–2002, the same every time, with its model published as `tpcds`. On your own warehouse, build a model, then publish it:

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
helios.search("store sales revenue")
helios.query(["store_sales_revenue"], ["tpcds.date_dim.d_year"]).records()   # on the sample
```

Or let a coding agent ask them: `helios mcp --print-config claude-code` prints the configuration to add to the agent ([helios-mcp's README](packages/helios-mcp/README.md)). To look at a published model, a query's path over it, or the ontology, run the [viewer](examples/viewer/README.md).

**Who runs the queries.** A program with no caller runs queries with the credentials it was configured with. That suits a laptop, a notebook or `helios mcp`. A host serving several people passes each request's `Caller`, and the warehouse runs the query as that person and enforces their permissions. Daedalus does this for shared deployments, and helios-serve's README shows it.

**On Cloudera AI Workbench**, install `helios-semantic[cloudera]` into the project's runtime and run `helios --profile workbench`. The profile reads the Workbench and Cloudera variables (`IMPALA_HOST`, `WORKLOAD_USER`, `ATLAS_*`, `INFERENCE_*`) into the settings; see [helios-cloudera's README](packages/helios-cloudera/README.md).

## The ontology

Under [`ontology/`](ontology/), a LinkML ontology describes the domain: its concepts, how they relate, and its synonyms and hierarchies. It comes in layers: a core, industry packs such as retail, and customer extensions. Mappings tie the ontology's classes to datasets and fields in an Ossie model. `helios_cortex.ontology` parses the layers into a versioned graph (the `ontology` extra).

## Sample environment

Helios is developed against TPC-DS:
- **Locally:** the store-sales star that `helios init --sample` builds in DuckDB, which the integration tests use (see [tests/integration](tests/integration/README.md)).
- **On Cloudera:** the full 24-table warehouse in Impala, with a reference business glossary in Atlas. [`examples/cloudera-tpcds`](examples/cloudera-tpcds/README.md) has the scripts that build both and how to run them.

The repository's published TPC-DS model is [`models/published/tpcds.ossie.yaml`](models/published/tpcds.ossie.yaml).

## Evaluation

`helios eval` runs a suite of questions through the tools with an LLM. It scores each answer against reference SQL, and reports accuracy, tool-call efficiency, recovery from errors and grounding. Comparing two variants shows whether a model change helped. Suites are data, not part of a package: `helios init --sample` writes `sample-suite.json` (ten questions the sample can answer, so `helios eval sample-suite.json` runs on it), and [`examples/eval/tpcds-v1.json`](examples/eval/tpcds-v1.json) has 20 questions for the full TPC-DS warehouse ([`examples/cloudera-tpcds`](examples/cloudera-tpcds/README.md)). See [helios-serve's README](packages/helios-serve/README.md).

## Repository layout

```
helios/
├── packages/           the libraries (each its own pyproject.toml, src/ and tests/)
├── tests/integration/  tests that cross packages (the facade on the DuckDB sample, identity end to end)
├── examples/           the viewer, evaluation suites, crawler presets, and TPC-DS on Cloudera
├── apps/
│   ├── helios/crawler/        the document crawler, until it becomes helios-crawl (Phase 10)
│   ├── helios-ds-generation/  the Helios-DS synthetic data generator (outside the workspace)
│   └── helios-ds-evaluation/  its evaluation harness (outside the workspace)
├── models/published/   the published TPC-DS model
├── ontology/           the LinkML ontology: core, packs, customer extensions, mappings
├── scripts/            development checks (compile a request, Impala connectivity, LinkML)
└── docs/               design notes and burndowns
```

Developing Helios itself (the workspace, tests, CI and releases) is described in [CONTRIBUTING.md](CONTRIBUTING.md). The decomposition into these libraries is tracked in [docs/helios-decomposition-burndown.md](docs/helios-decomposition-burndown.md).

## References

- Apache Ossie: https://github.com/apache/ossie
- LinkML: https://linkml.io
- Cloudera Impala TPC-DS kit: https://github.com/cloudera/impala-tpcds-kit
- TPC-DS specification: http://www.tpc.org/tpcds/
