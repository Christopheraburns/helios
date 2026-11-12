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

The registry of tools with their input schemas comes in 6.3 and `health` in 6.4
(Phase 6 of `docs/helios-decomposition-burndown.md`).
