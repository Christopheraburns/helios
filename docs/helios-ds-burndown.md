# Helios-DS-Generation Burn-Down

Sequenced task list for building the Helios-DS asset generator: the mechanisms to **create, review, edit and approve** synthetic assets.
Source: *Helios-DS Asset Generator — Engineering Development Specification* (the "spec").

**How to use:** work top to bottom. A task is done only when its **Done when** check passes; the spec requires objective readiness gates, not "basically works". Check the box and add the commit hash.

**Scope:** `apps/helios-ds-generation` (the generator and its review workflow). The Helios crawler, analyzers, LanceDB/Memgraph projections and the evaluator are separate projects; the items this project must hand off to them are listed in [Handoffs](#handoffs-to-other-projects).

**Scope revised 2026-09-30.** Helios is a semantic layer over structured *and* unstructured data, served through MCP to people and agents. Helios-DS-Generation exists to produce one realistic, TPC-DS-consistent corpus with a hidden answer key, so the Helios crawler and new structured-plus-unstructured indexing methods can be developed and measured. It may run only a handful of times. The remaining work is therefore cut to what the crawler needs:
- **Text first.** The crawler is built and tuned on PDF, email and chat. Images (Phase 5) come only once the text crawler meets its targets; audio and video (Phase 6) after that, if at all.
- **Kept:** lean aliases (C-05), golden questions (C-08), a frozen development corpus (C-09), ground-truth isolation (S-05) and version pinning (Q-03).
- **Cut:** curation overlays (stage C), the generator's own MCP interface and roles (S-01 to S-04), the standalone validation Job (C-07) and the Workbench release CI (Q-02). A corpus that is generated once is fixed by editing templates and regenerating.
- **Deferred:** simulated users and access lists (C-06), until the crawler handles permissions. Generation is deterministic and takes about 30 s, so adding them later just means regenerating.

Cut items stay listed, struck through, with the reason, so the spec's full scope remains traceable.

**Status at creation (2026-09-29):** scaffold only. `helios_ds/config.py` (partial config schema), `ids.py` (UUIDv5 helpers), `scenarios.py` (planner skeleton), `schemas.py` (record models), a FastAPI app whose endpoints return placeholders and keep jobs in an in-memory dict, and a React UI with submit/monitor/results pages. No generators, storage, lakehouse writes or tests exist yet.

---

## Design decision: how review, edit and approve fit a deterministic generator

**Decided 2026-09-30: see [ADR 0001](adr/0001-helios-ds-review-edit-approve.md).** In short:
- **Immutable datasets:** a published dataset is never modified; every correction produces a new dataset (a new `dataset_id`).
- **Stage B now, "reject and regenerate":** review with advisory flags and comments; approve or reject in the UI; fix templates, phrase banks or config in Git; regenerate.
- ~~**Stage C later, "curation overlays":**~~ *(Cut 2026-09-30; see the scope revision.)* all four kinds (exclude, presentation override, metadata change, pinned replacement), stored in `helios_ds.overlays`. The overlay bundle hash joins the dataset identity, and an edited dataset records its parent.
- **Superseding:** approving a dataset moves the other `READY` datasets in its **lineage** (same generation config, plus overlay-derived datasets) to `SUPERSEDED`, so one dataset per lineage is live.
- **Advisory flags:** review flags never block approval; failed validation still does.
- **Approver:** any authenticated Workbench user approves (S-04 roles are cut). The identity comes from the Application proxy, verified in R-06.

---

## Phase 0: Stabilise the existing scaffold

*Status 2026-09-29 (uncommitted):* P0-01 to P0-04 done: 41 tests pass (`pytest apps/helios-ds-generation/tests`), plus ruff and mypy. P0-05's workflow file is written and its Python steps pass in a clean venv, and a PR on GitHub has since run it green, so P0-05 is done too. Notes: the ID derivation changed (every ID below the dataset is now namespaced by the dataset, per the spec) and the golden values are pinned in `tests/deterministic/test_ids.py`. The `balanced` difficulty profile is renamed `mixed` to match the spec's request example. The old `/api/*` routes are removed. Known gap: `pyproject.toml`'s `helios-core @ {path=...}` dependency isn't valid PEP 508, so `pip install -e` fails; CI installs dependencies directly until F-11.

- [x] **P0-01** Add `tests/` (unit, deterministic, integration, benchmark) and `fixtures/tiny/` per spec layout; wire pytest to the `src` layout. *Done when:* `pytest apps/helios-ds-generation/tests` runs green in Workbench.
- [x] **P0-02** Fix determinism gaps in `ids.py`: namespace `truth_entity_id` and `claim_id` by dataset, and add `artifact_seed`/`scenario_seed` derivation exactly as in the spec (SHA-256 chain). *Done when:* fixed fixtures produce hard-coded expected IDs.
- [x] **P0-03** Harden `config.py`: auto-validate difficulty weights, validate or normalise scenario weights, reject unknown fields (`extra="forbid"`), and add canonical JSON serialisation plus `config_hash`. *Done when:* equivalent configs hash identically and unknown incompatible fields fail with a clear error.
- [x] **P0-04** Replace the placeholder routers with stubs matching the spec's REST contract (`/v1/generations`, `/v1/jobs/{id}`, `/v1/datasets/...`); keep the UI working against them. *Done when:* the UI round-trips against the `/v1` stubs.
- [x] **P0-05** Add the GitHub Actions PR workflow `helios-ds-gen.yml`: ruff, mypy, unit tests. *Done when:* a PR shows the checks.

## Phase 1: Foundation (spec "Foundation milestone")

*Status 2026-09-29:* **phase 1 is complete.** The exit gate (F-12) passed in Workbench against the real Impala lakehouse and S3 prefix, and locally at SF1 across two fresh environments. 112 tests pass, plus ruff and mypy. Runtime images are registered (F-11); from now on, generator Jobs and Applications should use `helios-ds-runtime`.

Decisions made while building phase 1:
- **Lakehouse placement and access (decision 2026-09-29):** `helios_ds` and `helios_ground_truth` live in **the same lakehouse as `tpcds`**, as new databases in the same Metastore. The Helios project has no direct access to AWS services, so every read and write goes through Cloudera services: the Impala Virtual Warehouse for `tpcds` and both new databases (`ImpalaTpcds`, `SqlLakehouseSink`), and the Workbench `S3 Object Store` data connection for generated artifacts (in `s3a://applied-ai-buk-d5eff1ab/helios-db/source`). The eligibility SQL is plain SQL that runs unchanged on Impala and on DuckDB (local). PyIceberg is used only by offline tests and CI (SQL catalog), and the earlier PyIceberg Hive/`iceberg:tpcds` path is removed. Ground-truth isolation from the crawler relies on a Ranger policy (S-05).
- **Extra tables:** `helios_ds.scenario_plans`, `helios_ds.dataset_lifecycle` (an append-only state log; the current state is the latest event) and `helios_ds.generation_runs` are added to the spec's list. Every `helios_ds` and ground-truth row carries `dataset_id`. JSON-valued fields are stored as canonical JSON strings so Impala can read them.
- **Dataset identity:** `dataset_id` = UUIDv5 over (config hash, template bundle hash, source fingerprint hash, generator version, ID schema version). The API's job record now reports `config_hash`; `dataset_id` is set once planning runs.
- **Allocation:** each artifact type's target is split across the scenario types that can produce it, by weight, using largest-remainder rounding. Shortfalls (too few eligible rows, or no scenario that produces a type) are reported in the manifest, never hidden.
- **Crawler boundary:** the manifest holds the scenario plans, so it's stored under `_manifests/` in the object store; crawlable artifacts go under `datasets/`.
- **Scenarios:** `customer_complaint` (web returns with complaint reasons) exists alongside the three spec scenarios but isn't in the default config.

No media generation merges until this phase's exit criterion passes.

- [x] **F-01** *(Done 2026-09-29: `helios_ds` and `helios_ground_truth` created in the tpcds lakehouse from `schemas/lakehouse_impala.sql`.)* Lakehouse namespaces: create `helios_ds.*` and `helios_ground_truth.*` Iceberg tables (datasets, generation_runs, artifacts, artifact_sources, template_versions, source_principals, source_acl_bindings; entities, entity_mentions, relationships, claims, evidence, expected_queries, expected_results). *Done when:* DDL is in Git and the tables exist in the Workbench Hive Metastore.
- [x] **F-02** *(Done 2026-09-29: the SQL sink runs against the Impala Virtual Warehouse in Workbench (inserts, reads and lifecycle events verified); the PyIceberg SQL-catalog sink covers offline tests and CI.)* `LakehouseSink` abstraction over PyIceberg: Hive Metastore catalog in Workbench, PostgreSQL SQL catalog for offline CI. *Done when:* the same test suite passes against both catalogs.
- [x] **F-03** *(`ImpalaTpcds` verified against the real Virtual Warehouse: same eligible-row counts as DuckDB SF1, 7,666 / 290 / 55,714.)* TPC-DS repository adapter (Impala/Iceberg) with a local fixture adapter. *Done when:* the same fixture maps to identical canonical records and shuffled input order yields the same output.
- [x] **F-04** Source snapshot fingerprint: Iceberg snapshot IDs where available, plus table/schema metadata and aggregate hashes. *Done when:* the fingerprint is stable across runs and changes when a source table changes.
- [x] **F-05** Deterministic scenario planner: SHA-256 candidate ranking, top-N selection, per-scenario and per-artifact seeds, local `random.Random` only, and a lint or test that forbids module-level `random` calls. *Done when:* three clean runs give identical scenario IDs and plans, and shuffled source input gives the same plan.
- [x] **F-06** Scenario definitions for `product_return_damage`, `warehouse_inventory_issue` and `promotion_performance`, mapping TPC-DS sources to artifact plans (spec baseline mapping table). *Done when:* each type produces a plan from SF1.
- [x] **F-07** Template registry: `templates/<type>/<id>/template.yaml` exposing every field the spec requires (id, version, schema_version, supported scenarios, required/optional fields, rendering params, locator strategy, content hash); the bundle hash feeds `dataset_id`. *Done when:* changing any template byte changes `dataset_id`.
- [x] **F-08** *(Decision 2026-09-29: assets live in `s3a://applied-ai-buk-d5eff1ab/helios-db/source`, reached through the Workbench data connection `S3 Object Store` (boto3 via Ranger/RAZ, no keys in the project). Verified against the real bucket with a probe object: write, idempotent re-write, rejected mismatch, delete.)* Object-store abstraction (S3-compatible over the Cloudera S3/ADLS/Ozone location, project filesystem for development) with write-if-hash-matches semantics. *Done when:* re-writing the same artifact is a no-op, and a hash mismatch raises a determinism-integrity error without overwriting.
- [x] **F-09** Public manifest writer: canonical JSON manifest plus `helios_ds.datasets`/`artifacts`/`artifact_sources` rows. *Done when:* manifests are byte-identical for equivalent runs.
- [x] **F-10** Dataset lifecycle state machine: `CREATING → VALIDATING → IN_REVIEW → READY | FAILED | REJECTED`. *Done when:* a dataset cannot reach `READY` with incomplete files or tables, and a failed run is never visible as `READY`.
- [x] **F-11** *(Built and pushed 2026-09-29 as `docker.io/christopheraburns/*:0.1.0`. Digests: `helios-ds-media-runtime` = `sha256:a7b5a1b0ef99142b7638d3182ee0caea04baa5667a57721499d4fab55f3bd72a`; `helios-ds-runtime` = `sha256:45e299437f05a81496310d8deae52abab83bef49fee4aeeeee5546082767f1c5`. Both registered in the Workbench Runtime Catalog and available in the project, 2026-09-29.)* Runtime images: `helios-ds-runtime` (pinned Python and lockfile, ReportLab, Pillow, PyIceberg, FastAPI, MCP SDK, Celery, fonts) and `helios-ds-media-runtime` (plus pinned FFmpeg and eSpeak NG), built on a Cloudera ML Runtime base and registered in Workbench. *Done when:* both images appear in the project runtime catalog and their digests are recorded.
- [x] **F-12** *(Passed in Workbench 2026-09-29 with `impala:tpcds` + `--lakehouse impala` + `s3a://applied-ai-buk-d5eff1ab/helios-db/source`: repeat runs gave the same dataset `a890dbea-196b-5df6-bc93-5764199b487a` and manifest SHA-256 `9b3f5914b327…`. The lakehouse holds exactly one `datasets` row, 667 unique scenario plans (313 return, 187 promotion, 167 warehouse), 15 template versions, and 1,750 planned artifacts with every target met. The first attempt exposed a validation bug with prefixed S3 keys, since fixed with regression tests; the retry reused that attempt's rows without duplicating them. Also passed locally at SF1 across two fresh environments.)* **Exit gate:** TPC-DS SF1 → planner → deterministic plan → Iceberg generation manifest, run twice from clean state. *Done when:* the plans and manifests are identical.

## Phase 2: Asynchronous job service

*Decision 2026-09-29:* the environment can't host PostgreSQL or Redis outside Workbench, so the spec's PostgreSQL + Celery + Redis job layer is replaced by **job state in the `helios_ds` lakehouse (through Impala) plus Workbench Jobs as the scheduler**. Measured costs: Impala reads about 0.1–0.15 s and writes about 1.5–2 s, so jobs record events only at coarse milestones, never per artifact. Iceberg has no row locks, so the job state is an append-only event log in which the first terminal event wins. The spec's gates for the job layer still apply and are met: status survives an API restart, retries create no duplicates, and failures record an actionable cause.

*Status 2026-09-29 (uncommitted):* **phase 2 is complete** (J-01 to J-05). 136 tests pass, plus ruff and mypy, in a clean venv with CI's dependencies. **Verified end to end in Workbench:** `POST /v1/generations` recorded job `job_28285dd8…` in the lakehouse and started Workbench run `p4na2z3x4oahrlfa` of `helios-ds-generate` on `helios-ds-runtime`. The worker reproduced dataset `a890dbea…` byte-for-byte (so the new runtime image reproduces F-12) and recorded QUEUED → RUNNING → SUCCEEDED; the run took about 32 s, 16 s of it work.

- [x] **J-01** Job state in the lakehouse: `helios_ds.generation_jobs` (request, config hash, idempotency key) and `helios_ds.job_events` (append-only; first terminal event wins); `helios_ds.jobs.JobStore`. *Done when:* job status survives an API restart. ✔ (tested; the tables were created in the lakehouse through Impala)
- [x] **J-02** Execution through Workbench Jobs: the API starts a run of the `helios-ds-generate` Job through the Cloudera AI API (`WorkbenchJobsDispatcher`, authenticated with `HELIOS_DS_API_KEY`), passing `HELIOS_DS_JOB_ID`. An `InlineDispatcher` covers local development. *Done when:* a submitted job runs on the right runtime. ✔ (verified in Workbench)
- [x] **J-03** `POST /v1/generations` returns 202 with a job handle; `Idempotency-Key` returns the existing job (409 if the key was used for a different request); `:cancel` stops the Workbench run and records CANCELLED, and workers also check for cancellation at checkpoints. *Done when:* the idempotency and cancellation contract tests pass. ✔ (live cancel of a real run not yet exercised)
- [x] **J-04** Idempotent work: re-running a job is safe (deterministic IDs, write-if-hash-matches objects, publish-once rows), and a finished job is left alone. *Done when:* a forced worker retry creates no duplicate logical artifacts, and failed tasks record an actionable cause. ✔ (tested; per-artifact tasks arrive with phase 3)
- [x] **J-05** Workbench deployment: the `helios-ds-generate` Job (created by `workbench/setup_jobs.py`: helios-ds-runtime, 2 vCPU / 4 GiB) plus the API/UI Application. *Done when:* the Application serves `/v1` on helios-ds-runtime with jobs dispatched to Workbench. ✔ Verified 2026-09-29: submitted from the dashboard, job `job_35fc2844…` ran as Workbench run `73a28ij9qxefirjk` and SUCCEEDED. A media Job on `helios-ds-media-runtime` is added in phase 6.

*Added 2026-09-29 (between phases 2 and 3, uncommitted):* a **manifest viewer** in the dashboard (Datasets & Manifests tab, plus **View manifest** on jobs), backed by new `/v1/datasets…` endpoints. It is groundwork for the phase 4 review screens (R-03/R-04). Verified on real data: dataset `a890dbea…`, 667 scenarios; about 2 s for the first manifest load from S3, about 0.35 s once cached. Fix: the first deployment returned 500s or hung because the API shared one Impala connection across its request threads. The SQL sink and `ImpalaTpcds` now use one connection per thread, with a regression test; 15 concurrent viewer requests against the real lakehouse all returned 200.

## Phase 3: Create — textual artifact slice (spec "Textual-artifact milestone")

First business story: Sale → Return → Complaint email → Return authorisation PDF → Internal support chat.

*Status 2026-09-30 (uncommitted):* **C-01 to C-04 done.** Assets are generated without any ML model. Templates supply the structure, TPC-DS facts supply the content, and seeded phrase banks supply the wording variation. Values shared per case (RMA number, case number, staff, timeline) derive from the scenario seed, so a story's report, email and chat agree. Every placed entity mention is recorded with an exact locator, ready for C-04. Renderers live in the template directories (`renderer.py` + `phrases.yaml`), so the template content hash covers them. Templates are now v1.1.0 and the generator 0.2.0. Rendering runs while the dataset is CREATING, so a dataset reaches IN_REVIEW only with its artifacts. Planned types without a renderer are reported as pending. **Verified in Workbench:** job `job_68177722…` rendered 60 artifacts (20 PDF, 20 email, 20 chat) for dataset `97a4edf9…` in about 30 s. A second job re-rendered all 60 byte-identically on the runtime image, and all 60 are served from `s3a://…/helios-db/source/datasets/…/artifacts/` with matching SHA-256. 164 tests pass. The dashboard previews rendered files in place: a manifest viewer **Artifacts** tab plus **Preview** in each story's detail, with PDFs shown inline, emails as formatted messages (via `GET /v1/artifacts/{id}/preview`) and chats as threads. The submit form takes scenario weights. **Follow-up:** the registered runtime image took `reportlab>=4.0` at build time; the requirements now pin `reportlab==5.0.1` and `pypdf==6.19.0`, so rebuild the runtime (0.1.1) to lock PDF bytes to that version.

- [x] **C-01** PDF generator (ReportLab): return report and return-authorisation templates; deterministic or stripped PDF metadata; selectable-text and scanned-style pages. *Done when:* 100% of PDFs parse, truth locators resolve, and hashes are stable in the pinned image.
- [x] **C-02** Email generator (`EmailMessage`): deterministic Message-IDs, MIME boundaries, dates and attachment names; thread provenance. *Done when:* every `.eml` parses, headers match truth, and hashes are stable.
- [x] **C-03** Chat generator: the neutral Helios-DS JSON/JSONL schema. *Done when:* output is 100% schema-valid, thread and message references are valid, and hashes are stable.
- [x] **C-04** *(Done 2026-09-30. Renderers tag evidence passages as well as mentions. Every mention and evidence locator is verified against the rendered bytes before anything is written, so the job fails otherwise. Rows get deterministic IDs and are written once per dataset, or fully rewritten if a partial set exists. Validation rejects orphans and claims without evidence. The exit gate now requires identical ground truth across fresh environments. Tables gained columns through an additive migration (`init-tables` runs `ALTER TABLE ... ADD COLUMNS`), applied to the Impala lakehouse. **Verified in Workbench:** job `job_ca2f5a5c…` produced dataset `1cd9f258…`: 60 artifacts, 167 entities, 496 mentions, 561 relationships, 80 claims (4 per story) and 157 evidence rows, with all locators, PDFs included, verified on the runtime image.)* Ground-truth builder for the slice: entities, mentions with precise locators, relationships, claims (including `truth_status`) and evidence. *Done when:* there are zero orphan rows and all evidence locators resolve.
- [x] **C-05** *(Rescoped 2026-09-30: lean. Built 2026-09-30, pending a Workbench run. `helios_ground_truth.entity_mentions` gained a `difficulty` column (direct, alias or contextual). Aliases: item colour and class ("yellow kids item"), city store ("Midway store"), salutation and surname ("Mrs. Beatty"), customer email, "receipt ending in 1892", and the support case number. Contextual: "the item", "the product", "that store", "the customer", "this return". Every email and chat carries all three tiers, and the PDF has alias cells (support case, customer email). The scenario query adds `i_color` and `c_salutation`. Templates are now 1.2.0 and the generator 0.3.0, so this is a new dataset. The review screen shows each mention's tier. The config's difficulty weights and profile are left in place but unused.)* Alias mentions: phrase banks refer to entities indirectly as well as by canonical name (e.g. "the blue kettle", "your Sacramento store", "order ending 4471"), still derived from TPC-DS facts and the scenario seed. Each mention's `surface_form` and a difficulty tier (`direct`, `alias`, `contextual`) are recorded in the ground truth. No config-driven tier weights. *Done when:* every story's artifacts contain alias and contextual mentions whose locators resolve to the canonical entity, and all three tiers appear in the corpus.
- [ ] **C-06** *(Deferred 2026-09-30, until the crawler handles permissions.)* Security simulation: source principals (alice, bob, carol, dave, erin, public_agent), groups, and `source_acl_bindings` per artifact. *Done when:* every artifact has an ACL policy and the bindings resolve.
- ~~**C-07** Validation job (`helios-ds-validate`)~~ *(Cut 2026-09-30: validation already runs in every generation and again at approval.)* **Kept part, done 2026-09-30:** approval re-reads every artifact (8 in parallel) and checks it is at its recorded locator with its recorded SHA-256 and size. A missing or altered artifact blocks approval with a count of each problem. Generation skips this re-read because every write is already verified.
- [x] **C-10** *(Added and done 2026-09-30.)* Plan only what can be rendered. The planner now gives an artifact type only to story types whose template has a renderer, so the requested counts all go to stories that produce files, and "planned" equals "rendered". Unrenderable requests still show in the manifest's counts (e.g. `image: target 4, planned 0`). This fixes the "147 of 300" result, where promotion and warehouse stories took shares they couldn't render and 21 return stories got only an email. Datasets made before generator 0.3.1 keep their mixed plans. *Done when:* a 100/100/100 request plans and renders 300 return artifacts, with every story getting all three.

- [x] **C-08** *(Rescoped 2026-09-30. Done 2026-10-01: verified in Workbench, where dataset `1ca99f86…` was generated with 60 questions, 10 of each kind. `helios_ds/golden.py` generates the questions right after the ground truth. `expected_queries` gained `kind`, `difficulty` and `required_evidence` columns. Questions and answers are checked in validation (every cited entity, claim, evidence row and artifact must exist; one answer per question) and included in the two-environment exit gate. Structured answers store their SQL, rows and a hash; tests re-run the SQL against TPC-DS. A "why" is answered with the supporting claim (refund approved *because* the packaging was damaged). Re-running a published dataset only compares its questions: none are added to, or changed in, a dataset that has left CREATING. On a 30-story dataset: 10 structured, 10 unstructured, 8 resolution, 10 joined, 5 cross-document, 10 no-answer. Shown in the manifest viewer's **Golden questions** tab, with kind filter, answer, SQL, claims, and evidence passages with artifact preview.)* Golden questions generated **with the dataset**: after the ground truth and before `IN_REVIEW`, generation writes `expected_queries`/`expected_results`, deterministic from the manifest, ground truth and TPC-DS (answers are computed, never LLM-written). Six kinds:
  1. structured (answer from a TPC-DS row, stored with its SQL);
  2. unstructured (a claim and its evidence passages);
  3. entity resolution (asked by an alias surface form that is unique in the corpus, tagged with its tier);
  4. joined structured plus unstructured;
  5. cross-document (a set built from claims across artifacts);
  6. no-answer (a real TPC-DS entity with no documents; the correct response is to abstain).

  About 10 per kind, capped by what the dataset supports, with a few seeded wordings per template. Asked as a single `evaluator` principal until C-06. Validation requires every referenced entity, claim, evidence row and artifact to exist. Shown read-only in a **Golden questions** tab. *Done when:* every question's answer and evidence resolve against the ground truth, all six kinds appear in a return-story dataset, and two fresh environments generate identical questions.

- ~~**C-11** Curate golden questions during review, freeze at approval~~ *(Cut 2026-10-01: the generated questions are good enough. Approval freezes them exactly as generated, since nothing can change them after generation. The plan is kept below for reference.)* While the dataset is `IN_REVIEW`, reviewers can accept, drop or reword generated questions, and add their own by picking claims, passages and entities from the ground truth. Edits are an append-only log next to the review marks; generated rows are never overwritten. Every edit is validated against the truth tables. Approval is blocked while any question is invalid; it writes the final question set and records its hash on the approval event. After `READY`, questions are fixed and editing closes. *Done when:*
  - an edited and an added question survive approval;
  - an invalid question blocks approval;
  - the frozen set's hash is in the audit trail;
  - no edits are accepted after approval.

- [x] **C-09** *(Done 2026-10-01.)* **Development corpus: `1ca99f86-e6a0-57fc-8311-cadcac4c8302`**. Approved by cburns at 2026-10-01T11:22:26Z.
  - **Source:** generator 0.3.1; config hash `ef3a821b0ec5…`; template bundle `9805c67fd32b…`; manifest SHA-256 `cc61bcd6bf11…`.
  - **Contents:** 101 damaged-product return stories, 301 artifacts (100 PDF, 101 email, 100 chat).
  - **Ground truth:** 794 entities, 3,135 mentions (2,131 direct, 698 alias, 306 contextual), 2,846 relationships, 403 claims, 785 evidence passages.
  - **Golden questions:** 60, ten of each kind. Question-set SHA-256 `ded20c2aad77…`, computed over the canonical JSON of the sorted question rows.

  Original text: Freeze the development corpus: choose text counts (PDF, email, chat), generate with C-05 and C-08, review and curate the questions (C-11), approve it in the dashboard, and record its `dataset_id` and question-set hash as the crawler's development corpus. *Done when:* the corpus is `READY` and its ID, counts and golden-question count are recorded here.

- [ ] **C-12** *(Added 2026-10-01, for the crawler's overfitting checks; crawler-analysis.md decision 4.)* Held-out corpus:
  - **Now:** a second, smaller dataset (e.g. 30 stories) generated with a different master seed, so it has different customers, items, stores and phrase choices. Review and approve it, then record its ID next to the development corpus.
  - **Then:** a variant with alternate phrase banks (new template versions with different wording), so the crawler's claim cue lexicons are tested against wording they were not written from.

  *Done when:* the held-out dataset is READY and recorded, and the crawler is scored on both.

## Phase 4: Review, edit and approve

Delivered in two stages (ADR 0001).

### Stage B: review, then approve, or reject and regenerate

- [x] **R-01** Decide the review/edit model and record it as an ADR. ✔ [ADR 0001](adr/0001-helios-ds-review-edit-approve.md), accepted 2026-09-30.
- [x] **R-03** *(Done 2026-09-30. Endpoints: `GET /v1/review/datasets/{id}` (counts and per-artifact status), `GET /v1/review/artifacts/{id}` (preview, story and facts, mentions with canonical entities, claims with evidence, relationships, marks), `POST /v1/review/artifacts/{id}/marks` and `GET /v1/whoami`. Marks are append-only in `helios_ds.review_marks`; the latest ACCEPTED or FLAGGED mark wins. Marks are refused (401) without an authenticated user. Verified read-only on dataset `1cd9f258…`, about 3 s per artifact bundle.)* Review APIs: list datasets `IN_REVIEW`; get an artifact with its native preview, manifest context, TPC-DS source rows and ground truth; record per-artifact review marks (accepted, flagged, comment) in the lakehouse. *Done when:* the contract tests pass. (The manifest viewer and artifact previews already cover part of this.)
- [x] **R-04** *(Done 2026-09-30: the dashboard **Review** tab. It has a dataset picker, progress bar, filterable artifact list and the artifact beside its ground truth. Mentions are highlighted and evidence is underlined in email and chat text; clicking an entity finds all its mentions. Accept, flag and comment, with the keyboard shortcuts a, n and p; Accept moves to the next unreviewed artifact. Confirmed in the Application: `/v1/whoami` shows the signed-in Workbench user.)* Review UI: per-artifact viewer side by side with its ground truth and source rows; accept, flag and comment; a dataset review summary (reviewed, flagged, accepted counts). Flags are advisory. *Done when:* a reviewer can walk the textual slice end to end.
- [x] **R-06** *(Done 2026-09-30, pending a Workbench run. `approve()` re-validates, records the approver and an optional note, then supersedes every other `READY` dataset with the same `config_hash`; each `SUPERSEDED` event names the approving dataset in the new `dataset_lifecycle.related_dataset_id` column. `reject()` needs a reason and is terminal. Endpoints: `POST /v1/review/datasets/{id}:approve` and `:reject`, 401 without an authenticated user, 409 with the validation problems when re-validation fails; flags never block. Review marks close (409) once a dataset is decided. The approval re-validates against the store the dataset was written to (from its manifest locator), so the Application doesn't need `HELIOS_DS_OBJECT_STORE`. The Review tab has a Decision panel showing the lineage and what approval will supersede.)* Approve and reject in the UI: first verify how the Workbench Application proxy passes the authenticated user, and refuse approval if it doesn't. Record the approver on the lifecycle event. Add a `reject()` operation and the `SUPERSEDED` state; approval supersedes other `READY` datasets in the lineage (same `config_hash`). *Done when:* only one dataset per lineage is `READY`, approval is blocked while validation fails (not by flags), and the approver is recorded.
- [x] **R-07** *(Done 2026-09-30, pending a Workbench run. Reconstructed from the two append-only logs, with no new table: `GET /v1/review/datasets/{id}/history` returns lifecycle events, review marks and the superseding events this dataset's approval caused, oldest first. Approval and rejection events carry the review counts as they stood at that moment. Shown under **History** on the Review tab and in the manifest viewer.)* Audit trail: every review mark, approval, rejection and superseding event is persisted and queryable. *Done when:* the history of an approved dataset can be reconstructed from the events.

- [x] **R-09** *(Added on request, done 2026-09-30. **Known issue (2026-10-01):** in Workbench, deletion stops at the first object-store step because the RAZ policy for the S3 connection denies `DeleteObject` on `helios-db/source`. `1cd9f258…`, `97a4edf9…` and `a890dbea…` are marked DELETED (hidden from the crawler) but their objects and rows remain. After the policy is fixed, clicking Delete again finishes each one.)* Delete datasets that are no longer needed: `POST /v1/datasets/{id}:delete` and **Delete dataset** in Datasets & Manifests, with a type-to-confirm dialog. It removes the objects and every dataset-keyed row except the lifecycle log, run history and job history, then records a `DELETED` event (actor and reason). `DELETED → CREATING` lets the same config regenerate. It is refused while `CREATING` or `VALIDATING` and retryable if interrupted. *Done when:* the objects and rows are gone and the history is kept.

### Stage C: curation overlays *(cut 2026-09-30)*

For a corpus generated once or twice, fixing a template and regenerating replaces per-artifact edits.

- ~~**R-02** Overlay schema and storage~~ *(Cut.)*
- ~~**R-05** Edit workflow~~ *(Cut.)*
- ~~**R-08** Diff view~~ *(Cut.)*

## Phase 5: Create — visual slice *(deferred: starts once the text crawler meets its targets)*

- [ ] **V-01** Image generator (Pillow, pinned fonts): product packaging, labels, receipts, shelves, synthetic damage. *Done when:* dimensions and format are correct and pixel/file hashes are stable.
- [ ] **V-02** Region-level ground truth (bounding boxes) and the difficulty classes easy, alias, OCR-only, region and context. *Done when:* all regions lie within image bounds and each class is represented.
- [ ] **V-03** Extend the review screen (R-04) to images, with bounding-box overlays in the viewer. *Done when:* a reviewer can inspect and flag regions.

## Phase 6: Create — audio and video slice *(deferred, possibly indefinitely; after images)*

- [ ] **A-01** Audio generator: eSpeak NG → deterministic WAV, scripted speaker turns, time-span truth. *Done when:* audio decodes, spans fit the duration, hashes are stable, and reference ASR recovers the required facts above threshold.
- [ ] **A-02** Video generator: procedural frames + TTS track + FFmpeg with pinned codec, frame rate, pixel format, threads and metadata; scene/keyframe/timecode truth (e.g. the warehouse-inspection storyboard). *Done when:* ffprobe validation passes, spans fit the duration, and hashes are stable on the reference platform.
- [ ] **A-03** Extend the review screen to audio and video, with a timeline scrubber showing truth intervals and transcript. *Done when:* a reviewer can jump to cited intervals.
- [ ] **A-04** Contradictory-evidence scenario (e.g. "resolved Aug 12" vs. later damaged-package evidence) using the full `truth_status` set. A text-only version (conflicting email and chat) can be pulled into Phase 3 if the crawler's methods need contradiction handling. *Done when:* the conflict set and expected conclusion are stored as a golden query.

## Phase 7: Agent interface and security

The generator is driven from its dashboard; agents query Helios, not the generator. Only ground-truth isolation remains.

- ~~**S-01** MCP facade over the job service~~ *(Cut.)*
- ~~**S-02** MCP Tasks for long-running generation~~ *(Cut.)*
- ~~**S-03** Review/approve MCP tools~~ *(Cut.)*
- ~~**S-04** Keycloak/OIDC roles~~ *(Cut: any authenticated Workbench user of the generation project may review and approve.)*
- [x] **S-05** *(Done 2026-10-01 as crawler task CR-0d. The crawler's identity `srv_helios_crawler` is denied `helios_ground_truth` and the `helios_ds` base tables, and can read only the `helios_ds.crawlable_artifacts` view (READY datasets, neutral columns). Verified by `python -m apps.helios.crawler.access_check`. S3 manifests are not yet RAZ-isolated, because data connections run as the session user; see CR-0d.)* Ground-truth isolation: a Ranger policy on `helios_ground_truth`, and crawler and query credentials verified to lack access. Needed before the crawler's first evaluation. *Done when:* a negative access test from the crawler project fails as expected.

## Phase 8: Reproducibility

- [x] **Q-01** *(Covered 2026-09-30 by the P0-05 workflow: ruff, mypy, the unit and deterministic suites (tiny-corpus generation, pinned IDs and golden hashes, the F-12 exit gate) and the UI build. Schema-compatibility and MCP contract tests are dropped with S-01.)* PR CI.
- ~~**Q-02** Merge-to-main CI with clean-state Workbench Jobs~~ *(Cut: runtime images are built by hand with `build.sh` when dependencies change.)*
- [ ] **Q-03** Pin and record what the text corpus depends on (Python, lockfile, runtime image digest, ReportLab and pypdf versions, fonts, template bundle, manifest schema, source fingerprint, generator version, architecture) in every dataset record. Includes rebuilding the runtime as 0.1.1 with the pinned `reportlab` and `pypdf`. FFmpeg and eSpeak NG join when Phase 6 does. *Done when:* the frozen corpus (C-09) records all of them.
- [x] **Q-04** *(Covered: `tests/deterministic/test_foundation_exit_gate.py` generates in two fresh environments and compares `dataset_id`, artifact IDs, manifests, ground truth and artifact hashes on every PR. Not a separate release gate.)* Clean-room reproducibility test.
- ~~**Q-05** Scale-up corpus~~ *(Replaced by C-09, text only.)*

---

## Handoffs to other projects

**Next project: the crawler.** After C-09 and S-05, work moves to the Helios crawler and text analyzers (the planned `POST /v1/datasets/{id}:crawl` hook, `helios_index.*`, and the LanceDB and Memgraph projections), with its own burn-down.

Items the generator must support but does not own:

| Consumer | Needs from Helios-DS |
|---|---|
| Helios crawler / `helios_ds_s3` connector | `READY` datasets only; source locators; ACLs returned with artifact metadata. The generator never writes `helios_index`. |
| LanceDB / Memgraph projections | Nothing directly; built only from `helios_index`. |
| Helios-DS-Evaluation (separate Workbench project) | `helios_ground_truth.*`, `expected_queries`, `expected_results`, threshold profiles. |
