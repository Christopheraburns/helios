# helios-cortex

The kernel of [Helios](https://pypi.org/project/helios-semantic/): semantic models of your
warehouse in the open [Apache Ossie](https://github.com/apache/ossie) format, compiled to SQL
for your engine and run as the person asking. Most people install
[`helios-semantic`](https://pypi.org/project/helios-semantic/), which brings this package
with the rest; install helios-cortex alone to build on the kernel.

```sh
pip install helios-cortex
helios init --sample        # a TPC-DS star in DuckDB with its model published, to try it on
```

What it holds:

- **Settings:** one `helios.toml`, overridden by `HELIOS_*` environment variables
  (`helios_cortex.settings.Settings`).
- **The semantic model and compiler:** `helios_cortex.ossie.SemanticModel` and
  `helios_cortex.compiler.Compiler`. The compiler turns metrics, dimensions and filters
  into one SQL AST, transpiled with SQLGlot for the engine's dialect.
- **Engines:** DuckDB, SQLAlchemy and Trino built in; others (helios-cloudera's Impala)
  are plugins. Each runs a query as the caller (`impersonate` or `token`) or as its
  service identity.
- **Storage:** the model registry (SQLite by default, or Postgres) and the artifact store
  for published model revisions (a directory, or S3, Azure, GCS or Ozone).
- **The ontology:** LinkML layers parsed into a versioned graph, with mappings to model
  elements.
- **The `helios` command:** its subcommands come from the installed packages. This one
  adds `init`, `source` and `model` (`add`, `list`, and `export`, which writes a model's
  published Ossie document so a host can ship it), and `registry`.
- **The plugin system:** `helios.engines`, `helios.catalogs`, `helios.profiles`,
  `helios.commands` and `helios.tools` entry points.

| Extra | Adds |
|---|---|
| `llm` | the LLM client (Anthropic, Mistral, any OpenAI-compatible endpoint, Bedrock) |
| `postgres` | Postgres for the registry and the SQLAlchemy engine |
| `trino` | the Trino engine |
| `s3`, `azure`, `gcs`, `ozone` | published models on object storage |
| `ontology` | LinkML ontology parsing |

Importing it reads no environment variables and touches no files; building `Settings`
does. What is stable, provisional or internal is listed in
[CONTRACTS.md](https://github.com/christopheraburns/helios/blob/main/packages/helios-cortex/CONTRACTS.md). Apache-2.0.
