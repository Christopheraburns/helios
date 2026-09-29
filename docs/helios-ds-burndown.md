# Helios-DS-Generation Burn-Down

Sequenced task list for building the Helios-DS asset generator: the mechanisms to **create, review, edit and approve** synthetic assets.
Source: *Helios-DS Asset Generator — Engineering Development Specification* (the "spec").

**How to use:** work top to bottom. A task is done only when its **Done when** check passes; the spec requires objective readiness gates, not "basically works". Check the box and add the commit hash.

**Scope:** `apps/helios-ds-generation` (the generator and its review workflow). The Helios crawler, analyzers, LanceDB/Memgraph projections and the evaluator are separate projects; the items this project must hand off to them are listed in [Handoffs](#handoffs-to-other-projects).

**Status at creation (2026-09-29):** scaffold only. `helios_ds/config.py` (partial config schema), `ids.py` (UUIDv5 helpers), `scenarios.py` (planner skeleton), `schemas.py` (record models), a FastAPI app whose endpoints return placeholders and keep jobs in an in-memory dict, and a React UI with submit/monitor/results pages. No generators, storage, lakehouse writes or tests exist yet.

---

## Design decision: how review, edit and approve fit a deterministic generator

The spec requires that the same source + config + seed + version produce the same corpus, and that anything not produced deterministically be "frozen and versioned" before it enters the benchmark. Review and editing must therefore **never modify artifact bytes in place**. Proposed model (confirm before starting R-tasks):

- **Review** is a dataset lifecycle stage. The spec's `CREATING → VALIDATING → READY` becomes `CREATING → VALIDATING → IN_REVIEW → APPROVED (READY) | REJECTED`. Only `READY` datasets are visible to the crawler.
- **Edits are curation overlays**: versioned, declarative changes (template field overrides, excluded artifacts, adjusted claim or ACL assignments, pinned replacement bytes for a single artifact) stored in Git or Iceberg. The overlay bundle's hash becomes part of `dataset_id`, so an edited corpus is a *new* reproducible dataset, not a mutated one.
- **Ground-truth consistency:** every overlay re-derives the affected truth rows (mentions, claims, evidence locators) and re-runs validation, so edits cannot silently break the answer key.
- **Isolation:** the review UI shows hidden ground truth and therefore runs only in the generation project, never where crawler or query credentials exist.

---

## Phase 0: Stabilise the existing scaffold

*Status 2026-09-29 (uncommitted):* P0-01 to P0-04 done: 41 tests pass (`pytest apps/helios-ds-generation/tests`), plus ruff and mypy. P0-05's workflow file is written and its Python steps pass in a clean venv, but it stays open until a PR on GitHub shows the checks. Notes: the ID derivation changed (every ID below the dataset is now namespaced by the dataset, per the spec) and the golden values are pinned in `tests/deterministic/test_ids.py`. The `balanced` difficulty profile is renamed `mixed` to match the spec's request example. The old `/api/*` routes are removed. Known gap: `pyproject.toml`'s `helios-core @ {path=...}` dependency isn't valid PEP 508, so `pip install -e` fails; CI installs dependencies directly until F-11.

- [x] **P0-01** Add `tests/` (unit, deterministic, integration, benchmark) and `fixtures/tiny/` per spec layout; wire pytest to the `src` layout. *Done when:* `pytest apps/helios-ds-generation/tests` runs green in Workbench.
- [x] **P0-02** Fix determinism gaps in `ids.py`: namespace `truth_entity_id` and `claim_id` by dataset, and add `artifact_seed`/`scenario_seed` derivation exactly as in the spec (SHA-256 chain). *Done when:* fixed fixtures produce hard-coded expected IDs.
- [x] **P0-03** Harden `config.py`: auto-validate difficulty weights, validate or normalise scenario weights, reject unknown fields (`extra="forbid"`), and add canonical JSON serialisation plus `config_hash`. *Done when:* equivalent configs hash identically and unknown incompatible fields fail with a clear error.
- [x] **P0-04** Replace the placeholder routers with stubs matching the spec's REST contract (`/v1/generations`, `/v1/jobs/{id}`, `/v1/datasets/...`); keep the UI working against them. *Done when:* the UI round-trips against the `/v1` stubs.
- [ ] **P0-05** Add the GitHub Actions PR workflow `helios-ds-gen.yml`: ruff, mypy, unit tests. *Done when:* a PR shows the checks.

## Phase 1: Foundation (spec "Foundation milestone")

No media generation merges until this phase's exit criterion passes.

- [ ] **F-01** Lakehouse namespaces: create `helios_ds.*` and `helios_ground_truth.*` Iceberg tables (datasets, generation_runs, artifacts, artifact_sources, template_versions, source_principals, source_acl_bindings; entities, entity_mentions, relationships, claims, evidence, expected_queries, expected_results). *Done when:* DDL is in Git and the tables exist in the Workbench Hive Metastore.
- [ ] **F-02** `LakehouseSink` abstraction over PyIceberg: Hive Metastore catalog in Workbench, PostgreSQL SQL catalog for offline CI. *Done when:* the same test suite passes against both catalogs.
- [ ] **F-03** TPC-DS repository adapter (Impala/Iceberg) with a local fixture adapter. *Done when:* the same fixture maps to identical canonical records and shuffled input order yields the same output.
- [ ] **F-04** Source snapshot fingerprint: Iceberg snapshot IDs where available, plus table/schema metadata and aggregate hashes. *Done when:* the fingerprint is stable across runs and changes when a source table changes.
- [ ] **F-05** Deterministic scenario planner: SHA-256 candidate ranking, top-N selection, per-scenario and per-artifact seeds, local `random.Random` only, and a lint or test that forbids module-level `random` calls. *Done when:* three clean runs give identical scenario IDs and plans, and shuffled source input gives the same plan.
- [ ] **F-06** Scenario definitions for `product_return_damage`, `warehouse_inventory_issue` and `promotion_performance`, mapping TPC-DS sources to artifact plans (spec baseline mapping table). *Done when:* each type produces a plan from SF1.
- [ ] **F-07** Template registry: `templates/<type>/<id>/template.yaml` exposing every field the spec requires (id, version, schema_version, supported scenarios, required/optional fields, rendering params, locator strategy, content hash); the bundle hash feeds `dataset_id`. *Done when:* changing any template byte changes `dataset_id`.
- [ ] **F-08** Object-store abstraction (S3-compatible over the Cloudera S3/ADLS/Ozone location, project filesystem for development) with write-if-hash-matches semantics. *Done when:* re-writing the same artifact is a no-op, and a hash mismatch raises a determinism-integrity error without overwriting.
- [ ] **F-09** Public manifest writer: canonical JSON manifest plus `helios_ds.datasets`/`artifacts`/`artifact_sources` rows. *Done when:* manifests are byte-identical for equivalent runs.
- [ ] **F-10** Dataset lifecycle state machine: `CREATING → VALIDATING → IN_REVIEW → READY | FAILED | REJECTED`. *Done when:* a dataset cannot reach `READY` with incomplete files or tables, and a failed run is never visible as `READY`.
- [ ] **F-11** Runtime images: `helios-ds-runtime` (pinned Python and lockfile, ReportLab, Pillow, PyIceberg, FastAPI, MCP SDK, Celery, fonts) and `helios-ds-media-runtime` (plus pinned FFmpeg and eSpeak NG), built on a Cloudera ML Runtime base and registered in Workbench. *Done when:* both images appear in the project runtime catalog and their digests are recorded.
- [ ] **F-12** **Exit gate:** TPC-DS SF1 → planner → deterministic plan → Iceberg generation manifest, run twice from clean state. *Done when:* the plans and manifests are identical.

## Phase 2: Asynchronous job service

- [ ] **J-01** PostgreSQL job schema: jobs, tasks, events, idempotency keys; replaces the in-memory `_jobs` dict. *Done when:* job status survives an API restart.
- [ ] **J-02** Celery + Redis with queues by resource class (planning, documents, images, audio, video, finalization). *Done when:* tasks route to the correct worker.
- [ ] **J-03** `POST /v1/generations` returns 202 with a job handle; `Idempotency-Key` returns the existing job; `:cancel` cancels cooperatively. *Done when:* the idempotency and cancellation contract tests pass.
- [ ] **J-04** Idempotent artifact tasks using deterministic IDs and write-if-hash-matches. *Done when:* a forced worker retry creates no duplicate logical artifacts, and failed tasks record an actionable cause.
- [ ] **J-05** Workbench deployment: `helios-ds-api`, `helios-ds-worker` and `helios-ds-media-worker` Applications plus a `helios-ds-validate` Job, with launch scripts in `workbench/`. *Done when:* all three Applications start in Workbench.

## Phase 3: Create — textual artifact slice (spec "Textual-artifact milestone")

First business story: Sale → Return → Complaint email → Return authorisation PDF → Internal support chat.

- [ ] **C-01** PDF generator (ReportLab): return report and return-authorisation templates; deterministic or stripped PDF metadata; selectable-text and scanned-style pages. *Done when:* 100% of PDFs parse, truth locators resolve, and hashes are stable in the pinned image.
- [ ] **C-02** Email generator (`EmailMessage`): deterministic Message-IDs, MIME boundaries, dates and attachment names; thread provenance. *Done when:* every `.eml` parses, headers match truth, and hashes are stable.
- [ ] **C-03** Chat generator: the neutral Helios-DS JSON/JSONL schema. *Done when:* output is 100% schema-valid, thread and message references are valid, and hashes are stable.
- [ ] **C-04** Ground-truth builder for the slice: entities, mentions with precise locators, relationships, claims (including `truth_status`) and evidence. *Done when:* there are zero orphan rows and all evidence locators resolve.
- [ ] **C-05** Alias and difficulty tiers applied per the config's difficulty weights (direct, alias, contextual). *Done when:* the tier distribution matches the config within tolerance.
- [ ] **C-06** Security simulation: source principals (alice, bob, carol, dave, erin, public_agent), groups, and `source_acl_bindings` per artifact. *Done when:* every artifact has an ACL policy and the bindings resolve.
- [ ] **C-07** Validation job (`helios-ds-validate`): count checks, artifact-resolution checks (SHA-256 of retrieved bytes equals the manifest), and truth referential integrity. *Done when:* a deliberately corrupted fixture fails validation.
- [ ] **C-08** Golden queries v1 in `expected_queries`/`expected_results`, e.g. "How many returns did the item have?" and "What are customers saying about those returns?". *Done when:* the queries exist with required-evidence specs.

## Phase 4: Review, edit and approve

- [ ] **R-01** Confirm the curation-overlay design above and record it as an ADR in `docs/`. *Done when:* the ADR is merged.
- [ ] **R-02** Overlay schema and storage: versioned overlay records (field override, artifact exclusion, claim/ACL change, pinned replacement bytes with provenance) plus the overlay-bundle hash in `dataset_id`. *Done when:* applying the same overlay twice produces an identical dataset, and a different overlay produces a different `dataset_id`.
- [ ] **R-03** Review APIs: list datasets in `IN_REVIEW`; get an artifact with a native preview URL, its manifest, TPC-DS source rows and ground truth; add review comments. *Done when:* the contract tests pass.
- [ ] **R-04** Review UI: per-artifact viewer (PDF, image, email, chat; audio/video in phase 5) side by side with its ground truth and source rows; per-artifact accept, flag and comment. *Done when:* a reviewer can walk the textual slice end to end.
- [ ] **R-05** Edit workflow: UI and API to author overlays, regenerate the affected artifacts and truth, and re-run validation automatically. *Done when:* an edited artifact's truth locators still resolve and validation passes.
- [ ] **R-06** Approval workflow: approve or reject the whole dataset with approver identity and a timestamp in generation-run telemetry; approval moves the dataset to `READY`, rejection to `REJECTED`. *Done when:* only approved datasets are crawler-visible, and approval is blocked while validation errors or unresolved flags exist.
- [ ] **R-07** Audit trail: every review, edit and approval event is persisted and queryable. *Done when:* the history of an approved dataset can be reconstructed from the events.
- [ ] **R-08** Diff view: compare two datasets or overlay versions (artifacts added, removed or changed; truth deltas). *Done when:* the diff between the base and an edited dataset is correct on a fixture.

## Phase 5: Create — visual slice

- [ ] **V-01** Image generator (Pillow, pinned fonts): product packaging, labels, receipts, shelves, synthetic damage. *Done when:* dimensions and format are correct and pixel/file hashes are stable.
- [ ] **V-02** Region-level ground truth (bounding boxes) and the difficulty classes easy, alias, OCR-only, region and context. *Done when:* all regions lie within image bounds and each class is represented.
- [ ] **V-03** Extend review/edit (R-04, R-05) to images, with bounding-box overlays in the viewer. *Done when:* a reviewer can inspect and flag regions.

## Phase 6: Create — audio and video slice

- [ ] **A-01** Audio generator: eSpeak NG → deterministic WAV, scripted speaker turns, time-span truth. *Done when:* audio decodes, spans fit the duration, hashes are stable, and reference ASR recovers the required facts above threshold.
- [ ] **A-02** Video generator: procedural frames + TTS track + FFmpeg with pinned codec, frame rate, pixel format, threads and metadata; scene/keyframe/timecode truth (e.g. the warehouse-inspection storyboard). *Done when:* ffprobe validation passes, spans fit the duration, and hashes are stable on the reference platform.
- [ ] **A-03** Extend review/edit to audio and video, with a timeline scrubber showing truth intervals and transcript. *Done when:* a reviewer can jump to cited intervals.
- [ ] **A-04** Contradictory-evidence scenario (e.g. "resolved Aug 12" vs. later damaged-package evidence) using the full `truth_status` set. *Done when:* the conflict set and expected conclusion are stored as a golden query.

## Phase 7: Agent interface and security

- [ ] **S-01** MCP facade over the same job service: tools `helios_ds_generate`, `_job_get`, `_job_cancel`, `_dataset_validate`, `_trigger_crawl`, `_evaluate`, `_compare_runs`; resources for manifest, status, evaluation and artifact metadata. *Done when:* MCP Inspector/SDK tests pass.
- [ ] **S-02** MCP Tasks for long-running generation, with a job-handle fallback for clients without Tasks. *Done when:* both paths are tested.
- [ ] **S-03** Review/approve MCP tools (list pending, get artifact review bundle, submit overlay, approve or reject), restricted to reviewer principals. *Done when:* an unauthorised principal is refused.
- [ ] **S-04** Keycloak/OIDC integration profile; reviewer and approver roles enforced on REST, MCP and UI. *Done when:* role tests pass.
- [ ] **S-05** Ground-truth isolation: a Ranger policy on `helios_ground_truth`, and crawler and query credentials verified to lack access. *Done when:* a negative access test from the crawler project fails as expected.

## Phase 8: Reproducibility and release gates

- [ ] **Q-01** PR CI: static checks, unit, schema-compatibility, seed/ID, tiny-corpus generation, golden manifest/hash comparison, REST/MCP contract tests.
- [ ] **Q-02** Merge-to-main CI: runtime image build/push/registration; clean-state Workbench Jobs via the Cloudera AI API; generate the tiny corpus twice and compare.
- [ ] **Q-03** Pin and record everything the spec lists (Python, lockfile, base and custom image digests, FFmpeg, eSpeak NG, fonts, template bundle, manifest schema, source fingerprint, generator version, architecture) in every dataset record.
- [ ] **Q-04** Clean-room reproducibility test: two fresh environments produce equal `dataset_id`, artifact IDs, manifests, truth records and artifact hashes. *Done when:* the test is a release-blocking CI gate.
- [ ] **Q-05** Scale-up: developer/integration corpus at configured counts (default PDF 500, image 300, email 500, chat 300, audio 100, video 50) with 50–100 golden questions. *Done when:* a full SF1 run reaches `READY` through review and approval.

---

## Handoffs to other projects

Items the generator must support but does not own:

| Consumer | Needs from Helios-DS |
|---|---|
| Helios crawler / `helios_ds_s3` connector | `READY` datasets only; source locators; ACLs returned with artifact metadata. The generator never writes `helios_index`. |
| LanceDB / Memgraph projections | Nothing directly; built only from `helios_index`. |
| Helios-DS-Evaluation (separate Workbench project) | `helios_ground_truth.*`, `expected_queries`, `expected_results`, threshold profiles. |
