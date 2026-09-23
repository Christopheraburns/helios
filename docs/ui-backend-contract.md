# Helios UI backend contract (current state)

This document records the backend interfaces available to a future Helios UI as
of 2026-09-23. It describes the current implementation; it is not a replacement
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

The UI-facing API application is the FastAPI app `apps.console.main:app`.
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

The separate React/TypeScript UI lives under `apps/ui`. Its Cloudera
Application entry point, `apps/ui/app.py`, serves the Vite production build
through `apps.ui.server:app`. Browser requests use the configured
`HELIOS_API_URL`; credentials are included so Cloudera's authenticated browser
identity, rather than the UI Application's service identity, reaches the API.

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

Resource routes require Cloudera's trusted `REMOTE-USER` header. The legacy
`x-forwarded-user` header remains a fallback, and `HELIOS_DEV_USER` is an
explicit local-development fallback when `HELIOS_DEV=1`. Missing identity
returns HTTP 401 with:

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

The router prefix is `/api/v1`. Read operations use GET and proposal review
decisions use POST. The model collection accepts an organization filter.
`GET /api/v1/healthz` is an unauthenticated process
readiness check. `GET /api/v1/diagnostics` returns authenticated principal
identity and accessible organization count. An authenticated principal with no
Helios grants receives a successful response with a count of zero.

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

`GET /api/v1/organizations`

- Returns organization summaries reachable through any organization- or
  model-scoped grant held by the current principal.
- Each summary contains `id`, `name`, and `available_actions`.
- Model-scoped users receive the organization context required for navigation
  without being granted `organization.read`.

### Models

`GET /api/v1/models?organization_id={org_id}`

- Returns only models for which the current principal has `model.read`.
- Organization administrators receive accessible models in their
  organization; model-scoped grants return only their explicit models.
- Returns the standard model summary and `available_actions`.

`GET /api/v1/models/{model_id}`

- Requires `model.read`.
- Returns the same model shape used by the organization model list.

`GET /api/v1/models/{model_id}/overview`

- Requires `model.read`.
- Returns persisted status, creator and timestamps; data-source summaries;
  permission-filtered dataset, relationship, concept, and metric counts; and
  publication, discovery, and review state.
- `unresolved_review_items` is `null` when no review information exists.
- Counts come from the backend's authorized graph projection. The UI must not
  inspect model artifacts to recreate them.

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
- draft, proposed, needs-review, approved, and rejected objects also require
  `model.edit`;
- edges with a hidden endpoint are removed; and
- permitted actions are computed independently for every returned object.

The full response remains available for compatibility. Canvas clients should
request bounded navigation responses with `navigation=true`, optionally using:

- `focus_node_id` and `depth` (0–3) for a local neighborhood;
- `lens=physical|semantic|ontology` to project the same authorized model graph
  for a particular navigation purpose;
- `include_attributes=true` when a dataset is explicitly expanded;
- `limit` (maximum 500) and `edge_limit` (maximum 2,000) to bound the
  response; or
- `query` for server-side authorized node search.

Authorization is applied to the complete graph before neighborhood traversal
or search. Navigation responses add authorized/returned counts, truncation,
hidden-neighbor counts, and expandable node IDs under `navigation`.

`GET /api/v1/models/{model_id}/graph/detail?element_id={id}`

- Lazily returns detail for one node or edge after applying the same graph
  authorization projection.
- May include physical identity, source, schema, profile statistics, semantic
  role, business terms, relationship evidence, and governance state when those
  values exist in model or discovery artifacts.
- Missing values are omitted rather than inferred.
- `available_actions` contains only server-authorized actions for that element.

### Proposal review graph and decisions

Editors can add `review_run_id={run_id}` to graph and graph-detail requests.
The run must belong to the requested model and both routes require
`model.edit`. The projection represents proposal elements from that discovery
run with statuses `needs_review`, `approved`, or `rejected`. Proposal metadata
contains opaque `review_section` and `review_element_id` values for authorized
mutation requests. Detail responses include review note, overrides, reviewer,
and review timestamp when available.

`POST /api/v1/models/{model_id}/reviews/{run_id}/decisions`

- Requires `model.edit`.
- Accepts `section`, `element_id`, and `decision` (`accept`, `reject`, or
  `edit`), plus optional `overrides` and `note`.
- Verifies that the run belongs to the model and that the element exists in
  that run's immutable proposal.
- Persists the review decision and authenticated principal ID, then returns
  the entry, review timestamp, reviewer, and updated summary.
- Clients wait for success and then refetch the affected graph and detail; the
  API does not provide an optimistic-state contract.

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

Known node kinds include `domain`, `concept`, `data_source`, `dataset`,
`attribute`, and `metric`. Known edge kinds include `physical_relationship`,
`semantic_relationship`, `inferred_relationship`, and
`ontology_relationship`. The DTO is deliberately schema-agnostic: details vary
by kind inside `metadata`.

Physical kinds require `datasource.read`; concepts and ontology relationships
require `ontology.read`; the domain root requires `model.read`; remaining
semantic kinds require `semantic.read`. A `model_consumer` does not have
`datasource.read`, so its authorized graph can omit datasets, attributes, and
physical edges even though it can read semantic and ontology resources.

## User interfaces

The primary visual UI is the React/TypeScript application in `apps/ui`. It
uses only this HTTP contract for selectors, overview, Canvas navigation,
Inspector details, and proposal review.

The legacy console also includes Jinja2 HTML with CSS in
`apps/console/static`. It
contains health, Atlas glossary/term CRUD, discovery run browsing, proposal
display, review, and publish pages. Review interactions use inline JavaScript
`fetch`; despite the module docstring, the current templates do not use HTMX.

These routes predate the future API-only frontend boundary. Their server-side
handlers call Atlas clients and filesystem run/artifact helpers directly.
They are implementation code, not reusable browser contracts.

## Current UI coverage and remaining limits

Existing APIs need not be redesigned for the covered capabilities.

1. **Organization selector**
   - Addressed by `GET /api/v1/organizations`.
   - Organization slug remains internal and is not needed by the selector.

2. **Model selector**
   - Addressed by the grant-filtered `GET /api/v1/models` collection.

3. **Model overview**
   - Addressed by `GET /api/v1/models/{model_id}/overview`.
   - Glossary, semantic, and ontology endpoints still return IDs rather than
     rich summaries.

4. **Authorized semantic canvas**
   - Addressed by authorized bounded navigation, search, lenses, and
     incremental neighborhood requests.
   - Layout remains a frontend concern; the API intentionally does not expose
     React Flow structures or coordinates.
   - Published Ossie metric formulas, field expressions, dimensions, and
     relationship details are not currently projected into graph metadata.
   - Consumers without `datasource.read` can receive a sparse graph with
     semantic objects but no physical dataset/attribute context.

5. **Node inspector**
   - Addressed by the authorized graph-detail endpoint for available artifact
     and profile data.
   - Atlas term enrichment and several artifact-specific details remain
     unavailable through this endpoint.

6. **Proposal review**
   - Dataset, field/semantic-role, relationship, metric, and glossary-term
     proposals can be reviewed.
   - There is no separate concept-mapping proposal type in the current
     discovery contract, so the UI does not fabricate one.

## Verification baseline

The backend and frontend suites cover the API authorization boundary, review
mutation/reconciliation, CORS preflights, graph adapters, and application
shell. The only known backend warning is a Starlette `TestClient` deprecation
for AnyIO's `BlockingPortal` alias.
