# Helios MCP server

The MCP Application exposes Helios's governed semantic and data tools to the
Helios conversation client and to registered external agents. It speaks MCP
Python SDK 2.x Streamable HTTP with stateless JSON responses.

See [Talk to Your Data architecture](talk-to-your-data-architecture.md) for
the complete identity, LLM, authorization, and query flow.

## Cloudera AI Application

Create an Application with:

- script `helios/apps/mcp/app.py`;
- a stable subdomain such as `helios-mcp`;
- the same standard PBJ Python 3.12 runtime used by the API;
- **Enable Unauthenticated Access** selected at the Cloudera gateway.

The last setting permits non-browser MCP protocol clients to reach the
Application. It does not make MCP anonymous: `/mcp` always requires the
configured bearer, and every tool requires a signed UI Principal or a
registered agent Principal. Missing bearer configuration returns 503.

The endpoints are:

```text
https://helios-mcp.<workbench-domain>/mcp
https://helios-mcp.<workbench-domain>/healthz
```

`/healthz` is public and returns only readiness, version, and model count.

## Environment

Required on MCP:

```text
HELIOS_MCP_TOKEN=<random bearer credential>
HELIOS_MCP_DELEGATION_SECRET=<random value of at least 32 bytes>
HELIOS_MCP_ALLOWED_HOSTS=helios-mcp.<workbench-domain>
```

The API Application must receive the same token and delegation secret, plus:

```text
HELIOS_MCP_URL=https://helios-mcp.<workbench-domain>/mcp
```

Configure `HELIOS_METADATA_DB`, `HELIOS_ROOT`, and `HELIOS_RUNS_DIR` only when
their project-persistent defaults are not correct. The API and MCP Applications
must resolve the same metadata grants and model artifacts.

For delegated queries, MCP also requires:

```text
IMPALA_HOST=<Virtual Warehouse JDBC host or URL>
WORKLOAD_USER=<approved Helios proxy account>
WORKLOAD_PASSWORD=<workload password>
IMPALA_PROXY_DELEGATION=true
```

The Virtual Warehouse must permit that account to proxy SSO subjects through
`doAs`, and Ranger must enforce the effective user. Helios verifies
`EFFECTIVE_USER()` before executing compiler-generated SQL. Leave
`IMPALA_PROXY_DELEGATION` false until this is proven in the target environment.

No custom Cloudera Runtime is required for MCP. Install the pinned API
requirements into the project-local dependency directory as described in
`ui-deployment.md`.

## Authentication and model context

The Helios API sends:

```text
Authorization: Bearer <HELIOS_MCP_TOKEN>
X-Helios-Principal-Assertion: <short-lived signed assertion>
```

The assertion carries the API-authenticated Principal, organization, and
locked model. MCP verifies it and reloads current grants before every tool
authorization.

For a separately registered external agent, set:

```text
HELIOS_MCP_DEFAULT_PRINCIPAL=<issuer>:<subject>
```

The client then sends `X-Helios-Model-ID`. The configured Principal must exist
in normal Helios grant records. A shared bearer alone never grants access.

## Current tools

- `list_models()` — grant-filtered available models.
- `describe_model(model)` — datasets, fields, metrics, and relationships.
- `search_semantics(question, limit, model)` — semantic matches.
- `describe(name, model)` — one semantic object.
- `compile_query(metrics, dimensions, filters, limit, engine, model)` —
  compile semantic inputs to SQL.
- `run_query(metrics, dimensions, filters, limit, model)` — compile and run
  through delegated Impala; maximum 1,000 rows.
- `explain_lineage(column, depth, model)` — Atlas column lineage.

Published `semantic.ossie.json` is the authoritative model source. MCP loads it
through the same `SemanticModel` and `Compiler` used by other Helios consumers.
If no model has been published, the latest proposal is converted to that
canonical in-memory shape before any tool reads it.

Dimensions and filters may use `dataset.field`, a physical
`database.table.field`, or an unambiguous field name, label, or synonym. Metrics
may use their canonical names or unambiguous synonyms. Filter objects accept
`column` for backward compatibility or the equivalent `field` key.

Filters use objects such as:

```json
{
  "metrics": ["Store Sales Revenue"],
  "dimensions": ["sales.store.state"],
  "filters": [
    {"column": "sales.date.year", "op": "=", "value": 2001}
  ],
  "limit": 20,
  "model": "sales-model"
}
```

Models are re-read when the published artifact changes. Authorization grants
are loaded from metadata for the current Principal; a requested model that
does not match the locked context is rejected. Expected lookup, ambiguity, and
compiler failures return structured `{error, message, retryable}` tool results
rather than terminating the MCP session.