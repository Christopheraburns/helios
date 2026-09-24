# Legacy Helios Console UI migration inventory

## Purpose and boundary

The FastAPI Application in `apps/console` currently serves two interfaces:

1. the authorized Helios API under `/api/v1`; and
2. the original Jinja2 Console UI at `/`, `/glossary`, `/term`, and `/runs`.

The second interface is being retired, but no route, template, or workflow is
removed in this step. The target architecture is:

```text
React/TypeScript Helios UI
        |
        | HTTPS JSON API only
        v
Helios API Application
        |
        +-- helios_core services
        +-- metadata, artifacts, Atlas, engines, discovery jobs
```

React must not import Python modules, read run/model files, call Atlas or
Impala, or reproduce authorization rules. Every migrated capability must use
an authenticated, resource-authorized Helios API.

## Key findings

- The legacy navigation exposes only **Health**, **Glossary**, and **Runs**.
- There is no legacy ontology page, semantic-model browser, or discovery-job
  launch form. Semantic information appears inside proposals and the
  review/publish workflow. Ontology information is not rendered at all.
- Discovery is performed outside the Console by the `harvest`, `profile`, and
  `propose` Jobs. The Console only reads their filesystem artifacts.
- Legacy routes use Cloudera Application authentication but do **not** call
  `current_principal`, `authorization_policy`, or any Helios resource-policy
  dependency. Consequently, their current Helios authorization action is
  effectively **none**. The actions in the matrix are the actions the
  replacement API must enforce.
- The authorized API already covers model selection, overview, graph
  navigation, lazy graph details, and individual proposal decisions.
- The new Canvas implements individual Approve, Reject, and Edit actions.
  However, Overview's **Review Proposals** and **Publish** links still navigate
  to the legacy `/runs/{run_id}/review` page on the API Application.
- `GET /api/v1/models/{model_id}/glossary`, `/semantic`, and `/ontology`
  currently return identifiers and permissions only. They are not content or
  CRUD APIs.
- FastAPI's generated `/docs`, `/redoc`, and `/openapi.json` are operational
  API surfaces and may remain after the general-purpose Console UI is removed.

## Authorization target

The migration should use the existing action vocabulary:

- operational readiness: no resource action; return no secret endpoint detail;
- authenticated diagnostics: authenticated Principal;
- model/run summaries: `model.read`;
- physical profile data: `datasource.read`;
- proposal/review reading and individual decisions: currently `model.edit`;
- publishing: `model.publish`;
- discovery launch: `discovery.run`;
- glossary reading/mutation: `glossary.read` / `glossary.edit`;
- semantic reading/mutation: `semantic.read` / `semantic.edit`;
- ontology reading/mutation: `ontology.read` / `ontology.edit`.

Where one response combines scopes, the backend must either require all
relevant actions or return an authorized projection. The frontend must not
infer missing permissions.

## Migration matrix

Status meanings:

- **Covered** — an API-backed equivalent exists in React.
- **Partial** — some data or workflow exists, but the legacy capability is not
  fully replaced.
- **API gap** — an authorized endpoint is required before migration.
- **Retain operationally** — this is not a general-purpose product UI.
- **No legacy capability** — no page/action exists to migrate.

| Legacy feature | Current route/page | Backend function/service | Existing authorized API | Required replacement action | New UI equivalent | API needed before migration | Recommended new UI destination | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Process readiness | None in legacy navigation; JSON endpoint already exists | `api_health()` | `GET /api/v1/healthz` | None | Initial connection state calls it | No | Operational endpoint only | Retain operationally |
| Dependency health dashboard | `GET /` | `health()`; `AtlasClient.ping()`, `ImpalaEngine.ping()`, `llm_from_env().ping()` | `GET /api/v1/models/{model_id}/status`; infrastructure details require `organization.manage` | `model.read` for compact model status; `organization.manage` for infrastructure checks | Overview shows compact API, semantic-model, and discovery/profile status; administrators can expand safe metadata, Atlas, Impala, and proposal-service checks | No for the migrated checks; MCP and Iceberg need explicit backend health contracts before they can be added | Overview → Helios health; detailed checks remain administrator-only | Covered |
| Principal and accessible-organization diagnostics | No old HTML page | `api_diagnostics()` and metadata grants | `GET /api/v1/diagnostics` | Authenticated Principal | Account indicator and connection state | No | Application shell/account diagnostics | Covered |
| List glossaries | `GET /glossary` | `glossaries()` → `AtlasClient.list_glossaries()` | `GET /api/v1/models/{model_id}/glossary` returns only `glossary_id` | `glossary.read` | Governance is a placeholder | Add model-scoped glossary collection/summary API | Governance → Glossary | API gap |
| Show glossary name, description, term count | `GET /glossary` | Atlas glossary documents | No content API | `glossary.read` | None | Include safe glossary summary DTO | Governance → Glossary | API gap |
| Create glossary | `POST /glossary` | `create_glossary()` → `AtlasClient.create_glossary()` | None | `glossary.edit` | None | Add model/organization-scoped create endpoint with validation and audit identity | Governance → Glossary | API gap |
| Delete glossary and terms | `POST /glossary/{guid}/delete` | `delete_glossary()` → `AtlasClient.delete_glossary()` | None | `glossary.edit` | None | Add authorized delete endpoint with dependency/impact response and confirmation contract | Governance → Glossary settings | API gap |
| Glossary detail and term list | `GET /glossary/{guid}` | `terms()` → `AtlasClient.get_glossary()` and `list_terms()` | No content API | `glossary.read` | None | Add paged/filterable glossary-term collection endpoint | Governance → Glossary detail | API gap |
| Filter terms by name or definition | `GET /glossary/{guid}?q=...` | Python in-memory case-insensitive filter in `terms()` | None | `glossary.read` | None | Add server-side authorized `query`, pagination, and limit parameters | Governance → Glossary search | API gap |
| Import Atlas glossary CSV | `POST /glossary/{guid}/import` | `import_terms()`; temp file; `AtlasClient.import_csv()` | None | `glossary.edit` | None | Add multipart import endpoint plus structured success/conflict/error DTO | Governance → Glossary import | API gap |
| New term form | `GET /glossary/{guid}/term/new` | `new_term()` renders Atlas glossary context | None | `glossary.edit` | None | Form itself needs no endpoint beyond glossary detail; create endpoint is required | Governance → Glossary term editor | API gap |
| Create glossary term | `POST /glossary/{guid}/term` | `create_term()` → `AtlasClient.create_term()` | None | `glossary.edit` | None | Add create-term endpoint for name, short/long description, abbreviation, and examples | Governance → Glossary term editor | API gap |
| View term details | `GET /term/{guid}` | `term()` → `AtlasClient.get_term()` and `assigned_entities()` | None | `glossary.read` | Proposed terms can appear on Canvas, but published Atlas term detail does not | Add term-detail DTO including authorized assignments | Governance → Glossary term detail; link from Inspector | API gap |
| Edit glossary term | `GET /term/{guid}/edit`; `POST /term/{guid}` | `edit_term()`, `update_term()` → `AtlasClient.update_term()` | None | `glossary.edit` | Canvas edit applies to discovery proposals, not published Atlas terms | Add update endpoint with validation and audit response | Governance → Glossary term editor | API gap |
| Delete glossary term | `POST /term/{guid}/delete` | `delete_term()` → `AtlasClient.delete_term()` | None | `glossary.edit` | None | Add authorized delete endpoint | Governance → Glossary term detail | API gap |
| Show linked Atlas columns | `GET /term/{guid}` | `AtlasClient.assigned_entities()` | No glossary assignment API; graph detail may show proposal business terms only | `glossary.read` and authorized physical visibility | Inspector has partial business-term display | Add authorized term-assignment collection that filters inaccessible assets | Governance term detail and Canvas Inspector | API gap |
| Link term to `database.table.column` | `POST /term/{guid}/assign` | `assign()`; `AtlasClient.find_column()` then `AtlasClient.assign()` | None | `glossary.edit` plus `datasource.read` for the target | None | Add assignment endpoint using an opaque authorized asset ID; do not require React to query Atlas names | Governance term detail / Canvas Inspector | API gap |
| Unlink term from column | `POST /term/{guid}/unassign/{entity_guid}` | `unassign()` → `AtlasClient.unassign()` | None | `glossary.edit` plus authorized target visibility | None | Add authorized assignment-delete endpoint with opaque IDs | Governance term detail / Canvas Inspector | API gap |
| List all filesystem runs | `GET /runs` | `runs()` → `runstore.list_runs()` | `GET /api/v1/models/{model_id}/runs` returns only runs associated with the authorized model | `model.read` | Overview shows latest lifecycle only; Models page is a placeholder | Existing model-scoped API is the correct boundary; enrich it with useful summary fields rather than exposing global runs | Models → Discovery Runs | Partial |
| Run stage status and updated time | `GET /runs` | `runstore.list_runs()` checks `harvest.json`, `profile.json`, `propose.json` | Model runs endpoint returns `id`, stages, updated time, and missing marker | `model.read` | Latest stage is summarized on Overview | No for basic list; pagination/status DTO may be needed at scale | Models → Discovery Runs | Partial |
| Run harvest summary | `GET /runs/{run_id}` | `run_detail()` → `runstore.summary()` and `load("harvest")` | No run-detail endpoint | `model.read`; redact data-source details not authorized to the Principal | Overview exposes only aggregate discovery status | Add model-owned run-detail endpoint | Models → Run detail | API gap |
| Run profile summary | `GET /runs/{run_id}` | `run_detail()` → `runstore.summary()` and `load("profile")` | Graph detail exposes selected latest-profile values only | `model.read` plus `datasource.read` for physical data | Canvas Inspector shows row count/type/null/cardinality/min/max when available | Add model/run-scoped profile summary and paged relationship/candidate endpoints | Models → Run detail; Data Sources → Profile | Partial |
| Tables, row counts, column counts, key candidates | `GET /runs/{run_id}` | Combines harvest tables and profile table entries | No run-specific table collection API | `datasource.read` | Dataset Inspector shows only selected-node details | Add authorized run table summaries; omit inaccessible assets | Data Sources → Profile / Run detail | API gap |
| Accepted profiled relationships | `GET /runs/{run_id}` | `profile.relationships` | Graph can expose relationships, evidence, match ratio, distinct/unmatched values | `datasource.read` / semantic read as projected by graph authorization | Canvas and Inspector cover graph-visible relationships | No if Canvas is accepted as the destination; run-specific comparison may need an optional `run_id` filter | Canvas Physical lens / Inspector | Covered/Partial |
| Suggested relationship candidates | `GET /runs/{run_id}` | `profile.suggested_relationships` | Proposal/review graph contains proposed relationships after `propose`, not raw profile candidates | `model.edit` and `datasource.read` | Canvas review covers proposals, not every profiler suggestion | Add run profile-candidate endpoint only if raw profiler triage remains a product requirement | Models → Run detail / Review queue | Partial |
| Rejected relationship/data-quality candidates | `GET /runs/{run_id}` | `profile.rejected_candidates` | No complete run-specific API | `datasource.read`; `model.edit` if treated as discovery review | Canvas can show rejected proposals, not profiler-rejected candidates | Add authorized candidate endpoint with evidence and status | Data Sources → Data quality / Run detail | API gap |
| Per-table profile page | `GET /runs/{run_id}/table/{key}` | `run_table()` → harvest/profile files | Graph detail returns latest profile lazily, not an arbitrary run | `datasource.read` | Canvas dataset/attribute Inspector partially covers it | Add run-scoped dataset-profile detail if historical runs must be browsable | Data Sources → Dataset profile; Canvas Inspector | Partial |
| Column type/null rate/distinct/min/max | Same table profile page | `profile.tables[key].stats.columns` | `GET /api/v1/models/{model_id}/graph/detail` exposes these for an authorized selected attribute from the latest profile | `datasource.read` | Canvas Attribute Inspector | No for current/latest model navigation; yes for historical run comparison | Canvas Inspector / Data Sources profile | Covered/Partial |
| Table glossary matches | Same table profile page | Harvest glossary terms mapped to qualified columns | No published glossary-assignment API | `glossary.read` plus physical visibility | Inspector may show proposal business terms only | Add authorized glossary assignment data to graph detail or glossary API | Canvas Inspector | API gap |
| Read full proposal | `GET /runs/{run_id}/propose` | `run_propose()` → `runstore.load("propose")` | Review graph/detail can project datasets, fields, relationships, metrics, and glossary terms | `model.edit` | Canvas review mode visualizes proposals and evidence | Add a review/run summary endpoint for LLM metadata, complete counts, paging, and non-graph proposal fields; do not expose raw files | Canvas review and Models → Proposal summary | Partial |
| Proposal LLM provider/model/call count | Proposal and review pages | Values in `propose.json["llm"]` | None | `model.edit` | None | Add safe proposal provenance/audit fields to review summary | Models → Proposal summary | API gap |
| Proposal dataset and field semantics | Proposal page | Proposal datasets/fields | `GET .../graph?review_run_id=...` and graph detail | `model.edit` | Canvas review mode and Inspector | No for graph review; summary/paging may still be required for completeness | Canvas Semantic/Physical lenses | Covered |
| Proposal relationships, confidence, source, reason | Proposal page | Proposal relationships | Review graph/edge detail returns state, confidence, evidence, and metadata where available | `model.edit` | Canvas edge review and Inspector | Enrich detail only for any reason/evidence fields not yet represented | Canvas review | Covered/Partial |
| Proposal metrics | Proposal page | Proposal metrics | Review graph/detail represents metrics | `model.edit` | Canvas Semantic review | No for individual review; list-oriented review may need review-summary paging | Canvas review | Covered |
| Proposed glossary terms | Proposal page | Proposal glossary terms | Review graph maps terms to generic concept nodes | `model.edit` | Canvas review can select and decide them | Rich glossary proposal details may need a small DTO if graph detail is insufficient | Canvas review / Governance proposal queue | Covered/Partial |
| Review dashboard and per-section counts | `GET /runs/{run_id}/review` | `review_page()` → `review.summary()`, decisions, preflight | Overview exposes unresolved total; decision POST returns a summary; no GET review-summary endpoint | `model.edit` | Canvas review has states but no full section count dashboard | Add `GET /api/v1/models/{model_id}/reviews/{run_id}` with summary, audit, validation/preflight, and publication state | Models → Review queue; Canvas review toolbar | API gap |
| Approve/reject one proposal | `POST /runs/{run_id}/review/decide` | `review.decide()` and `review.save()` | `POST /api/v1/models/{model_id}/reviews/{run_id}/decisions` | `model.edit` | Canvas Inspector Approve/Reject | No | Canvas review Inspector | Covered |
| Edit dataset proposal | Same decision route; inline editor | Overrides `name`, `kind`, `description` | Authorized decision endpoint accepts generic overrides | `model.edit` | Canvas Inspector provides JSON overrides rather than a typed form | No backend gap; add typed React controls for usability | Canvas dataset Inspector | Partial |
| Edit field semantic proposal | Same decision route; inline editor | Overrides `name`, `role`, `refers_to`, `description` | Authorized decision endpoint accepts overrides | `model.edit` | Generic JSON edit | No backend gap; typed role/refers-to editor recommended | Canvas attribute Inspector | Partial |
| Edit relationship proposal | Same decision route; inline editor | Overrides target table/column | Authorized decision endpoint accepts overrides | `model.edit` | Generic JSON edit | No backend gap; API should validate override schema before legacy retirement | Canvas relationship Inspector | Partial |
| Edit metric proposal | Same decision route; inline editor | Overrides name, expression, description | Authorized decision endpoint accepts overrides | `model.edit` / potentially `semantic.edit` in a future finer-grained policy | Generic JSON edit | Add typed validation/error DTO for expressions; mutation route already exists | Canvas metric Inspector | Partial |
| Edit glossary-term proposal | Same decision route; inline editor | Overrides name and definition | Authorized decision endpoint accepts overrides | `model.edit` / potentially `glossary.edit` in a future finer-grained policy | Generic JSON edit | No basic mutation gap; typed controls recommended | Canvas concept Inspector / Governance | Partial |
| Dataset cascade accept/reject including fields | `POST /runs/{run_id}/review/dataset` | `review.cascade_dataset()` | None | `model.edit` | Canvas decides one selected element at a time | Add authorized model/run cascade endpoint with changed IDs and updated summary | Canvas dataset Inspector | API gap |
| Bulk accept pending above confidence | `POST /runs/{run_id}/review/bulk` | `review.bulk_accept()` | None | `model.edit` | None | Add authorized bulk-decision endpoint with threshold validation, dry-run count, and result summary | Models → Review queue / Canvas toolbar | API gap |
| Reset all or one review section | `POST /runs/{run_id}/review/reset` | `review.clear()` | None | `model.edit` | None | Add authorized reset endpoint; require explicit confirmation and return audit/summary | Models → Review queue | API gap |
| Review audit (`reviewed_at`, `reviewed_by`, notes, overrides) | Review state and response data | `review.load/save()` | Graph detail and decision response expose available audit data | `model.edit` | Inspector displays returned audit metadata | Add review-summary GET for run-level audit; element audit is covered | Canvas Inspector / Review queue | Partial |
| Metric preflight errors before publish | Review page | `helios_core.ossie.preflight()` | None | `model.edit`; publish still requires `model.publish` | None | Include preflight and validation issues in review-summary/publish-readiness API | Models → Review queue / Overview action state | API gap |
| Publish reviewed Ossie model | `POST /runs/{run_id}/publish` | `review.apply()`, `ossie.build/preflight/validate`, `ArtifactStore.write_published_ossie()` | None | `model.publish` | Overview may show Publish, but it links to the legacy review page | Add authorized publish endpoint with validation failures, manifest, version, audit, and idempotency behavior | Overview and Models → Review queue | API gap |
| Optional Git commit during publish | Checkbox on review page | `subprocess.run(git add/git commit)` | None | No suitable end-user action | None | Do not migrate as a browser checkbox. Version/publish through an audited backend workflow; CI or operators own Git commits | No general UI destination | Retire behavior |
| Last-published timestamp and publish result | Review page | Published artifact mtime and redirect query messages | Overview has publication state, not publication event detail | `model.read`; publish result requires `model.publish` | Overview shows current state | Add publication/version metadata to overview or versions endpoint | Overview / Models → Versions | Partial |
| Published semantic model identity | No dedicated legacy page; produced by Publish | `ArtifactStore`, Ossie document | `GET /api/v1/models/{model_id}/semantic` returns ID only; graph shows authorized semantic projection | `semantic.read` | Semantic Canvas lens and Overview counts | Add semantic summary/version detail APIs only for information not represented by graph | Canvas Semantic lens / Models → Versions | Partial |
| Semantic model versions | No dedicated legacy page | Published files/metadata | `GET /api/v1/models/{model_id}/versions` returns associated IDs only | `semantic.read` | Models page is a placeholder | Enrich versions endpoint with timestamps, status, creator, and safe summary | Models → Versions | API gap |
| Ontology information/review | No legacy route, form, link, or template | None in Console UI | Ontology ID endpoint and Ontology Canvas lens exist | `ontology.read` / `ontology.edit` | Ontology lens exists; no authoring workflow | Define APIs only when ontology authoring/review becomes a product capability | Canvas Ontology lens / Governance | No legacy capability |
| Launch harvest/profile/propose discovery | No route; empty Runs page tells users to use Cloudera Jobs | External Jobs and `jobs/*` scripts | None | `discovery.run` | Disabled **Run Discovery** button explicitly states API is missing | Add model-scoped launch endpoint and run-status contract; do not invoke job scripts from React | Overview / Models → Discovery | API gap |
| Error and success pages | `error.html`, `notice.html`; no direct route | `AtlasError` exception handler and redirect messages | API errors use HTTP status/detail; mutation DTOs vary | Same as attempted operation | React has loading/error/empty states | Standardize structured API errors for imports, validation, publish, and long-running jobs | Inline page notifications/toasts | Partial |
| Legacy top navigation and status styling | `base.html`, `console.css`, `review.css` | Jinja layout/static files | Not applicable | Not applicable | React application shell replaces it | No | New UI shell | Covered |
| Duplicate misspelled review template | `templates/review.htmnl` is not routed | None beyond duplicate static file | Not applicable | Not applicable | None | No; remove only during final legacy cleanup | None | Dead artifact |

## Smallest API additions, in migration order

### Phase 1 — remove React dependencies on legacy pages

1. Add `GET /api/v1/models/{model_id}/reviews/{run_id}` for complete review
   summary, audit, preflight issues, and publish readiness.
2. Add `POST /api/v1/models/{model_id}/reviews/{run_id}/publish`, requiring
   `model.publish`.
3. Change Overview's **Review Proposals** link to open Canvas review mode,
   preferably with a deep-linkable `review_run_id`.
4. Change **Publish** to call the authorized publish API and reconcile Overview
   and Canvas from the backend.
5. Add authorized cascade, bulk-decision, and reset operations if those legacy
   productivity features are still required.

After Phase 1, proposal review and publishing no longer require
server-rendered pages.

### Phase 2 — migrate runs, profiles, and discovery

1. Enrich the model-scoped runs collection and add model/run detail.
2. Add authorized run profile summaries and historical dataset-profile detail.
3. Implement Models → Discovery Runs and Data Sources → Profile pages.
4. Add a `discovery.run` launch endpoint plus asynchronous status semantics.
   The API should invoke the supported Cloudera Job mechanism; React must not
   execute Python scripts or inspect job files.

### Phase 3 — migrate glossary management

1. Add model-scoped glossary and term read APIs.
2. Add glossary/term create, update, delete, import, assignment, and
   unassignment APIs with `glossary.edit`.
3. Filter all physical assignments through datasource authorization.
4. Implement Governance → Glossary and Inspector links.

Direct browser-to-Atlas calls are not an acceptable shortcut.

### Phase 4 — complete model/version and operations surfaces

1. Enrich semantic version and publication metadata APIs.
2. Decide whether dependency health belongs in an administrator-only React
   page or external monitoring.
3. Add ontology authoring APIs only when that capability is defined; there is
   no legacy ontology workflow to preserve.

## Final retirement gate

Do not remove `apps/console/templates`, `apps/console/static`, or legacy routes
until all of the following are true:

- no React route or action links to `/glossary`, `/term`, or `/runs`;
- publish, review, glossary, run, and discovery mutations use authorized APIs;
- API tests prove the required action and model/organization/run ownership for
  every endpoint;
- React tests cover loading, empty, denied, failure, and successful
  reconciliation states;
- audit identity comes from the authenticated Principal, not an untrusted
  browser value;
- deep links exist for model, run, review, glossary term, and relevant Canvas
  focus contexts; and
- operational readiness/API documentation remain available without retaining
  a general-purpose HTML Console.

At that point, the API Application root can return a small service document or
redirect to `/docs`, while `/api/v1/healthz`, `/openapi.json`, `/docs`, and
`/redoc` remain as appropriate operational interfaces.
