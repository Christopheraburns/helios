# Helios UI backend contract (current state)

This document records the backend interfaces available to a future Helios UI as
of 2026-09-22. It describes the current implementation; it is not a replacement
API design.

## Contract boundary

The future frontend must use the Helios HTTP API as its only data and
authorization contract. It must not:

- import `helios_core` Python modules;
- read model YAML or JSON artifacts directly;
- query `helios_meta` or the SQLite metadata database directly;
- call Impala or Atlas directly; or
- derive permissions from roles or resource state in the browser.

The browser must render only resources returned by the API and enable actions
only when the API returns the corresponding `available_actions` or
`permitted_actions` value.

## Application and startup

The UI-facing application is the FastAPI app `apps.console.main:app`.
`apps/console/main.py` creates the app, migrates a
`SQLiteMetadataRepository`, mounts `/static`, configures Jinja templates, and
includes the `/api/v1` and review routers.

In Cloudera AI Workbench, `apps/console/app.py` is the Application script.
Because Workbench executes it in a Jupyter kernel, it starts uvicorn as a child
process:

`python -m uvicorn apps.console.main:app --host 127.0.0.1 --port $CDSW_APP_PORT`

The checkout is resolved from `HELIOS_ROOT`, or from
`$CDSW_PROJECT_DIR/helios`. `HELIOS_DEV=1` enables uvicorn reload.

The repository also contains a separate MCP Application. `apps/mcp/app.py`
starts `apps.mcp.server:app` in the same manner. MCP is an agent-facing
interface, not the browser UI contract.

The custom runtime is built by `runtime/Dockerfile` and `runtime/build.sh` on a
Cloudera PBJ Workbench Python 3.12 base. `runtime/requirements.txt` includes
FastAPI/uvicorn, MCP, HTTP clients, Impala/Hive clients, SQLGlot, DuckDB,
Pydantic, JSON Schema, YAML, pandas, PyArrow, and LLM clients. Jinja2 is used by
the console but is not explicitly pinned. Pytest is also not listed, so the
test suite requires a separate test dependency installation.

Relevant configuration includes `HELIOS_ROOT`, `HELIOS_METADATA_DB`,
`HELIOS_RUNS_DIR`, `CDSW_APP_PORT`, Atlas credentials, Impala connection
settings, and LLM provider settings.

## Authentication and authorization

All current `/api/v1` routes require the trusted `x-forwarded-user` header.
Missing identity returns HTTP 401 with:

```json
{"detail": "authenticated principal is required"}
```

The API creates a human `Principal` with issuer `cloudera-workbench`, subject
and display name equal to the header value, and stable ID
`cloudera-workbench:<subject>`. The API does not create or update a persisted
principal record during a request.

The default policy loads grants for that principal ID from the metadata
repository. Grants are resource-scoped. Organization administrators inherit
permissions over resources in their organization; other roles are scoped to a
specific model.

Authorization failures return HTTP 403 in FastAPI's
`{"detail": "<message>"}` envelope. Unknown organizations and models return
HTTP 404. The API loads the resource before checking its permission, so these
responses are intentionally distinct.

The complete action vocabulary is:

- `organization.read`, `organization.manage`
- `datasource.read`, `datasource.manage`
- `model.create`, `model.read`, `model.edit`, `model.delete`, `model.publish`
- `discovery.run`
- `glossary.read`, `glossary.edit`
- `semantic.read`, `semantic.edit`
- `ontology.read`, `ontology.edit`
- `query.compile`, `query.execute`

Model responses expose the actions currently allowed to the caller as
`available_actions`. Graph nodes and edges expose their own
`permitted_actions`. These fields, not client-side role interpretation, are
the UI authorization contract.

## Current REST API

The router prefix is `/api/v1`. All current operations are GET requests and
have no query parameters.

### Organizations

`GET /api/v1/organizations/{org_id}`

- Requires `organization.read`.
- Returns `id`, `name`, and `member_ids`.
- The persisted organization slug is not returned.

`GET /api/v1/organizations/{org_id}/models`

- Requires `organization.read` on the organization.
- Returns `organization_id` and all models in that organization.
- Each model has `id`, `organization_id`, `name`, `description`,
  `data_sources`, and `available_actions`.
- Each data-source reference has only `data_source_id` and `selected_assets`.

There is no endpoint that lists organizations available to the current
principal.

### Models

`GET /api/v1/models/{model_id}`

- Requires `model.read`.
- Returns the same model shape used by the organization model list.

`GET /api/v1/models/{model_id}/glossary`

- Requires `glossary.read`.
- Returns `model_id`, `glossary_id`, and `available_actions`; it does not
  return glossary content.

`GET /api/v1/models/{model_id}/semantic`

- Requires `semantic.read`.
- Returns `model_id`, `semantic_model_id`, and `available_actions`; it does
  not return the semantic document.

`GET /api/v1/models/{model_id}/ontology`

- Requires `ontology.read`.
- Returns `model_id`, `ontology_id`, and `available_actions`; it does not
  return ontology content.

`GET /api/v1/models/{model_id}/runs`

- Requires `model.read`.
- Returns `model_id`, `runs`, and `available_actions`.
- Runs are resolved from the filesystem using `Model.discovery_run_ids`.

`GET /api/v1/models/{model_id}/versions`

- Requires `semantic.read`.
- Returns `model_id`, `versions`, and `available_actions`.
- Versions come from `Model.version_ids`.

The SQLite model loader currently does not populate `discovery_run_ids` or
`version_ids`, so the two list responses are normally empty when using the
production repository.

### Authorized model graph

`GET /api/v1/models/{model_id}/graph`

The route first requires `model.read`. `ArtifactGraphRepository` then builds a
graph from only the requested model:

1. a published model-scoped Ossie JSON artifact, if present;
2. otherwise the newest model-scoped proposal JSON artifact; or
3. otherwise a configured base graph from model and data-source metadata.

The server projects that graph through authorization before serialization:

- cross-organization and cross-model objects are removed;
- each object requires the read action associated with its kind;
- draft, proposed, and rejected objects also require `model.edit`;
- edges with a hidden endpoint are removed; and
- permitted actions are computed independently for every returned object.

The response shape is:

```json
{
  "model_id": "model-id",
  "organization_id": "organization-id",
  "nodes": [
    {
      "id": "dataset:orders",
      "kind": "dataset",
      "label": "orders",
      "status": "published",
      "confidence": null,
      "evidence": null,
      "metadata": {},
      "permitted_actions": ["datasource.read"]
    }
  ],
  "edges": [
    {
      "id": "relationship:orders_customer",
      "kind": "semantic_relationship",
      "source": "dataset:orders",
      "target": "dataset:customer",
      "status": "published",
      "confidence": null,
      "evidence": null,
      "metadata": {},
      "permitted_actions": ["semantic.read"]
    }
  ],
  "summary": {
    "node_count": 1,
    "edge_count": 0,
    "node_kinds": ["dataset"],
    "edge_kinds": []
  }
}
```

Known node kinds include `domain`, `data_source`, `dataset`, `attribute`, and
`metric`. Known edge kinds include `physical_relationship`,
`semantic_relationship`, and `inferred_relationship`. The DTO is deliberately
schema-agnostic: details vary by kind inside `metadata`.

Physical kinds require `datasource.read`; concepts and ontology relationships
require `ontology.read`; the domain root requires `model.read`; remaining
semantic kinds require `semantic.read`. A `model_consumer` does not have
`datasource.read`, so its authorized graph can omit datasets, attributes, and
physical edges even though it can read semantic and ontology resources.

## Existing server-rendered UI

The current console is Jinja2 HTML with CSS in `apps/console/static`. It
contains health, Atlas glossary/term CRUD, discovery run browsing, proposal
display, review, and publish pages. Review interactions use inline JavaScript
`fetch`; despite the module docstring, the current templates do not use HTMX.

These routes predate the future API-only frontend boundary. Their server-side
handlers call Atlas clients and filesystem run/artifact helpers directly.
They are implementation code, not reusable browser contracts. There is no
React, Vue, or other separate frontend package.

## UI-enabling gaps

Only gaps that block or materially limit the requested UI are listed here.
Existing APIs need not be redesigned.

1. **Organization selector**
   - No endpoint lists organizations visible to the current principal.
   - Organization slug is not exposed.
   - A caller must already know an organization ID.

2. **Model selector**
   - The only model list requires a known organization ID and
     `organization.read`.
   - A principal with only model-scoped access cannot discover those models.
   - There is no current-principal model list or grant-filtered model search.

3. **Model overview**
   - Model status, creator, and creation/update timestamps are persisted but
     not exposed.
   - Data-source display metadata is absent; only IDs and selected assets are
     returned.
   - Glossary, semantic, and ontology endpoints return IDs, not summaries.
   - Persistent run and version associations are not loaded into `Model`.

4. **Authorized semantic canvas**
   - The graph endpoint is the correct authorized topology source.
   - It provides no layout coordinates, filtering, pagination, or incremental
     expansion.
   - Published Ossie metric formulas, field expressions, dimensions, and
     relationship details are not currently projected into graph metadata.
   - Consumers without `datasource.read` can receive a sparse graph with
     semantic objects but no physical dataset/attribute context.

5. **Node inspector**
   - There is no node-detail endpoint.
   - The UI can inspect only the metadata already included in the full graph
     response.
   - There is no API mapping a graph node to richer Ossie, proposal, Atlas
     term, profile, or evidence details.

## Verification baseline

The complete existing suite passed before this document was added:
`75 passed, 1 warning`. The warning is a Starlette `TestClient` deprecation
for AnyIO's `BlockingPortal` alias. Pytest was supplied from an isolated
temporary install because it is not part of the repository runtime
requirements.
