# helios-semantic

Helios builds semantic models of your data (datasets, fields, metrics, relationships)
and answers questions with them: it compiles metrics and dimensions to SQL and runs it on
your warehouse as the person asking. This is the package to install.

```sh
pip install helios-semantic                     # DuckDB and SQLite, on a laptop
pip install "helios-semantic[cloudera,llm]"     # Impala and Atlas; an LLM to propose models
```

## The `Helios` facade

```python
from helios_semantic import Helios

helios = Helios.from_settings()            # helios.toml, HELIOS_* variables, then defaults

helios.models()                            # [{"name": "shop", "status": "published"}]
helios.search("revenue by region")         # the datasets, fields and metrics involved
helios.compile(["revenue"], ["shop.customers.c_region"]).sql
result = helios.query(["revenue"], ["shop.customers.c_region"],
                      filters=[{"column": "shop.customers.c_region", "op": "=", "value": "east"}])
result.records()                           # [{"c_region": "east", "revenue": 3750.0}]

helios.publish("20261110T120000Z", accept_pending=True)   # a discovery run's proposals
helios.health()
```

A method raises `HeliosError` (with `code`, `message` and `retryable`) where a tool
refuses: an unknown metric is `invalid_semantic_query`, a model nobody published
`model_not_found`.

**Who runs the queries.** `Helios.from_settings()` with no caller is your own program,
running with the credentials you configured, so a connection in `service` identity mode
runs its queries. `Helios.from_settings(caller=Caller("alice"))`, or
`helios.as_caller(Caller("alice"))`, acts for alice: the warehouse runs the query as her
(`impersonate` or `token` mode) and enforces her permissions, and a service-identity
connection refuses. A server for many users gives each request its own `as_caller`, or
uses helios-serve's tools directly.

## The `helios` command

```sh
helios init                        # a starter helios.toml
helios harvest --source warehouse --database shop --model shop
helios profile && helios propose && helios publish --accept-pending
helios eval tpcds                  # with [llm]
```

## Extras

| Extra | Adds |
|---|---|
| `cloudera` | helios-cloudera: the Impala engine, the Atlas catalog, the Workbench settings profile |
| `llm` | an LLM client, to propose models and run `helios eval` |
| `postgres`, `trino` | a Postgres registry and engine; the Trino engine |
| `s3`, `azure`, `gcs`, `ozone` | published models on object storage |
| `ontology` | LinkML ontology parsing |
| `all` | all of the above |

## The libraries

helios-semantic installs helios-cortex (settings, compiler, engines, registry, the
`helios` command), helios-model (harvest, profile, propose, publish) and helios-serve
(the tools, their registry, health and evaluation). Use them directly for more control;
helios-mcp (a local MCP server for your agent) and helios-cloudera install separately.
