# helios-serve

The Helios tools, as plain Python functions: list the published semantic models,
describe one, search their semantics, compile a question into SQL, run it as the
caller, and explain a column's lineage.

helios-serve depends only on helios-cortex. It has no MCP dependency and no HTTP
server: Daedalus serves the tools to shared, governed deployments, and helios-mcp
serves them over stdio to one developer's local agent.

```python
from helios_cortex import Caller
from helios_cortex.settings import Settings
from helios_serve import Stores, tools

stores = Stores.from_settings(Settings())      # the registry and the published models
alice = Caller("alice")                        # whoever the host says is asking

tools.search_semantics(alice, stores, question="revenue by region")
tools.compile_query(alice, stores, metrics=["revenue"], dimensions=["shop.customers.c_region"])
tools.run_query(alice, stores, metrics=["revenue"], dimensions=["shop.customers.c_region"])
```

Every tool is `tool(caller, stores, **arguments) -> dict`, with keyword arguments:

| Tool | What it returns |
|---|---|
| `list_models` | `{"models": [{"name", "status"}]}`: the published models the caller may see |
| `describe_model` | datasets, fields, metrics and relationships of a model (the first when `model` is omitted) |
| `search_semantics` | the datasets, fields and metrics a natural-language `question` involves |
| `describe` | one dataset, column (`database.table.column`) or metric by `name` |
| `compile_query` | SQL for `metrics`, `dimensions`, `filters`, in `dialect` (default: the model's own connection's) |
| `run_query` | the query's `columns` and `rows`, run on the model's connection as the caller |
| `explain_lineage` | a `column`'s lineage from a catalog that has it (Atlas in helios-cloudera) |

Only published models are served; an unreviewed proposal never is. A tool returns
`{"error": code, "message": ..., "retryable": ...}` for anything the caller can fix
rather than raising.

`Stores` holds what the tools read, and each part is the host's to replace:
`visibility` (which models a caller may see), `data_policy` (which queries may run;
by default only those the warehouse runs as the caller), `audit` (where a record of
each call goes, with administrator-only diagnostics for errors), `engines` and
`catalogs` (how a connection or a catalog is built).

## The tool registry

A server (Daedalus, helios-mcp) offers the tools from one source, the registry:

```python
from helios_serve import load_registry

registry = load_registry()
for tool in registry.tools():
    tool.name, tool.description, tool.input_schema, tool.effect   # what a server offers
registry.call("run_query", alice, stores, metrics=["revenue"])     # same as tools.run_query
```

`input_schema` is JSON Schema built from the function's type hints (`Annotated[T,
"text"]` describes a parameter; a `TypedDict` is an object). It keeps to what MCP and
OpenAPI 3.0 share: an optional argument is left out of `required`, never typed `null`.
`effect` is `read` (Helios metadata only) or `query` (runs SQL on the warehouse as the
caller: `run_query` alone); no tool changes data. `registry.tools(effects={"read"})` is
a read-only metadata server.

`explain_lineage` is offered only when an installed catalog plugin has lineage (Atlas,
in helios-cloudera), so a base install lists six tools and one with helios-cloudera
seven.

Another package adds a tool by marking a function and registering it under the tool's
name:

```python
from helios_serve import tool

@tool(effect="read", description="What an agent reads to decide when to call it.")
def row_count(caller, stores, *, table: str) -> dict: ...
```

```toml
[project.entry-points."helios.tools"]
row_count = "my_package.tools:row_count"
```

## Health

`health(stores)` says whether the deployment can answer, for a host to serve wherever it
reports health (an HTTP `/healthz`, a Daedalus probe, a CLI):

```python
from helios_serve import health

health(stores)             # the model registry and the published models
health(stores, deep=True)  # also pings each warehouse connection and the catalog
# {"status": "healthy", "version": "0.1.0", "model_count": 3,
#  "components": [{"id": "registry", "label": ..., "status": "healthy", "description": ...}, ...]}
```

A component is `healthy`, `degraded` (reachable but not ready: a registry schema that
needs `helios registry upgrade`, or nothing published yet), `unavailable`, or `unknown`
(nothing configured to check). The deployment is `unavailable` when the registry or the
models are, `degraded` when any checked component is not healthy, and `healthy`
otherwise. Descriptions are fixed sentences: an exception's text never reaches the
result or the log. A deep check pings with the connection's own (service) credentials.
