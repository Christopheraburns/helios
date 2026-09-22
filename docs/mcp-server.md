# helios MCP server

The second helios Application. It exposes published semantic models to agents over the Model Context Protocol
(Streamable HTTP). An agent discovers what a model can answer, expresses a question as a semantic request, and gets
SQL or rows back. It never sees the physical schema and never writes SQL; the compiler decides joins from the
published relationships.

## Deploy in Cloudera AI Workbench

Application → New:
- Script `helios/apps/mcp/app.py`, subdomain `helios-mcp`, the helios runtime
- Tick **Enable Unauthenticated Access** (MCP clients cannot complete the Workbench login), and set
  `HELIOS_MCP_TOKEN` in the project environment; the server then requires `Authorization: Bearer <token>` on `/mcp`
- `IMPALA_HOST` and credentials as for the jobs, so `run_query` can reach the warehouse

Check `https://helios-mcp.<workbench-domain>/healthz` — it lists the published models.

## Connect a client

Claude Desktop (`claude_desktop_config.json`) via the reference HTTP bridge:

    {"mcpServers": {"helios": {"command": "npx", "args": ["-y", "mcp-remote", "https://helios-mcp.<domain>/mcp",
                                "--header", "Authorization: Bearer ${HELIOS_MCP_TOKEN}"],
                               "env": {"HELIOS_MCP_TOKEN": "<token>"}}}}

Cursor and other clients that speak Streamable HTTP directly: URL `https://helios-mcp.<domain>/mcp`, header
`Authorization: Bearer <token>`.

## Tools

| tool | purpose |
|---|---|
| `list_models()` | published models |
| `describe_model(model_name, include_fields)` | datasets, metrics, dimensions, joins — the menu |
| `search_model(model_name, query)` | metrics / dimensions / measures matching words in the question |
| `explain_request(model_name, request)` | fact, joins, columns a request resolves to |
| `compile_query(model_name, request)` | SQL without running it |
| `run_query(model_name, request, limit)` | SQL + rows; capped by `HELIOS_MCP_MAX_ROWS` (500) |

A semantic request:

    {"metrics": ["Store Sales Revenue"], "dimensions": ["State", "d_year"],
     "filters": [["d_year", "=", 2001]], "order_by": ["-Store Sales Revenue"], "limit": 20}

Metrics and dimensions may be given by business name, unique column name, or `dataset.column`. Ambiguity is an
error naming the candidates, which the agent resolves by qualifying. `measures` allows an ad-hoc aggregate over a
column when no metric fits; `via` pins a relationship when a dataset has several (sold date vs ship date).

Models are re-read when the published file changes, so publishing from the console takes effect without a restart.