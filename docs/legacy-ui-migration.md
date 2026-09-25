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
  Overview review links use Canvas `review_run_id` deep links and publish uses
  the authorized API. React has no links to legacy `/runs` pages.
- `GET /api/v1/models/{model_id}/glossary`, `/semantic`, and `/ontology`
  currently return identifiers and permissions only. They are not content or
  CRUD APIs.
- FastAPI's generated `/docs`, `/redoc`, and `/openapi.json` are operational
  API surfaces and may remain after the general-purpose Console UI is removed.

## Implemented run and review migration

The React routes now include:

- `/models` for model-scoped discovery activity;
- `/models/runs/{run_id}` for artifact-backed run detail and proposal review;
- `/models/runs/{run_id}/profile/{table_id}` for an exact historical profile;
  and
- `/canvas` deep links using `organization`, `model`,
  `review_run_id`, `lens`, `focus_node_id`, `element_id`, and optional
  `related_node_ids`.

The runs collection and detail require `model.read`; historical physical
profile summaries and table profiles additionally require `datasource.read`;
proposal collections and review mutations require `model.edit`; publish
requires `model.publish`. Model/run ownership is checked before artifacts are
returned.

Run lifecycle is evidence-backed and nullable. The current artifact producer
does not persist queued/running/failed/cancelled state, progress, initiator, or
diagnostic events. The API reports completion only when a proposal artifact
exists and otherwise leaves unsupported lifecycle values unavailable. React
polls every five seconds only for explicit `queued` or `running` responses,
stops for terminal or null status, and refreshes model overview after an active
run becomes terminal.

Proposal collections cover datasets, fields, relationships, metrics, and
glossary terms, with decision/search filters and paging. React provides typed
edits, individual approve/reject, dataset cascade, threshold bulk acceptance,
section reset, and publish feedback according to server-returned actions.
There is no ontology-mapping proposal contract.

Run/profile read parity is complete: run detail automatically lists
harvested/profiled tables with links and key candidates, and displays accepted,
suggested, and rejected relationship evidence. Historical table detail shows
primary-key candidates, exact-versus-approximate cardinality, glossary matches
derived from the same run's harvest artifact, and accepted relationships.

Explicit follow-ups remain:

- add producer-owned lifecycle persistence before claiming live run progress
  or diagnostic status;
- add supported discovery launch and cancellation APIs before enabling those
  actions in React; and
- complete glossary CRUD and richer model-version surfaces separately.

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
| Model-linked glossary summary | `GET /glossary` | `glossaries()` → `AtlasClient.list_glossaries()` | `GET /api/v1/models/{model_id}/glossary` returns the linked glossary summary | `glossary.read` | Governance → Glossary summary | No | Governance → Glossary | Covered |
| Show glossary name, description, term count | `GET /glossary` | Atlas glossary documents | Included in the model glossary summary | `glossary.read` | Glossary header and count | No | Governance → Glossary | Covered |
| Create glossary | `POST /glossary` | `create_glossary()` → `AtlasClient.create_glossary()` | `POST /api/v1/models/{model_id}/glossary` creates and binds one glossary | `glossary.edit` | No-glossary empty-state workflow | No | Governance → Glossary | Covered |
| Delete glossary and terms | `POST /glossary/{guid}/delete` | `delete_glossary()` → `AtlasClient.delete_glossary()` | `DELETE /api/v1/models/{model_id}/glossary?confirm=true` | `glossary.edit` | Confirmed delete action | No | Governance → Glossary settings | Covered |
| Glossary detail and term list | `GET /glossary/{guid}` | `terms()` → `AtlasClient.get_glossary()` and `list_terms()` | `GET .../glossary/terms` | `glossary.read` | Responsive term cards with paging | No | Governance → Glossary detail | Covered |
| Filter and sort terms | `GET /glossary/{guid}?q=...` | Python in-memory case-insensitive filter in `terms()` | Authorized `query`, `sort`, `direction`, `offset`, and `limit` parameters | `glossary.read` | URL-backed search, sort, and paging | No | Governance → Glossary search | Covered |
| Import Atlas glossary CSV | `POST /glossary/{guid}/import` | `import_terms()`; temp file; `AtlasClient.import_csv()` | `POST .../glossary/import` validates model glossary identity and returns structured counts/errors | `glossary.edit` | Glossary CSV import action | No | Governance → Glossary import | Covered |
| New term form | `GET /glossary/{guid}/term/new` | `new_term()` renders Atlas glossary context | Uses glossary summary plus term create API | `glossary.edit` | Accessible term editor modal | No | Governance → Glossary term editor | Covered |
| Create glossary term | `POST /glossary/{guid}/term` | `create_term()` → `AtlasClient.create_term()` | `POST .../glossary/terms` | `glossary.edit` | New term workflow | No | Governance → Glossary term editor | Covered |
| View term details | `GET /term/{guid}` | `term()` → `AtlasClient.get_term()` and `assigned_entities()` | `GET .../glossary/terms/{term_id}` returns safe detail and authorized assignments | `glossary.read` | Deep-linkable term detail | No | Governance → Glossary term detail | Covered |
| Edit glossary term | `GET /term/{guid}/edit`; `POST /term/{guid}` | `edit_term()`, `update_term()` → `AtlasClient.update_term()` | `PATCH .../glossary/terms/{term_id}` | `glossary.edit` | Typed term editor | No | Governance → Glossary term editor | Covered |
| Delete glossary term | `POST /term/{guid}/delete` | `delete_term()` → `AtlasClient.delete_term()` | `DELETE .../glossary/terms/{term_id}` | `glossary.edit` | Confirmed term deletion | No | Governance → Glossary term detail | Covered |
| Show linked Atlas columns | `GET /term/{guid}` | `AtlasClient.assigned_entities()` | Term detail filters assignments through authorized graph visibility | `glossary.read` and authorized physical visibility | Term mappings with Canvas links | No | Governance term detail and Canvas Inspector | Covered |
| Link term to `database.table.column` | `POST /term/{guid}/assign` | `assign()`; `AtlasClient.find_column()` then `AtlasClient.assign()` | Authorized assignment endpoint accepts a Helios Canvas element ID and resolves Atlas server-side | `glossary.edit` plus `datasource.read` | Authorized asset picker | No | Governance term detail / Canvas Inspector | Covered |
| Unlink term from column | `POST /term/{guid}/unassign/{entity_guid}` | `unassign()` → `AtlasClient.unassign()` | Authorized assignment delete endpoint rechecks target visibility | `glossary.edit` plus authorized target visibility | Remove mapping action | No | Governance term detail / Canvas Inspector | Covered |
| List all filesystem runs | `GET /runs` | `runs()` → `runstore.list_runs()` | `GET /api/v1/models/{model_id}/runs` returns only model-associated runs with lifecycle, phases, counts, and actions | `model.read` | Models → Discovery & Activity lists authorized model runs | Pagination may be needed at scale; do not restore a global run list | Models → Discovery Runs | Covered |
| Run stage status and updated time | `GET /runs` | `runstore.list_runs()` checks `harvest.json`, `profile.json`, `propose.json` | Model runs return artifact-backed phases and nullable lifecycle values | `model.read` | Run cards show returned phases and unavailable evidence explicitly | Producer persistence is needed for live lifecycle values | Models → Discovery Runs | Covered for persisted evidence |
| Run harvest summary | `GET /runs/{run_id}` | `run_detail()` → `runstore.summary()` and `load("harvest")` | `GET /api/v1/models/{model_id}/runs/{run_id}` verifies model ownership and returns safe artifact-backed summary | `model.read` | Models → Run detail | No for current artifact summary | Models → Run detail | Covered |
| Run profile summary | `GET /runs/{run_id}` | `run_detail()` → `runstore.summary()` and `load("profile")` | `GET .../runs/{run_id}/profile` returns exact-run safe physical evidence | `model.read` plus `datasource.read` for physical data | Run detail automatically renders tables and all candidate groups | No | Models → Run detail; Data Sources → Profile | Covered |
| Tables, row counts, column counts, key candidates | `GET /runs/{run_id}` | Combines harvest tables and profile table entries | Exact-run profile summary returns harvested/profiled table summaries and key candidates | `model.read` plus `datasource.read` | Run detail lists tables with historical profile links | No | Data Sources → Profile / Run detail | Covered |
| Accepted profiled relationships | `GET /runs/{run_id}` | `profile.relationships` | Exact-run profile summary returns allowlisted accepted evidence | `model.read` plus `datasource.read` | Run detail accepted relationship table | No | Run detail / Canvas Physical lens | Covered |
| Suggested relationship candidates | `GET /runs/{run_id}` | `profile.suggested_relationships` | Exact-run profile summary returns allowlisted suggested evidence | `model.read` plus `datasource.read` | Run detail suggested relationship table | No | Models → Run detail / Review queue | Covered |
| Rejected relationship/data-quality candidates | `GET /runs/{run_id}` | `profile.rejected_candidates` | Exact-run profile summary returns allowlisted rejected evidence | `model.read` plus `datasource.read` | Run detail rejected candidate table | No | Data Sources → Data quality / Run detail | Covered |
| Per-table profile page | `GET /runs/{run_id}/table/{key}` | `run_table()` → harvest/profile files | `GET /api/v1/models/{model_id}/runs/{run_id}/profile/tables/{table_id}` returns the exact historical profile | `model.read` plus `datasource.read` | Models → historical table profile | No for stored table statistics | Data Sources → Dataset profile; Canvas Inspector | Covered |
| Column type/null rate/distinct/min/max | Same table profile page | `profile.tables[key].stats.columns` | Exact historical table profile includes statistics and exact/approximate distinct evidence | `model.read` plus `datasource.read` | Historical table profile | No | Canvas Inspector / Data Sources profile | Covered |
| Table glossary matches | Same table profile page | Harvest glossary terms mapped to qualified columns | Exact historical table profile derives matches from the same run's harvest artifact | `model.read` plus `datasource.read` | Historical table profile glossary column | No for legacy run parity; published glossary assignments remain separate | Canvas Inspector / Data Sources profile | Covered for run evidence |
| Read full proposal | `GET /runs/{run_id}/propose` | `run_propose()` → `runstore.load("propose")` | `GET /api/v1/models/{model_id}/runs/{run_id}/proposals` provides typed section paging/filtering, review state, provenance, and Canvas links | `model.edit` | Run detail proposal workspace and Canvas review | No raw-file endpoint is required | Canvas review and Models → Proposal summary | Covered |
| Proposal LLM provider/model/call count | Proposal and review pages | Values in `propose.json["llm"]` | None | `model.edit` | None | Add safe proposal provenance/audit fields to review summary | Models → Proposal summary | API gap |
| Proposal dataset and field semantics | Proposal page | Proposal datasets/fields | `GET .../graph?review_run_id=...` and graph detail | `model.edit` | Canvas review mode and Inspector | No for graph review; summary/paging may still be required for completeness | Canvas Semantic/Physical lenses | Covered |
| Proposal relationships, confidence, source, reason | Proposal page | Proposal relationships | Review graph/edge detail returns state, confidence, evidence, and metadata where available | `model.edit` | Canvas edge review and Inspector | Enrich detail only for any reason/evidence fields not yet represented | Canvas review | Covered/Partial |
| Proposal metrics | Proposal page | Proposal metrics | Review graph/detail represents metrics | `model.edit` | Canvas Semantic review | No for individual review; list-oriented review may need review-summary paging | Canvas review | Covered |
| Proposed glossary terms | Proposal page | Proposal glossary terms | Review graph maps terms to generic concept nodes | `model.edit` | Canvas review can select and decide them | Rich glossary proposal details may need a small DTO if graph detail is insufficient | Canvas review / Governance proposal queue | Covered/Partial |
| Review dashboard and per-section counts | `GET /runs/{run_id}/review` | `review_page()` → `review.summary()`, decisions, preflight | `GET /api/v1/models/{model_id}/reviews/{run_id}` returns counts, audit, preflight, validation, publication, and actions | `model.edit` | Run detail proposal workspace and Canvas review summary | No | Models → Review queue; Canvas review toolbar | Covered |
| Approve/reject one proposal | `POST /runs/{run_id}/review/decide` | `review.decide()` and `review.save()` | `POST /api/v1/models/{model_id}/reviews/{run_id}/decisions` | `model.edit` | Canvas Inspector Approve/Reject | No | Canvas review Inspector | Covered |
| Edit dataset proposal | Same decision route; inline editor | Overrides `name`, `kind`, `description` | Authorized decision endpoint accepts overrides | `model.edit` | Run detail uses typed dataset controls; Canvas retains its graph Inspector editor | No | Canvas dataset Inspector | Covered |
| Edit field semantic proposal | Same decision route; inline editor | Overrides `name`, `role`, `refers_to`, `description` | Authorized decision endpoint accepts overrides | `model.edit` | Generic JSON edit | No backend gap; typed role/refers-to editor recommended | Canvas attribute Inspector | Partial |
| Edit relationship proposal | Same decision route; inline editor | Overrides target table/column | Authorized decision endpoint accepts overrides | `model.edit` | Generic JSON edit | No backend gap; API should validate override schema before legacy retirement | Canvas relationship Inspector | Partial |
| Edit metric proposal | Same decision route; inline editor | Overrides name, expression, description | Authorized decision endpoint accepts overrides | `model.edit` / potentially `semantic.edit` in a future finer-grained policy | Generic JSON edit | Add typed validation/error DTO for expressions; mutation route already exists | Canvas metric Inspector | Partial |
| Edit glossary-term proposal | Same decision route; inline editor | Overrides name and definition | Authorized decision endpoint accepts overrides | `model.edit` / potentially `glossary.edit` in a future finer-grained policy | Generic JSON edit | No basic mutation gap; typed controls recommended | Canvas concept Inspector / Governance | Partial |
| Dataset cascade accept/reject including fields | `POST /runs/{run_id}/review/dataset` | `review.cascade_dataset()` | `POST .../reviews/{run_id}/decisions/dataset` | `model.edit` | Run detail and Canvas dataset actions reconcile returned summary | No | Canvas dataset Inspector | Covered |
| Bulk accept pending above confidence | `POST /runs/{run_id}/review/bulk` | `review.bulk_accept()` | `POST .../reviews/{run_id}/decisions/bulk` validates a 0–1 threshold | `model.edit` | Proposal workspace bulk action with result feedback | Dry-run preview remains optional follow-up | Models → Review queue / Canvas toolbar | Covered |
| Reset all or one review section | `POST /runs/{run_id}/review/reset` | `review.clear()` | `POST .../reviews/{run_id}/reset` | `model.edit` | Proposal workspace confirms and resets the selected section | No | Models → Review queue | Covered |
| Review audit (`reviewed_at`, `reviewed_by`, notes, overrides) | Review state and response data | `review.load/save()` | Graph detail and decision response expose available audit data | `model.edit` | Inspector displays returned audit metadata | Add review-summary GET for run-level audit; element audit is covered | Canvas Inspector / Review queue | Partial |
| Metric preflight errors before publish | Review page | `helios_core.ossie.preflight()` | None | `model.edit`; publish still requires `model.publish` | None | Include preflight and validation issues in review-summary/publish-readiness API | Models → Review queue / Overview action state | API gap |
| Publish reviewed Ossie model | `POST /runs/{run_id}/publish` | `review.apply()`, `ossie.build/preflight/validate`, `ArtifactStore.write_published_ossie()` | `POST /api/v1/models/{model_id}/reviews/{run_id}/publish` returns manifest or structured validation failure | `model.publish` | Overview and proposal workspace publish through the API with feedback | Version/audit enrichment and idempotency semantics remain follow-ups | Overview and Models → Review queue | Covered |
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

Completed:

1. Model-scoped glossary and term read APIs expose only the glossary bound to
   the selected model.
2. Glossary/term create, update, delete, import, assignment, and unassignment
   APIs enforce `glossary.edit`.
3. Physical assignments are filtered and mutated only after datasource and
   authorized-graph checks.
4. Governance → Glossary provides published-term management, proposed-term
   review, deep-linkable details, and Canvas focus links.

The legacy routes remain present during the final retirement validation; the
React application has no dependency on them.

**Glossary retirement status:** ready for removal after deployment smoke
testing against the configured Atlas instance. Automated parity coverage now
protects model scoping, authorization, term CRUD, import validation,
assignment filtering, proposal review, and Canvas links. This status applies
only to `/glossary*` and `/term*`; the broader Console retirement gate still
depends on the remaining non-glossary rows in this document.

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
