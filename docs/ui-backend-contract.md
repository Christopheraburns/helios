# Helios UI backend contract (current state)

This document records the backend interfaces available to a future Helios UI as
of 2026-09-24. It describes the current implementation; it is not a replacement
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

`GET /api/v1/models/{model_id}/status?details={false|true}`

- Requires `model.read`.
- The compact response reports API availability, model publication state,
  discovery/profile state, and the most recent successful harvest and profile
  artifacts for the requested model.
- Each component uses `healthy`, `degraded`, `unavailable`, or `unknown`;
  partial failures do not prevent other component results from being returned.
- `details=true` additionally requires `organization.manage` and runs
  independent metadata repository, Atlas, Impala, and proposal-service checks.
- Detailed checks return safe descriptions only. They do not return hosts,
  connection strings, credentials, tokens, or raw exception messages.
- MCP and Iceberg are not reported as separate checks because the current
  backend has no configured, authenticated MCP-to-API health contract and no
  independent Iceberg connectivity probe.

`GET /api/v1/models/{model_id}/glossary`

- Requires `glossary.read`.
- Returns the model-linked Atlas glossary summary, term count, and
  `available_actions`. A model without a linked glossary returns
  `glossary: null`.

The model-scoped glossary contract also provides:

- `POST|DELETE /api/v1/models/{model_id}/glossary` to create/bind or
  confirmed-delete/unbind the model glossary;
- `GET|POST /api/v1/models/{model_id}/glossary/terms` for an authorized,
  paged, searchable, sortable term collection and term creation;
- `GET|PATCH|DELETE .../glossary/terms/{term_id}` for lazy detail and CRUD;
- `POST .../glossary/import` for a UTF-8 Atlas CSV whose `GlossaryName`
  matches the linked glossary;
- `GET .../glossary/assignable-assets` and term assignment create/delete
  routes. These accept Helios Canvas element IDs and resolve Atlas entities
  only after `datasource.read` and graph visibility checks.

Writes require `glossary.edit`. Every term is checked against the glossary
bound to the requested model, and inaccessible physical assignments are
omitted rather than exposed. React never calls Atlas directly.

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
- Each run contains the artifact-backed `harvest`, `profile`, and `propose`
  phases actually returned by the API, stage availability, aggregate counts,
  lifecycle fields, warnings, and errors.
- Lifecycle `status`, `progress`, initiator, and timestamps are nullable.
  Existing harvest/profile/proposal files do not prove that a producer is
  queued or running, who initiated it, or that it failed. A proposal artifact
  is currently the only evidence used to report `completed` and 100 percent
  progress. Missing or malformed evidence remains unavailable rather than
  being inferred.

`GET /api/v1/models/{model_id}/runs/{run_id}`

- Requires `model.read`, verifies that the run belongs to the model, and
  returns the same lifecycle contract plus source and proposal provenance.
- Returns HTTP 404 when the run is not model-owned or has no readable
  artifacts.

`GET /api/v1/models/{model_id}/runs/{run_id}/profile`

- Requires both `model.read` and `datasource.read`.
- Returns safe harvested/profiled table summaries, row and column counts,
  primary-key candidates, and accepted, suggested, and rejected relationship
  evidence from that exact run.
- Relationship evidence is allowlisted; unknown artifact fields are not
  returned. Missing or malformed artifact collections become empty evidence
  rather than leaking raw artifact content.

`GET /api/v1/models/{model_id}/runs/{run_id}/profile/tables/{table_id}`

- Requires both `model.read` and `datasource.read`.
- Returns the historical table statistics from that exact run, proposal-safe
  provenance, primary-key candidates, exact-versus-approximate distinct-value
  evidence, run-harvest glossary matches, accepted relationships, and Canvas
  metadata. It does not silently substitute the latest profile.
- Returns HTTP 404 when the table or profile artifact is unavailable.

`GET /api/v1/models/{model_id}/runs/{run_id}/proposals`

- Requires `model.edit` and model/run ownership.
- Requires one of `datasets`, `fields`, `relationships`, `metrics`, or
  `glossary_terms` as `section`; there is no ontology-mapping proposal type.
- Supports `decision`, `query`, `offset`, and `limit` filters and returns typed
  proposal data, confidence/provenance, review audit, paging, permitted
  actions, and Canvas deep-link metadata.

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

`GET /api/v1/models/{model_id}/reviews/{run_id}`

- Requires `model.edit`.
- Returns per-section decision counts, review audit data, preflight issues,
  publication validation errors, publication readiness, the current
  publication manifest (when present), and `available_actions`.
- Review actions are `decide`, `cascade`, `bulk_accept`, `reset`, and
  `publish`; clients must not infer them from roles.

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

`POST /api/v1/models/{model_id}/reviews/{run_id}/decisions/dataset`

- Requires `model.edit`.
- Accepts `table` and an `accept` or `reject` decision, applying it to the
  dataset and all of its attributes.

`POST /api/v1/models/{model_id}/reviews/{run_id}/decisions/bulk`

- Requires `model.edit`.
- Accepts `min_confidence` from 0 through 1 and accepts currently pending
  elements at or above the threshold.

`POST /api/v1/models/{model_id}/reviews/{run_id}/reset`

- Requires `model.edit`.
- Accepts an optional review `section`; omitting it clears all decisions
  without modifying the immutable proposal.

The mutation endpoints record the authenticated reviewer and return the
updated review summary.

The React proposal workspace renders those same five proposal types. It
provides typed editors for dataset, field, relationship, metric, and glossary
term overrides; individual accept/reject/edit; dataset-and-field cascade;
confidence-threshold bulk acceptance; section reset; and publish feedback.
Controls are enabled only by returned `available_actions`.

`POST /api/v1/models/{model_id}/reviews/{run_id}/publish`

- Requires `model.publish`.
- Applies review decisions, validates the generated Ossie document, and
  writes model-scoped published YAML, JSON, and manifest artifacts.
- Validation failures return HTTP 422 with
  `detail.code=publication_validation_failed` and an `errors` list.
- The API does not expose the legacy Console's optional Git commit behavior.
- Clients refetch review, graph, detail, and overview state after success.

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

### Persistent audit activity

`GET /api/v1/audit/events`

- Returns paginated, redacted events for the authenticated Principal.
- Supports organization, Principal, session, model, component, event type,
  outcome, severity, time range, offset, and limit filters.
- `include_all=true` and another `principal_id` require
  `organization.manage` for the requested organization.

`GET /api/v1/audit/events/{event_id}`

- Returns an event owned by the Principal or an event in an organization they
  manage.
- Returns 404, rather than 403, for a cross-user event the caller cannot read.

`GET /api/v1/audit/sessions`

- Groups visible events by browser/job session.
- Uses the same own-session default and organization-admin expansion.

`POST /api/v1/audit/client-events`

- Accepts only allowlisted navigation, context-selection, workspace, Canvas,
  and activity-filter actions.
- Does not accept arbitrary details or identity. Model references are checked
  with `model.read`.

The browser stores an opaque UUID in `sessionStorage` and sends it as
`X-Helios-Session-ID`. CORS explicitly permits that header. The API returns
`X-Helios-Request-ID`; correlation IDs never replace SSO identity.

## User interfaces

The primary visual UI is the React/TypeScript application in `apps/ui`. It
uses only this HTTP contract for selectors, overview, Canvas navigation,
Inspector details, and proposal review.

Implemented routes are:

- `/` — selected model overview;
- `/models` — model-scoped discovery activity;
- `/models/runs/{run_id}` — run detail and proposal workspace;
- `/models/runs/{run_id}/profile/{table_id}` — historical table profile; and
- `/canvas` — current or historical review graph; and
- `/activity` — own-session audit activity with organization-admin expansion.

Every route preserves the authorized `organization` and `model` query
parameters. React does not link to the legacy `/runs` pages.

Canvas deep links accept `lens=physical|semantic|ontology`,
`review_run_id`, `focus_node_id`, `element_id`, and a comma-separated
`related_node_ids`. The graph is loaded around `focus_node_id`; `element_id`
selects a returned node or edge in the Inspector; related node IDs guide
multi-node fitting for relationship links. Invalid or unauthorized focus and
selection values are removed rather than used to bypass graph projection.

The run list and detail poll every five seconds only while the API returns
`queued` or `running`. Polling stops for terminal states (`completed`,
`completed_with_warnings`, `failed`, or `cancelled`) and for nullable status.
A transition from active to terminal refreshes the model overview. A refresh
failure leaves the last successful run data visible with retry feedback.
Run detail automatically loads the authorized profile summary and renders
linked harvested/profiled tables plus accepted, suggested, and rejected
relationship groups. Historical table detail renders key candidates, glossary
matches, cardinality accuracy, and accepted relationship evidence.

The legacy console also includes Jinja2 HTML with CSS in
`apps/console/static`. It
contains health, Atlas glossary/term CRUD, discovery run browsing, proposal
display, review, and publish pages. Review interactions use inline JavaScript
`fetch`; despite the module docstring, the current templates do not use HTMX.

These routes predate the future API-only frontend boundary. Their server-side
handlers call Atlas clients and filesystem run/artifact helpers directly.
They are implementation code, not reusable browser contracts.

## Talk to Your Data

The `/talk` UI uses only the model-scoped conversation API:

- `GET /api/v1/models/{model_id}/conversations`
- `POST /api/v1/models/{model_id}/conversations`
- `GET /api/v1/models/{model_id}/conversations/{conversation_id}`
- `POST /api/v1/models/{model_id}/conversations/{conversation_id}/turns`

Every request derives the Principal from the normal authenticated API request
and requires `model.read`. Conversations are private to their creating
Principal and selected Model. An append includes `expected_version`; stale
clients receive HTTP 409 and must reload.

The persistent contract contains conversation summaries and user/assistant
message text. Current-turn query rows, SQL, and sanitized MCP tool activity are
returned with create/append responses but deliberately are not persisted. The
API's server-side conversation layer supplies bounded history to the LLM and
continues to execute semantic and data operations exclusively through Helios
MCP. The browser never receives Mistral, MCP, Impala, or delegation secrets.

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

7. **Explicit follow-ups**
   - The API does not persist producer lifecycle events. Accurate queued,
     running, failed, cancelled, progress, initiator, warning, and error values
     require a future producer-owned status store.
   - There is no discovery launch or cancellation endpoint. The UI does not
     invoke job scripts and exposes no cancel action.
   - Published glossary CRUD, CSV import, and authorized physical assignments
     are available through the model-scoped governance API. Semantic-model
     version history and producer persistence remain separate follow-up work.

Run/profile parity with the legacy `run.html` and `run_table.html` read-only
evidence is complete. Governance → Glossary replaces the legacy Atlas
glossary/term CRUD, import, and assignment workflows while retaining proposal
review as a distinct model-run workflow.

## Verification baseline

The backend and frontend suites cover the API authorization boundary, review
mutation/reconciliation, CORS preflights, graph adapters, and application
shell. The only known backend warning is a Starlette `TestClient` deprecation
for AnyIO's `BlockingPortal` alias.
