# Helios Crawler Burn-Down (v1: text)

Plan for the first Helios crawler. **How it analyzes an asset, step by step, and what is configurable: [crawler-analysis.md](crawler-analysis.md).** It indexes the Helios-DS development corpus (PDF, email, chat), resolves what the documents mention to TPC-DS entities through the ontology, extracts claims, writes `helios_index` in the lakehouse, and projects the result into Memgraph. Its output is scored against the hidden ground truth and the golden questions.

**Status (2026-10-06):** since the status below, crawls start from the UI (DS-6), crawled passages are embedded and searchable from Talk to Your Data (CR-11, built; golden questions not yet scored), the answer path shows a document lane, and the Ontology page browses from a class to its entities, documents and passages (CR-9). DS-6 and CR-11 are in commit `83ac412`; the CR-9 instance browsing is uncommitted. CR-E2 and CR-L1 followed the same day (uncommitted): the LLM crawler ran on the development corpus and was scored. Next: CR-L2 (hybrid), then C-12 and CR-L3. A side quest, ontology authoring (OA-0 to OA-10), was added to [ontology-burndown.md](ontology-burndown.md) on 2026-10-06. A plan to make the crawler fully configurable (CG-0 to CG-10) was added the same day; it now comes before CR-L2.

**Status (2026-10-02):** the deterministic arm (A) is complete end to end on the development corpus: fetch, segments, mentions, joint resolution, claims, Memgraph projection and the scoring harness (CR-0 to CR-8). Every gate is met; see the table under CR-8. Inputs were ready from the start:
- **Development corpus:** `1ca99f86-e6a0-57fc-8311-cadcac4c8302`, READY. It has 101 return stories and 301 artifacts.
  - Ground truth: 794 entities and 3,135 mentions (2,131 direct, 698 alias, 306 contextual), 403 claims, 785 evidence passages.
  - 60 golden questions; question-set SHA-256 `ded20c2a…`.
- **Ontology and graph:** LinkML ontology (`ontology/`), parser (`shared/helios_core/ontology/`), Helios Graph Application with a Memgraph gateway (`apps/helios/graph/`), ontology viewer.

**How to use:** work top to bottom. A task is done only when its **Done when** check passes. Check the box and add the commit hash.

---

## Principles

- **Evidence first, LLM second.** Version 1 resolves entities by working backwards from the data: identifiers and aliases are built from TPC-DS through the ontology mapping, and ambiguous references are settled with structured joins. A language model is an optional, separately measured tier (`model_assisted`), never the baseline.
- **The crawler never sees the answer key.** It reads only what a real connector would see: each artifact's bytes plus neutral metadata. Never `helios_ground_truth.*`, never the generation manifest (it contains story facts), and never `scenario_id` or `template_id` (they would reveal which documents belong to one story). S-05 enforces this with Ranger.
- **The lakehouse is canonical; Memgraph is a disposable copy** (as in the ontology plan). Every index row records the crawl run (index generation) and the ontology version it was made under.
- **Precision over recall** (`ontology/README.md`). Every link records `resolved_by` (tier) and its evidence segments. A match below threshold is `PossiblySameAs`, never `SameAs`.
- **Locators match the ground truth's conventions,** so scoring compares like with like:
  - email: `part`/`start`/`end`, or the `From` header;
  - chat: `message_id`/`start`/`end`;
  - PDF: `page` and the text on it.

## Architecture

```
helios_ds (READY datasets, neutral artifact metadata) + S3 artifact bytes
        │  connector (no ground truth, no manifest, no scenario/template IDs)
        ▼
  analyzers: email · chat · PDF  →  segments (with locators)
        ▼
  mention extraction: identifier patterns + TPC-DS gazetteer (from the ontology mapping)
        ▼
  resolution: exact_key → alias → fuzzy → joint (structured joins) → [model_assisted]
        ▼
  claims (subject, predicate, object, evidence) with the retail pack's predicate vocabulary
        ▼
  helios_index.* (Iceberg via Impala)  ──►  Memgraph projection (Helios Graph gateway)
        ▼
  Helios-DS-Evaluation (separate project; reads ground truth + helios_index)  →  scores
```

The crawler runs as a **Workbench Job in the Helios project** (`apps/helios/`). It does not run in the generation project, which holds ground-truth access.

---

## Tasks

### Prerequisites

- [x] **CR-0a** *(Done 2026-10-01, uncommitted.)*
  - **Retail pack 0.2.0:**
    - new classes `Brand` (is_a Organization), `Reason` (is_a EnterpriseEntity) and `Sale` (is_a Transaction);
    - `Item` gains `brand → Brand`, `color` and `item_class`;
    - `Return` gains `reason → Reason`, `of_sale → Sale`, `returned_item` and `authorization_number` (RMA; documents only);
    - relationship types `Contains`, `ReturnOf` and `HasReason`;
    - the `RetailClaimPredicate` enum (decision 4).
  - **`mappings/ossie/tpcds.yaml`** targets `helios_retail@0.2.0` with the published model's names:
    - datasets `customer`, `store`, `item`, `reason`, `store_sales`, `store_returns` and so on, with Brand mapped from `item` by `i_brand_id`;
    - relationships `<table>__<column>__<target>`;
    - concepts `metrics.store_sales_revenue` and `metrics.store_returns_amount`;
    - free-text alias columns dropped (`i_item_desc`, `s_market_desc`);
    - resolution scope set to the corpus prefix.

    Return → Sale has no Ossie relationship (TPC-DS joins them on ticket and item), so the crawler derives `ReturnOf`.
  - **Validation:** `apps/helios/tests/test_ontology_mapping.py` (8 tests) checks every element, column, relationship and metric against the published model, and every class and attribute against the ontology. `linkml-lint` itself is not installed in this environment.

  Original task: Fix the ontology files listed in `docs/ontology-burndown.md`:
  - add `Sale`, `Reason` and `Brand` to the retail pack;
  - map Ossie elements by the published model's names (`customer`, `store`, `item`, `store_sales`, … rather than `dim_*`), and point the glossary concepts at metrics that exist;
  - set resolution scopes to the corpus prefix `s3a://applied-ai-buk-d5eff1ab/helios-db/source/datasets/**`.

  *Done when:* `linkml-lint` passes and the viewer shows no broken mappings.
- [x] **CR-0b** *(Done 2026-10-01, uncommitted.)*
  - **`helios_core.ontology.mapping`:** typed `SourceMapping` loaded from `ontology/mappings/**`, plus `resolution_config()`, which gives the resolver per-class primary, secondary, display and alias columns, scope matching and tier thresholds.
  - **Parser:** adds every mapping whose `ontology_version` is in the schema's import chain:
    - `OssieElement -MAPS_TO-> Class` with the identifier columns;
    - `OssieElement -MATERIALISES_AS-> Class` for relationships;
    - `GlossaryTerm` nodes for concepts.

    It reports broken mappings (`not_found`, `unknown_class`, `version_mismatch`), sets each class's `layer` (done with O-4b), and drops the unused `[Ossie: …]` description tags.
  - **Publish:** passes the mappings and `models/published/tpcds.ossie.yaml`.
  - **Viewer:** the inspector shows each class's Ossie mappings and identifiers, and classes whose mapped element is missing are outlined in red (completing O-4's broken-mapping highlighting).
  - **Real files:** 17 Ossie elements, 9 class mappings, 7 relationship mappings, 2 concepts, no broken mappings.

  Original task: Parser completeness:
  - read `ontology/mappings/*.yaml` (identifiers, aliases, resolution thresholds) instead of `[Ossie: …]` description tags;
  - set the `layer` property on Class nodes;
  - expose the mapping to the crawler as a typed `ResolutionConfig` (per class: primary, secondary, display and alias columns; per tier: threshold).

  *Done when:* a unit test loads `tpcds.yaml` into a `ResolutionConfig` for Customer, Store, Item, Sale, Return and Reason.
- [x] **CR-0c** *(Done 2026-10-01, uncommitted; not yet run against Impala.)*
  - **New package `shared/helios_core/index`:** record models, Impala Iceberg DDL with additive column migration, and a small `IndexStore` (impyla in Workbench, DuckDB in tests). This is the start of CR-1.
  - **Tables:** `helios_index.ontology_versions` holds each (version, hash) once, with publisher, time, counts and the canonical graph JSON. `helios_index.ontology_activations` is an append-only log; its latest row is the active version.
  - **Publishing** records in the lakehouse first, then the disk cache. Different content under an existing version is refused (409: publish under a new version); identical content is a no-op.
  - **`POST /api/v1/ontology/{version}:activate`** records an activation and points the cache at it.
  - **On API start,** a background thread restores any published versions the disk cache lacks, plus the active pointer, so the gateway's startup rebuild keeps working without Impala of its own.
  - **Without Impala settings** (local development) the cache is used on its own.
  - **Tables are created on first use;** the Helios workload user needs Ranger rights to create the `helios_index` database and to write its tables.
  - **Tests:** 8, in `apps/helios/tests/test_index_ontology_versions.py`.
  - The ontology burn-down is brought up to date.

  Original task: Store ontology versions in `helios_index.ontology_versions` (decision 1). Publishing writes the version there, and the gateway rebuilds from it. Bring `docs/ontology-burndown.md` up to date at the same time: O-2 to O-5 are committed but unchecked.
- [x] **CR-0d** *(Done 2026-10-01.)* Crawler identity: machine user `srv_helios_crawler` (EnvironmentUser role; Ranger policies on `tpcds`, the `crawlable_artifacts` view only in `helios_ds`, nothing on `helios_ground_truth`, and table rights in `helios_index`, which was created by an admin). The access check, run as that user, passes every SQL check:
  - **readable:** the view, `tpcds`, and `helios_index` (create, write, read);
  - **denied:** three `helios_ground_truth` tables, plus the `helios_ds.artifacts` and `scenario_plans` base tables.

  Through the view the crawler sees the READY datasets, including the development corpus `1ca99f86…` (301 artifacts), so the crawler takes the dataset to crawl as a setting.

  **Known gap (S3):** Workbench data connections authenticate as the person running the session, not the machine user. So RAZ cannot yet stop the crawler reading `_manifests/` (story facts); the check's manifest read shows *allowed*. Mitigation: the crawler reads objects only through the locators the view returns and never lists the bucket. Closing it needs the machine user's own IDBroker mapping and S3 credentials for the crawler Job.

  **Lessons from setup:**
  - A new machine user needs EnvironmentUser, a workload password and a completed user sync before Impala accepts it (401 until then; one transient 503 on first login).
  - The `helios_ds` policy must name the view, not `*`.

  Rotate the workload password: it appeared in chat during setup.

  Earlier note (prepared, needs an admin): Built so far:
  - **`helios_ds.crawlable_artifacts`**, a view created by Helios-DS `init-tables`. It shows the 8 neutral columns, for READY datasets only (latest lifecycle state), and never `scenario_id` or template IDs. Tested.
  - **`python -m apps.helios.crawler.access_check`,** run in the Helios project as the crawler. It prints the effective user and checks that the view and artifact objects are readable, and that ground-truth tables, `helios_ds` base tables and `_manifests/` objects are denied.

  **Isolation needs a separate identity.** Ranger and RAZ grant per user, so if the crawler connects as the same workload user that owns `helios_ds` and `helios_ground_truth`, nothing can be hidden from it. Use a dedicated machine user (e.g. `srv_helios_crawler`) as the crawler's `WORKLOAD_USER` and S3 connection identity.

  **Admin steps:**
  1. Run Helios-DS `init-tables` (creates the view).
  2. Create the crawler user.
  3. Create the output database once, as an admin: `CREATE DATABASE IF NOT EXISTS helios_index;`. It lives in the same metastore and warehouse as `tpcds`; the code creates the tables in it on first use.
  4. Ranger (Hadoop SQL) for the crawler user:
     - `tpcds`: SELECT on all tables (resolution builds its dictionaries and joins from TPC-DS);
     - `helios_ds`: SELECT on the `crawlable_artifacts` view only;
     - `helios_ground_truth`: nothing (explicit deny if broader policies would grant it);
     - `helios_index`: CREATE, SELECT, Update (Impala INSERT) and ALTER on its tables.
  5. The Helios API's own `WORKLOAD_USER` also writes `helios_index` (ontology versions, CR-0c): SELECT, Update, CREATE and ALTER there.
  6. RAZ/S3: allow read on `helios-db/source/datasets/*`; deny `helios-db/source/_manifests/*`.
  7. Give the Helios project an S3 data connection usable by that identity.
  8. Run the access check.

  Original task: Access for the Helios project:
  - read `helios_ds.datasets` and `helios_ds.artifacts` through a **view** that exposes only the neutral columns (`artifact_id`, `dataset_id`, `artifact_type`, `mime_type`, `source_locator`, `sha256`, `size_bytes`, `semantic_timestamp`), and only for READY datasets;
  - an S3 data connection reading `helios-db/source/datasets/`;
  - no access to `helios_ground_truth` or `_manifests/`. This is S-05 from the Helios-DS burn-down, done here.

  *Done when:* from the Helios project, the view is readable, and reading `helios_ground_truth.claims` or a manifest object is denied.

- [x] **CR-0e** *(Done 2026-10-01, uncommitted; tables migrated in Impala.)*
  - **`helios_core.crawler.settings.CrawlerSettings`:** typed and strict (unknown keys rejected).
    - **Sections:** analyzers (pdf, email, chat, each with options), identifier patterns, PDF field labels, contextual phrases, case-linking rules, and claim cues with negation and hedge words.
    - **Checks:** regexes must compile; capture groups must exist; `key` patterns need columns; case-linking must name existing patterns.
    - **Built-in defaults,** written from general retail language. Tests run them against real corpus text.
  - **`helios_index.crawler_settings`** holds versions 1, 2, 3… Identical content returns the existing version, and versions never change. `crawler_settings_activations` is the activation log. With nothing activated, the defaults apply.
  - **`crawl_runs`** gained `settings_version` and `settings_hash`.
  - **API (`apps/helios/console/crawler.py`):**
    - `GET /api/v1/crawler/settings` (active settings plus every version), `GET …/defaults`, `GET …/versions/{n}`;
    - `POST …/settings`: validates the structure, then checks every class and claim predicate against the **active ontology version**. It answers 422 with a list of problems, or saves a new version with a warning if no ontology is active;
    - `POST …/settings/{n}:activate`.
  - **Tests:** 14, in `test_crawler_settings.py`.
  - **Follow-up:** like the ontology endpoints, these record who acted but have no RBAC permission yet. Add one (e.g. reuse `datasource.manage`, or add `crawler.manage`) before wider use.

  Original task: crawler settings in the lakehouse Crawler settings in the lakehouse:
  - a typed settings schema with defaults: analyzers per asset type, identifier patterns, PDF label lexicon, contextual phrases, case-linking rules, claim cue lexicons and negation words;
  - versions stored in `helios_index.crawler_settings` with an activation log;
  - API: `GET /api/v1/crawler/settings` (active and versions), `POST …/settings` (validate and save a new version), `POST …/settings/{version}:activate`;
  - every crawl records the settings version and hash.

  The settings editor in the Helios UI is part of CR-9. Identity rules stay in the published ontology mapping.

  *Done when:* an invalid settings document is rejected with a clear message, a saved version is immutable, and a crawl run's recorded settings hash matches the version it used.

### Index and connector

- [x] **CR-1** *(Done 2026-10-01, uncommitted. Tables created in Impala as `srv_helios_crawler`; the crawler and API users can both read them.)*
  - **`shared/helios_core/index`** is the single definition; the documentation-only copies in Helios-DS are removed.
  - **Tables:** 11 Iceberg tables. `crawl_runs` is append-only, and a run's final state is its latest row. `assets`, `segments`, `mentions`, `entities`, `entity_links` (SameAs / PossiblySameAs with `resolved_by`, score and evidence segments), `relationships` (asset or entity at either end, typed by an ontology relationship class), `claims` and `claim_evidence`, plus the two ontology tables from CR-0c. Every run table carries `crawl_run_id` and `ontology_version`.
  - **Stable IDs** (`index.ids`) are derived from content, not the run, so runs compare row by row and unchanged assets can be carried forward. Entities are keyed by class plus source key (`tpcds.customer:c_customer_sk=…`).
  - **The store** batches inserts (500 rows or about 2 MB per statement), so each Iceberg commit isn't one row. It stores lists and dicts as canonical JSON and decodes them on read. A failed run's partial rows are deleted.
  - **CLI:** `python -m helios_core.index ddl | init-tables`. `schema_impala.sql` is checked for drift.
  - **Tests:** 6 in `test_index_schema.py`.

  Original task: `helios_index` schema, in a shared module (`shared/helios_core/index/`) that replaces the documentation-only copy in `helios_ds/schemas.py`, with the same pydantic → Impala DDL generation as Helios-DS. Tables:
  - `crawl_runs`;
  - `assets` (asset version = SHA-256), `segments`;
  - `mentions` (surface form, locator, proposed type);
  - `entities` and `entity_links` (SameAs / PossiblySameAs, `resolved_by`, score, evidence segments);
  - `relationships`;
  - `claims` and `claim_evidence`;
  - `ontology_versions`.

  Every row carries `crawl_run_id` and `ontology_version`. *Done when:* `init-tables` creates them in Impala and DDL drift is tested.
- [x] **CR-2** *(Done 2026-10-01, uncommitted; run for real on the development corpus.)*
  - **`apps/helios/crawler/connector.py`:**
    - lists one dataset through `helios_ds.crawlable_artifacts`, with neutral columns only;
    - fetches by recorded locator and never lists the bucket;
    - verifies SHA-256 and size;
    - retries transient storage errors up to 3 times with back-off, and fetches the first asset alone before going parallel (8 workers), so RAZ authentication is established.
  - **Statuses:** `fetched`, `carried_forward`, `integrity_failed`, `missing` (no object) and `fetch_failed` (storage error after retries). New `status` and `status_detail` columns on `helios_index.assets`.
  - **`apps/helios/crawler/crawl.py`:**
    - records the run, with the active settings version and hash and the ontology version;
    - plans incrementally against the last successful run (only reusable statuses carry forward, so failed assets are retried next time);
    - hands each verified asset's bytes to the analyzers (CR-3 onward);
    - removes a failed run's rows.
  - **Job command:** `python -m apps.helios.crawler crawl --dataset <id> [--full]`.
  - **Real runs as `srv_helios_crawler` on `1ca99f86…`:**
    1. 301 listed, 293 fetched, 8 failed (concurrent first requests hit RAZ authentication errors; this led to the retry and warm-up);
    2. 293 carried forward, 8 fetched;
    3. **all 301 carried forward, nothing fetched;**
    4. `--full` from cold: 301 fetched in 22 s, no failures.
  - **Tests:** 6, in `test_crawler_connector.py`.
  - **Note:** S3 reads run as the Workbench user who runs the Job (see CR-0d's S3 gap).
  - **Carrying forward derived rows** (segments, mentions…) for unchanged assets arrives with CR-3.

  Original task: `helios_ds_s3` connector: enumerate READY datasets and their artifacts through the CR-0d view, fetch bytes by locator, verify SHA-256, and skip unchanged assets (incremental by asset version). *Done when:* it lists the 301 assets of the development corpus and a second crawl fetches nothing.

### Understanding the text

- [x] **CR-3** *(Done 2026-10-02, uncommitted; run for real on the development corpus.)*
  - **`apps/helios/crawler/analyzers.py`:** detects each asset's type from its bytes (a mismatch with the declared type is recorded as `type_mismatch`, not guessed) and splits it into segments with ground-truth-compatible locators:
    - **email:** the identity headers (From, with display name and address), the subject (with the date), and the plain-text body with LF line endings; an HTML-only body is converted to text if the settings allow;
    - **chat:** one segment per message, with sender, role, timestamp and thread; only schemas the settings accept;
    - **PDF:** one segment per page (pypdf, up to the settings' page limit). Page `structure` holds label/value pairs (labels from the settings) and table rows (a run of at least 4 header lines starting at a known label, at least 2 of them known, followed by one value per header).
  - **Statuses:** `analyzed`, `unsupported` (no analyzer, or disabled in the settings), `type_mismatch`, `no_text` (e.g. a scanned PDF) and `invalid`.
  - **Segments are written to `helios_index.segments`.** The new column is `structure`, because FIELDS is an Impala reserved word; there's now a reserved-word test for every `helios_index` column.
  - **Unchanged assets carry their segments forward,** but only when the previous run used the same crawler version (now 0.2.0) and settings; otherwise they are re-analyzed.
  - **`pypdf==6.19.0`** is added to the Helios dependencies.
  - **Real run as `srv_helios_crawler` on `1ca99f86…`:** 301 analyzed into 1,003 segments (600 chat messages, 101 email headers, subjects and bodies, 100 PDF pages) in 24 s. On all 100 reports, the item table and all 9 labelled fields were read. A repeat crawl carried everything forward.
  - **Done-when check,** run as the evaluator (cburns): **all 785 ground-truth evidence passages and all 3,135 mentions fall inside a crawler segment** (email 266 and 918, chat 219 and 814, PDF 300 and 1,403).
  - **Tests:** 7 analyzer tests; the connector tests now use real sample assets.
  - The Crawler page shows Analyzed and Segments columns.

  Original task: Analyzers to segments: email (headers and body), chat (one segment per message), PDF (pages via `pypdf`, keeping text order). *Done when:* every development-corpus asset is segmented, and every ground-truth evidence locator falls inside a crawler segment. That last check runs in the evaluation project.
- [x] **CR-4** *(Done 2026-10-02, uncommitted; measured on the development corpus.)* `apps/helios/crawler/gazetteer.py` builds per-class surface forms from the warehouse through the mapping (secondary keys, display names, alias columns, rendered alias templates, and partial first/last names for classes with two display columns; 348,409 forms; token-indexed longest match). `mentions.py` runs four extractors per asset: PDF labels and table cells, identifier patterns, gazetteer (low-specificity names such as store `ese` need a class cue within 40 characters or confirmation elsewhere in the asset; partial names need an anchored instance in the same asset), and contextual phrases. Locators follow the ground-truth conventions with offsets. **Result (CR-8):** direct-tier recall 0.997, alias 1.0, contextual 1.0; precision 0.93 (value mentions excluded). Crawler 0.4.0. Tests: `test_crawler_mentions.py`.

  Original task: Mention extraction, built backwards from the data:
  - **identifier patterns:** item and customer IDs, ticket numbers, RMA and case numbers, email addresses, "receipt ending in NNNN", money and dates;
  - **a TPC-DS gazetteer** built from the mapping's display, secondary and alias columns: product, store, customer, brand and reason names, salutation plus surname, colour plus class, "<city> store";
  - **contextual references** ("the item", "this return"), which are recorded with no entity yet.

  *Done when:* direct-tier mention recall is ≥ 0.95 on the development corpus (scored by CR-8).
- [x] **CR-5** *(Done 2026-10-02, uncommitted; measured on the development corpus.)* `resolution.py` and `cases.py`: candidates by tier (exact_key, alias by specificity, partial names inheriting their anchor, minimal fuzzy), case clusters by union-find over the settings' identifiers, and **joint resolution**: one parameterised query per cluster against `store_returns` joined along the mapping's relationships (plus `store_sales` on ticket and item, `date_dim`, and co-located Brand on `item`), gated on a ticket or two constraint kinds; a unique row promotes every consistent mention to SameAs `joint`. Contextual references link when exactly one instance of the class is resolved in the asset or cluster. Return entities carry both the warehouse key and the document ids (RMA, CS). Relationships: Mentions, About, ReturnOf, Contains, LocatedAt, PartyTo, HasReason. `ResolutionConfig.relationships` exposes the mapping's join paths. **Result:** 101/101 clusters resolved uniquely; alias-tier accuracy 0.996, direct 0.999, contextual 1.0; SameAs precision 1.0 (3,351 links, none wrong, none below threshold); cases pairwise P/R 1.0/1.0; relationships 1.0 except LocatedAt/PartyTo recall 0.97 (returns with null store or customer keys). Crawler 0.5.0. Tests: `test_crawler_resolution.py`.

  Original task: Resolution, recording `resolved_by` and evidence on every link:
  - **exact_key:** identifiers;
  - **alias:** gazetteer aliases, scored, with the alias threshold;
  - **fuzzy:** rapidfuzz on names, with the fuzzy threshold;
  - **joint:** settle ambiguous candidates with structured joins among the entities resolved in the same asset. For example, "the Midway store" plus a resolved customer and item gives the `store_returns` row that names one store. This is the main new method of v1.
  - **contextual:** attach "the item" and similar to the asset's resolved entity of that type when there is exactly one.

  *Done when:* alias-tier resolution accuracy is ≥ 0.90 and nothing below threshold is linked as SameAs.
- [x] **CR-6** *(Done 2026-10-02, uncommitted; measured on the development corpus.)* `claims.py`: units are chat messages or sentences with exact offsets; cue phrases per predicate from the settings (strength strong/medium/weak → confidence 0.95/0.85/0.7; `*` gaps), clause-local negation and hedge guards, speaker voice (chat role, email author, PDF = staff); subject and object come from the case's resolved Return and its structural edges, one claim per (predicate, subject, object) per case with evidence per unit. **Result:** precision 1.0, recall 1.0 (403/403), evidence agreement 1.0 (785/785); all 60 golden questions pass at the retrieval level. Crawler 0.6.0. Tests: `test_crawler_claims.py`. The lexicon is generic retail language; the held-out corpus (C-12) is the over-tuning check.

  Original task: Claims: a predicate vocabulary declared in the retail pack (e.g. `PACKAGING_DAMAGED`, `RETURN_REASON`, `REFUND_REQUESTED`, `REFUND_APPROVED`), recognised by phrase rules over segments, with subject and object taken from the resolved entities and evidence spans recorded. *Done when:* claim precision is ≥ 0.90 and recall ≥ 0.80 on the development corpus.

### Projection, scoring and visibility

- [x] **CR-7** *(Done 2026-10-02, uncommitted.)* Push model, like the ontology: `apps/helios/graph/index.py` (labels CrawlRun, Asset, Segment, Mention, Entity, Claim; whitelisted edge types incl. the ontology relationship classes; `INSTANCE_OF` to the materialised Class node; MERGE on index ids so reloads are idempotent and `drop_run` removes one run), gateway endpoints `/v1/index/{run}:begin|/{table}|:finish`, `GET/DELETE /v1/index/{run}`, `GET /v1/index`; `apps/helios/console/graph_client.py` (`HELIOS_GRAPH_GATEWAY_URL` + `HELIOS_GRAPH_TOKEN`); `POST /api/v1/crawler/runs/{id}:project` pushes a run's tables in reference order and returns graph counts beside index row counts. Projections are not rebuilt on gateway restart (the lakehouse is canonical). Tests: `test_graph_index.py` (drop and reload give equal counts, on a Bolt stand-in; the live Memgraph test runs in the helios-graph runtime).

  Original task: Projection into Memgraph:
  - gateway endpoints to load one crawl run's entities, assets, segments, mentions, links and claims (whitelisted labels, as for the ontology), linked to the ontology classes they instantiate;
  - a Helios API client for the gateway (`HELIOS_GRAPH_GATEWAY_URL` plus token). The API currently reads the graph store files directly.

  *Done when:* deleting Memgraph's data and reloading from `helios_index` gives the same graph counts.
- [x] **CR-8** *(Done 2026-10-02 together with CR-E1, uncommitted.)* `apps/helios/crawler/evaluate.py` scores one crawl run against one ground-truth dataset: segment coverage, mention precision and recall by class and tier, resolution accuracy by tier and wrong SameAs, case clusters (pairwise), relationships by type (ground-truth predicates mapped to ontology edges), claims by predicate with evidence-locator agreement, and the golden questions by kind (no-answer questions must have nothing linked). Results go to `helios_index.evaluations` (`EvaluationRecord`; metrics are deterministic, so a re-evaluation is byte-identical). CLI `python -m apps.helios.crawler evaluate --run … --dataset …`; API `POST /api/v1/crawler/runs/{id}:evaluate` runs as the signed-in principal through Impala proxy delegation (`evaluator_mode`), 403 when the ground truth is not readable; `GET …/evaluations`. Crawler page: Score column, Evaluate action, Scores tab. Tests: `test_crawler_evaluate.py`.

  **Development corpus `1ca99f86…`, crawler 0.6.0 (final run `crawl_dad7102e…`, local index):**

  | Measure | Gate | Result |
  |---|---|---|
  | Segment coverage | 1.0 | 1.000 |
  | Mention recall direct / alias / contextual | ≥ 0.95 direct | 0.997 / 1.000 / 1.000 |
  | Mention precision (value mentions excluded) | — | 0.928 |
  | Resolution accuracy alias / direct / contextual | ≥ 0.90 alias | 0.996 / 0.999 / 1.000 |
  | SameAs precision | no sub-threshold SameAs | 1.000 (3,351 links) |
  | Cases pairwise P / R | — | 1.000 / 1.000 |
  | Claims precision / recall | ≥ 0.90 / ≥ 0.80 | 1.000 / 1.000 |
  | Evidence agreement | — | 1.000 |
  | Golden questions pass rate (all six kinds) | — | 1.000 |

  These numbers come from local development runs (run tables in DuckDB, TPC-DS and the corpus read from the lakehouse). The lakehouse run as `srv_helios_crawler` and its evaluation from the Crawler page are the next operational step. Original task: Scoring (reads ground truth and `helios_index`):
  - mention precision and recall by tier and entity type;
  - entity-resolution accuracy by tier;
  - claim precision and recall;
  - evidence-locator agreement;
  - golden questions at the retrieval level: for each question, are its required entities linked and its required evidence segments indexed? No-answer questions must have no linked documents.

  Results are written to a scores table and shown as a report. *Done when:* a scored report for crawl run × development corpus exists and is reproducible.
- [x] **CR-9** *(Done 2026-10-06. First slice 2026-10-01; instance browsing 2026-10-06, uncommitted, checked against the real index through the API but not viewed in a browser.)* A **Crawler** page in the Helios UI (Build → Crawler, `/crawler`, `pages/CrawlerPage.tsx`):
  - **Runs tab:** every crawl, newest first, with status, listed, fetched, carried forward, problems, duration, and ontology and settings versions; a source filter. Clicking a run shows its facts and request, asset counts by status and class (clickable filters), search, and each asset's status and problem detail.
  - **Settings tab:** the active version (or built-in defaults), version history with Edit and Activate, and a JSON editor with Format and a note. "Validate and save" lists the API's validation problems inline, then offers to activate the new version. There is help on what each section means.
  - **API:** `GET /api/v1/crawler/runs[?source=]` and `GET /api/v1/crawler/runs/{id}`.
  - **Tests:** 2 page tests and 1 API test.
  - **Scores and starting crawls** arrived with CR-8 and DS-6: the Scores tab, "Start crawl" with an optional note, and the note shown on each run.
  - **Instance browsing (2026-10-06):** selecting a class on the Ontology page shows "Found in documents": the entities of that class and its subclasses from each source's latest successful crawl, most-documented first, with search by name or key. Selecting an entity shows its warehouse keys, related entities, claims, and each linked document with the passages that mention it; the mentions are highlighted.
    - `shared/helios_core/index/browse.py`; `GET /api/v1/crawler/entities?classes=&q=&limit=` and `GET /api/v1/crawler/entities/{id}?run=`; `features/ontology/ClassInstances.tsx`.
    - Measured on the development corpus: 116 customers; an entity's detail takes about 3 seconds the first time and 0.5 seconds after (a finished run's tables are kept in memory).
    - Tests: 2 index tests and 3 component tests.
  - **Not done, moved out of CR-9:** a structured settings form (JSON editor only), an RBAC permission for the crawler pages, and segments and mentions per asset on the Crawler page.

  Original task: Visibility in the **Helios UI** (`apps/helios/ui`; all crawler screens live there, none in the Helios-DS dashboard), **including the crawler settings editor** (CR-0e: view versions, edit as a validated form or JSON, save as a new version, activate): a crawl-runs page (run, counts, scores) and instance browsing on the Ontology page (from a class to its entities, then their mentions and evidence). *Done when:* you can go from `Customer` to a customer's linked documents and the passages that mention them.

### Data sources: crawl any location (added 2026-10-02)

The crawler was wired to one location type: a Helios-DS dataset, read through `helios_ds.crawlable_artifacts` with files in one S3 bucket. Everything after "here are an asset's bytes" is location-independent, so locations become pluggable connectors, configured as **data sources** in Helios.

**Decisions (2026-10-02):**
1. Extend the existing `data_sources` table in Helios's metadata store. It already has organizations, RBAC (`datasource.read` / `datasource.manage`) and links to semantic models. Each crawl run snapshots the source's configuration into `crawl_runs`.
2. Build both an object-store connector and a "rows as documents" connector.
3. Do it now.

**Credentials never live in Helios tables.** A data source stores only a reference (a Workbench data connection name, or the Helios Impala settings), resolved by the crawler Job at run time. Ranger and RAZ still decide what the crawler identity may read.

- [x] **DS-1** *(Done 2026-10-02, uncommitted.)* `apps/helios/crawler/connectors/`: a `Connector` base (retried, verified, parallel fetching with warm-up; a `too_large` status; `test()` lists the scope and reads one readable asset) and three connectors. Crawl runs are keyed by data source (`crawl_runs.connector` = type, `source` = data source ID) and snapshot the source's configuration, without secrets. Outcomes that can't change with the same content and settings (`unsupported`, `type_mismatch`, `no_text`, `invalid`) now carry forward with their reason instead of being downloaded again. Crawler 0.3.0. The Job takes `--source <id>` (read from Helios's metadata store; set `HELIOS_METADATA_DB` on the Job if the API uses a non-default path) or `--dataset <id>` as before. Regression on `1ca99f86…`: 301 analyzed, 1,003 segments, as before. Original: Connector interface (`test`, `list(scope)` giving stable native IDs and version tokens, `fetch`) with the Helios-DS reader as the first connector. Crawl runs and assets are keyed by data source; the Job takes `--source`.
- [x] **DS-2** *(Done 2026-10-02, uncommitted.)* Metadata migration 8 adds `description`, `scope_json`, `crawl_json`, `updated_at` and `updated_by` to `data_sources`; it is applied automatically when the API starts. Scopes are validated per connector type (`helios_core.crawler.sources`). `apps/helios/console/data_sources.py` serves the types (with scope JSON schemas), list (with last crawl), get (with recent crawls), create, update, delete (409 while a model uses the source), and test connection. Reads need `datasource.read` and changes need `datasource.manage`, both at organization level (org admins today). Tests: 6 API tests and 1 repository test. Original: Registry: `data_sources` gains `description`, `scope` (validated per connector type), `crawl` (enabled, settings version, schedule) and `updated_at`/`updated_by`. API: list connector types and their scope schemas, list/get/create/update/delete sources, and test a connection, under `datasource.read` / `datasource.manage`.
- [x] **DS-3** *(Done 2026-10-02, uncommitted.)* `pages/DataSourcesPage.tsx` replaces the placeholder. It is now organization-level, so it no longer needs a model selected. It lists sources (type, scope, connection, last crawl), has an add and edit form per connector type, Test connection with a sample of assets, Delete, and shows the crawl command per source. Warehouse sources are listed read-only. Tests: 3 form tests. Original: The Data Sources page in the Helios UI (replacing the placeholder): list, add and edit with a form per connector type, Test connection with a sample of assets, Crawl now, last-crawl status.
- [x] **DS-4** *(Done 2026-10-02, uncommitted; tested live on 20 corpus files in S3.)* Paged listing, include and exclude globs, size limit, ETag plus size as the version token, MIME type from the extension. A plain-text analyzer is added (`text/plain`, `text/markdown`; one segment, with a `max_chars` setting). Original: Object-store connector: S3 or Ozone through a Workbench data connection; bucket and prefix, include and exclude patterns, MIME and size limits; ETag as the version token. Adds a plain-text analyzer.
- [x] **DS-5** *(Done 2026-10-02, uncommitted; tested live on `tpcds.item` as `srv_helios_crawler`.)* Validated identifiers only; filter values are bound parameters; row order is by key with a row limit. Each row's content is a small JSON document (`application/x-helios-row+json`), versioned by its hash. The row analyzer makes one segment per non-empty text column (`{"column": name}`). Original: Rows-as-documents connector: a lakehouse table through Impala; key, text and timestamp columns and an optional filter, validated as identifiers (no free SQL). Each row is one asset, and each text column one segment.
- [x] **DS-6** *(Done 2026-10-05, commits `023467d` and `83ac412`.)* Start crawls from the UI through the Workbench Jobs API. "Start crawl" on the Crawler page picks a registered source or a READY Helios-DS dataset, offers a full re-crawl and an optional note (up to 200 characters, stored with the crawl request), and runs the `helios-crawl` Job (`POST /api/v1/crawler/runs`, `console/crawl_jobs.py`, `jobs/crawl.py`). A second crawl of the same target waits for the first. Recent launches and their Job status are listed.
- [ ] **DS-7** Capture source access lists, and filter retrieval by them (with Helios-DS C-06).

### Evaluation harness and LLM crawler (added 2026-10-02)

Two crawlers run side by side on the same corpus and are scored by the same harness, to measure working backwards from the data against asking an LLM. Details: [crawler-analysis.md](crawler-analysis.md), section 9.

**Identities:**
- Both crawlers run as `srv_helios_crawler` and never see the ground truth.
- The harness runs as **the signed-in user's SSO principal**, started from the Crawler page through the Helios API, so Ranger decides who may evaluate. It needs Impala proxy delegation (`IMPALA_PROXY_DELEGATION`) so queries run as that user; without it they run as the API's `WORKLOAD_USER`, and each score records which.

**Arms**, each recorded on its crawl run as `strategy`:

| Arm | Finding mentions and claims | Resolving to TPC-DS |
|---|---|---|
| A, `deterministic` | rules, dictionaries, alias templates, cue lexicons (CR-4 to CR-6) | case grouping plus joint resolution against the warehouse (CR-5) |
| B, `llm` | an LLM reads each segment or case and returns entities, types and claims, each with the exact quoted text | exact keys the LLM quoted (IDs, emails) only; no warehouse reasoning |
| C, `hybrid` | the LLM, as in B | arm A's case grouping and joint resolution |

**LLM:** the project default from the AI Model Provider page (the project's `LLM_PROVIDER`, model and endpoint environment variables, through `helios_core.llm`). Session overrides live only in the API's memory and can't reach a Job. Provider and model are recorded on every run; keys are never stored in tables.

- [x] **CR-E1** *(Done 2026-10-02 with CR-8, except LLM-arm measures, which wait for CR-L1.)* Harness:
  - the CR-8 metrics: mentions by tier and class; resolution accuracy and wrong definite links; case clustering; claims; evidence locators; golden questions at the retrieval level, including correct "no answer";
  - plus, for LLM arms: hallucinated spans, tokens, cost and latency per document, and run-to-run variation;
  - results in a `helios_index.evaluations` table (run, corpus, evaluator principal, metrics, ground-truth dataset);
  - an **Evaluate** action and a scores view on the Crawler page, comparing arms per dataset.

  *Done when:* a crawl run can be scored from the UI, a user without ground-truth access is refused, and a re-evaluation gives the same numbers.
- [x] **CR-E2** *(Done 2026-10-06, uncommitted.)* `strategy` on `crawl_runs`, plus LLM provenance (provider, model, prompt version and hash, temperature, token and cost totals), and the Crawler page filters by strategy.
  - **Where it is recorded:** in the run's `settings` JSON (`strategy`, and `llm` with provider, model, temperature, prompt version and prompt hash), not as new columns, so no table change is needed and older runs read as `deterministic`. Totals are in the run's `counts` (`llm_calls`, `llm_cached`, `llm_tokens_in`, `llm_tokens_out`, `llm_ms`, `llm_hallucinated_spans`, `llm_claims_unanchored`, `llm_failed`, and `llm_cost_microusd` when prices are set in the settings). No key or endpoint is stored.
  - **Starting one:** `--strategy` on the crawler command, `HELIOS_CRAWL_STRATEGY` for the Job, `strategy` on `POST /api/v1/crawler/runs`, and a Strategy choice on "Start crawl".
  - **Crawler page:** a Strategy filter, an LLM label on the run, and the model, prompt and usage in the run detail.
  - **Scorecard:** `run.llm` carries the model, tokens, cost, time per document and the hallucinated-span rate (the CR-E1 measures that were waiting). Run-to-run variation waits for CR-L3.
  - **Rules that follow from it:** a crawl reuses only a previous run of the same strategy; search, embeddings and ontology browsing use the latest *deterministic* crawl only.
- [x] **CR-L1** Arm B, the LLM crawler:
  - prompts that give the ontology classes, definitions and claim vocabulary, and ask for JSON with the exact quoted text for every entity and claim;
  - temperature 0;
  - every quote mapped back to character offsets in its segment; quotes not found in the document are dropped and counted as hallucinated;
  - responses cached by (model, prompt hash, segment hash), so reruns are cheap and identical;
  - prompts versioned in the crawler settings (CR-0e).

  *Done when:* it crawls the development corpus into `helios_index` with every row grounded to a real span.

  **Done 2026-10-06, uncommitted** (`apps/helios/crawler/llm_arm.py`; crawler version 0.7.0).
  - **How it works:** one call per document (all its segments), on the project's default model. The prompt is the settings' `llm.instructions` (versioned as `llm.prompt_version`) plus the class definitions and claim vocabulary read from the ontology files. Replies are cached in `state/llm_cache` by provider, model, prompt hash and document text. A quote is located in the segment the model named, else anywhere in the document; one that is nowhere is dropped and counted.
  - **Resolution, as arm B is defined:** a quoted business key (customer ID, email address, item or store ID) naming exactly one warehouse row; a quoted RMA or case number is its own Return entity. No names, no case grouping, no warehouse queries. A claim needs a subject and object that resolved.
  - **Run on the development corpus** (`crawl_da9ce15c813d4ff49762f71fe3fc79cb`, Mistral medium, 7 min 49 s): 301 documents, 3,840 mentions, 817 definite links, 332 entities, 30 claims. All 3,840 mentions and 34 evidence passages match their span exactly. 231 of 5,090 returned items (4.5%) quoted text that was not in the document and were dropped. 214,765 tokens in, 183,564 out; 2.9 s per document.
  - **Scored** (evaluation `5b9881f7`), next to the deterministic run:

    | Measure | Deterministic | LLM (arm B) |
    |---|---|---|
    | Mention recall: direct / alias / contextual | 99.7% / 100% / 100% | 92.3% / 95.6% / 80.7% |
    | Mention precision | 92.8% | 72.9% |
    | Mentions resolved to the right row | 99.8% | 10.1% |
    | Definite-link precision | 100% | 39.2% |
    | Claims: precision / recall | 100% / 100% | 0% / 0% |
    | Golden questions (retrieval level) | 100% | 16.7% |

  - **Reading the table:** every link judged wrong is a Return linked to an entity made from its RMA number, which has no warehouse key, so the harness cannot match it; customers, items and stores have no wrong links. The same cause gives zero claims: each claim involves a Return. This is arm B's intended limit (no warehouse reasoning), and what CR-L2 is for.
  - **Not done:** prices are unset, so no cost is reported; the prompt has had no tuning. (A second run from the cache gave identical rows on 2026-10-06; see CG-0.)
- [ ] **CR-L2** Arm C, hybrid: arm B's extraction feeding arm A's case grouping and joint resolution (needs CR-5). *Done when:* scored on the same corpus as A and B.
- [ ] **CR-L3** Comparison report: A, B and C side by side on the development and held-out corpora (C-12); arm B run three times for variation. *Done when:* the report exists and is reproducible from recorded runs.

**Order:**
1. CR-3 (segments, shared by all arms);
2. CR-E1 and CR-E2, so every later step is measured;
3. CR-4 to CR-7 for arm A, alongside CR-L1;
4. CR-L2 after CR-5;
5. CR-L3.

C-12 (held-out corpus) is needed before CR-L3. CR-10 (the LLM as a last tier inside arm A) stays as a fourth variant.

### Configurable crawler: no shapes in code (added 2026-10-06)

Remove every retail, returns and TPC-DS assumption from the crawler's code and make each one configuration an end user can set in the UI, keeping today's accuracy on the development corpus exactly. Plan, inventory and configuration design: [crawler-configurable.md](crawler-configurable.md). Making the configuration easier to produce (simpler screens, LLM-proposed settings) is a later step and not part of this.

**Gates, checked after every task:**
- **Same output:** with the `retail-returns` preset, a crawl of the development corpus gives the same row IDs, the same content and the same scorecard as the baseline recorded in CG-0.
- **No shapes in code:** a test fails if a crawler source file contains a class, claim, table, column or domain word from the preset. The list of known violations only shrinks.

- [x] **CG-0** *(Done 2026-10-06, uncommitted.)* Safety net. Record the baseline (row IDs and content hashes per table, and the scorecard) for the development corpus and the fixture crawls; add the "no shapes in code" test with today's violations listed. *Done when:* the baseline is reproduced twice from the current code, and the test fails if a new violation is added.
  - **Fingerprint** (`apps/helios/crawler/baseline.py`): per table, the number of rows and a hash of their IDs and content, the run's counts, and the scorecard (headline numbers and a hash of every metric). Left out: the run ID, and whether a document was read again or carried forward.
  - **Development corpus:** `python -m apps.helios.crawler baseline record|check <file>` (needs Impala and ground-truth access; exits 1 with the differences). Baselines in `apps/helios/crawler/baselines/`: `development-rules.json` (301 assets, 1,003 segments, 4,297 mentions, 511 entities, 3,351 links, 2,831 relationships, 403 claims, 1,007 evidence; every headline score as in CR-8) and `development-llm.json`.
  - **Reproduced:** the rules baseline by a full re-crawl with every document read again (`crawl_a5e7238c…`); the LLM baseline by a crawl answered entirely from cached replies (`crawl_74ef6f74…`, 301 of 301 cached, no model calls). Both identical, scorecards included. The second also settles CR-L1's open item on cache reproducibility.
  - **Offline, in the test suite** (`test_crawler_baseline.py`): the seven sample documents against the sample warehouse, for both strategies, compared with `tests/baselines/sample-*.json`: every row, and every query sent to the warehouse (four, for the rules crawl). Reproduced in separate processes, and by a carried-forward crawl.
  - **No shapes in code** (`tests/shapes.py`, `test_crawler_shapes.py`): the vocabulary is read from the shipped retail pack, the shipped mapping and the default settings, not typed by hand; it is looked for in string constants and names, not comments or docstrings. Today: **493 uses in 8 files** (`resolution.py` 212, `settings.py` 181, `claims.py` 55, `evaluate.py` 16, `mentions.py` 12, `gazetteer.py` 10, `access_check.py` 6, `llm_arm.py` 1), listed in `tests/baselines/crawler_shapes.json`. Adding one fails; removing one fails until the list is regenerated, so the list always states what is left.
  - **To record a deliberate change:** `HELIOS_UPDATE_BASELINE=1` with either test file; `baseline record` for the development corpus.
  - **Limits:**
    - The development-corpus check is a command, not part of the test suite: it needs a crawl, Impala and ground-truth access.
    - The shapes test finds retail *words*. It cannot see retail *structure* written in neutral words: the chat layout's field names, English sentence rules and date formats, lower-case cue words such as "store" and "item", and matching columns by their suffix. The inventory in [crawler-configurable.md](crawler-configurable.md) remains the checklist for those.
    - The sample PDF's bytes come from the PDF library; a different library version could change the sample baseline without any change to the crawler.
- [x] **CG-1** *(Done 2026-10-06, uncommitted.)* Settings schema 2. The engine's defaults become empty; today's rules become the `retail-returns` preset; stored version 1 settings are read as the preset plus their edits. *Done when:* same output with the preset; an empty configuration crawls without error and finds only segments.
  - **Engine defaults are empty.** `CrawlerSettings()` has no patterns, labels, contextual phrases, case identifiers or claim cues, and generic LLM instructions (`EMPTY_SETTINGS`; `is_empty()`).
  - **Preset:** `shared/helios_core/crawler/presets/retail-returns.yaml` is the old built-in rules, value for value (a test compares it with a dump taken from the old code). `presets/index.yaml` holds titles and descriptions. Engine code names no preset.
  - **Schema 1 documents** are read as the preset with the document's own sections on top, including ones saved before the `llm` section existed. Which preset that is, is stated in `presets/index.yaml`, not in code.
  - **API:** `GET /api/v1/crawler/settings/presets` and `/presets/{name}`; `/settings` reports `empty`; `/settings/defaults` now returns the empty document.
  - **Crawler page:** the Settings tab warns when the crawler has no rules, and offers "Start from <preset>" and "Start from empty".
  - **The crawler warns** at the start of a run when its settings are empty.
  - **This environment:** no settings version had ever been saved; crawls were using the built-in rules. The preset was saved as settings version 1 and activated, so behaviour is unchanged.
  - **Same output:** the sample tests pass without re-recording. On the development corpus, a rules crawl with settings version 1 (`crawl_ae65eb78…`, every document read again) and an LLM crawl (301 of 301 from cache, same prompt hash) both match their baselines, scorecards included.
  - **Empty configuration:** with no mapping it crawls and finds only segments; with a mapping it finds what the dictionary names, links no documents into cases and makes no claims.
  - **Shapes:** `settings.py` 181 to 0. Total 493 to **312 uses in 7 files**.
  - **Note:** the settings hash changed (the schema number is part of it), so the first crawl after this re-reads every document.
- [x] **CG-2** *(Done 2026-10-06, uncommitted.)* Mentions. Class cues, header rules, ordinary words and dictionary thresholds move to settings; the built-in ID format is removed (`mentions.py`: `CUES`, `_TPCDS_ID`, `_email_header`; `gazetteer.py`: `COMMON_WORDS` and thresholds). *Done when:* same output.
  - **Settings, new sections:** `class_cues` (per class, regular expressions joined into one case-insensitive pattern, exactly as the code built them) and `cue_window`; `header_rules` (a header field names an entity of a class); `dictionary` (`ordinary_words`, `short_token`, `max_shared_instances`, `max_form_tokens`).
  - **Code:** `mentions.py` and `gazetteer.py` read them; the built-in key format, the three classes' cue patterns, the sender-is-a-customer rule and the word list are gone. The dictionary is built with the settings' `dictionary` section; without one, no word is ordinary.
  - **Shapes:** `mentions.py` 12 to 0, `gazetteer.py` 10 to 0.
- [x] **CG-3** *(Done 2026-10-06, uncommitted.)* Document identity and small shapes. Key names on patterns, date formats, the "about" rules, resolution thresholds, and the source name from the mapping (`DOCUMENT_ID_NAMES`, `DATE_FORMATS`, `CLASS_PRIORITY`, `about_target`, `source_schema`). *Done when:* same output.
  - **Settings:** `key_name` on a pattern (the name a document identifier is stored under); `date_formats` (the engine itself reads only ISO dates); `about` (`title_segments`, `class_priority`); `resolution` (`max_candidates`, `close`, `low_specificity_factor`, `top_possible`); `cases.max_hub_documents`.
  - **Mapping:** `database` (the warehouse database the tables are in; when absent, the model's name up to its first dot). The crawler, the dictionary and the LLM arm take the source name from it. Added to the shipped file and its LinkML schema.
  - **Older saved settings:** a schema 1 document gets its patterns' `key_name` from the rules that were built in, so its entities keep their IDs.
  - **Left for CG-4:** `ROW_LIMIT` (an anchor's query limit) and everything about returns and sales. **Not done:** `access_check.py` still names one TPC-DS table to probe (6 uses).
  - **Shapes:** `llm_arm.py` 1 to 0; `resolution.py` 212 to 200.
- **After CG-2 and CG-3:** settings version 2 in this environment is the preset with the new sections (version 1, saved an hour earlier, lacked them and would have changed results). Same output confirmed: sample tests without re-recording; on the development corpus a rules crawl (`crawl_a84b262c…`) and an LLM crawl (301 of 301 from cache), both identical to their baselines with scorecards. Shapes: **277 uses in 4 files** (`resolution.py` 200, `claims.py` 55, `evaluate.py` 16, `access_check.py` 6).
- [x] **CG-4** *(Done 2026-10-06, uncommitted.)* Record anchors. Replace the return-and-sale code (`JointSchema`, `Constraints`, `build_query`, `promote`, `DATE_JOINS`, the structural edges) with the anchor model: lookups, joins, dates, related records, precedence and query limits. *Done when:* same output, and the warehouse query for every case in the development corpus is identical to the baseline's.
  - **Anchors are part of the mapping** (`anchors:` in `SourceMapping`): the anchor's class; `lookups` (a column, `equals` or `ends_with`, integer or string, the columns of the rules that feed it, and the class a value identifies); `joins` (foreign key, class, relationship); `colocated` classes; `date` (a column, or a join to a date table); `related` records (`join_on` key pairs, relationship, their own joins and date, `share_anchor_entities`); `row_limit` and `min_constraint_kinds`. Validated on save against the semantic model and the ontology; documented in the mapping's LinkML schema.
  - **Code:** `apps/helios/crawler/anchors.py` builds the plan, the query and the match from an anchor and knows no class. `resolution.py` lost `JointSchema`, the return and sale constants, the TPC-DS date joins, the ticket fields and the column-suffix matching. A document identifier's class comes from the rule that found it. A mapping with no anchors groups documents and resolves each mention by its tier, asking the warehouse nothing.
  - **Shipped mapping:** the retail anchor (a return of a sale) is declared in `ontology/mappings/ossie/tpcds.yaml`.
  - **Same output:** the sample tests pass without re-recording, which includes the text and parameters of every warehouse query. On the development corpus, a rules crawl with mapping version 2 (`crawl_b8611ad9…`: 101 cases, 101 queried, 101 resolved) and an LLM crawl both match their baselines with scorecards.
  - **Generic, shown by test:** `test_crawler_anchors.py` runs the same code on a clinic warehouse (visits, patients, clinicians) configured only by a mapping: a single-column key, a date in its own column, text lookups, no related record.
  - **This environment:** mapping version 2 (the shipped file with its anchor and database name) is active; version 1 had no anchor and would have resolved no cases under the new code.
  - **Simplifications to know:** a related record is named `join_on`, not `on`, because YAML reads a bare `on` as true. When a name could mean the anchor's entity or a related record's, the anchor's wins; that is fixed behaviour, not a setting. With several anchors a case is tried against each in order and takes the first that identifies a row; only one anchor has been exercised on real data.
  - **Shapes:** `resolution.py` 200 to 0. Total **77 uses in 3 files** (`claims.py` 55, `evaluate.py` 16, `access_check.py` 6).
- [x] **CG-5** *(Done 2026-10-06.)* Claims. Predicates with subject and object, where to find each, speakers and who may assert what, cue strengths and text rules move to settings (`SHAPES`, `STRUCTURAL_EDGES`, `UNIT_FIRST`, `STAFF_ONLY`, `CUSTOMER_ROLES`, `STAFF_SEGMENTS`, `GENERIC_WORDS`, `ABBREVIATIONS`). The LLM crawler reads the same shapes. *Done when:* same output for the rules crawler, and the LLM crawler's rows are unchanged from its cached replies.
  - **Settings, `claims` section:** `predicates` (per kind of claim: `subject` and `object`, each a class and an ordered `find` list; `blocked_speakers`; `unknown_speaker_needs_strong_cue`), `case_classes`, `speakers` (rules that say who is speaking), `weak_words`, `abbreviations`, `negation_window`, `gap_words` and `confidence`. The `find` steps are a fixed menu: `in_unit`, `case`, `{case_edge: <relationship>}`, `author`, `single_in_document`, `single_in_case`.
  - **Code:** `claims.py` holds no predicate, class, relationship or role name. Cue words for a kind of claim with no definition state nothing. The LLM crawler takes its claim shapes from the same settings (`claim_shapes`); so does the try-it panel.
  - **Older saved settings:** a schema 1 claims section keeps its cue words and gains the definitions that were in code.
  - **Form editor:** the Claims tab edits kinds of claim, speaker rules, weak words, abbreviations and the numbers.
  - **Same output:** sample tests without re-recording. On the development corpus with settings version 3: a rules crawl (`crawl_80265a2f…`) and an LLM crawl (301 of 301 from cache, same prompt hash), both identical to their baselines with scorecards.
  - **Shapes:** `claims.py` 55 to 0. Total **22 uses in 2 files**: `evaluate.py` 16 (the scoring harness, CG-9) and `access_check.py` 6 (unassigned). The crawler's own pipeline has none.
  - **Left in code by choice:** the names of segment types (`email_body`, `page`, `message`) and "the From header is the author" are the analyzers' own vocabulary, not a customer's.
- [x] **CG-6** *(Done 2026-10-06, uncommitted, ahead of CG-0 to CG-5; anchors join the mapping in CG-4.)* The mapping as a versioned document with an API (shared with ontology side quest OA-2): identifiers, anchors and thresholds, validated against the published semantic model and the active ontology. *Done when:* a mapping changed through the API drives a crawl, and the repository's mapping file is no longer read at crawl time.
  - **Store:** `helios_index.mappings` and `helios_index.mapping_activations` (`shared/helios_core/index/mappings.py`). Versions are immutable; identical content is not a new version; one active version per semantic model.
  - **API** (`/api/v1/ontology/mappings`): list, read a version, the shipped examples (`/shipped`), `:validate`, save (`POST`, needs `ontology.edit`, refused with its problems if invalid), and `/{version}:activate` (checked again first).
  - **Validation** (`mapping_problems`): each class mapped once with a key; plain column names; every table, column, relationship and metric exists in the published semantic model; every class and relationship type exists in the active ontology. A check that cannot run (no such model, no active ontology) is reported as not checked.
  - **Crawler:** reads the active stored mapping and prints its version and hash. With none active it warns and extracts no mentions, as it did with no mapping file. `ontology/mappings/` is not read at crawl time. Ontology publish also uses the stored mappings, falling back to the shipped files only when none is stored.
  - **This environment:** the shipped TPC-DS mapping was imported unchanged as version 1 and activated.
  - **Same output, checked by hand** (CG-0's automatic gate does not exist yet): crawl `crawl_b6fdf329d7…` through the Job gave the same counts as the previous rules crawl, and identical rows (IDs and content) in segments, mentions, entities, links, relationships, claims and evidence.
  - **Not done here:** no editor in the UI (since built, see CG-8), so a mapping was changed through the API; the Ontology page still shows the mapping captured when its version was published; the lookup of a semantic model by name reads `models/published/`.
- [x] **CG-7** *(Done 2026-10-07, uncommitted.)* Analyzers. Chat layouts as configured field paths; any remaining layout assumptions. *Done when:* same output, and a second chat layout is read by configuration alone.
  - **Chat layouts** (`analyzers.chat.layouts`): per format, the fields a file must have to be that format (`match`) and dotted paths to the list of messages, each message's ID, text, sender, sender's name, time and (optionally) role, the thread ID, and the list of people with their ID and role. The engine has no layout of its own: with none configured a chat file is recorded as `type_mismatch` and not read.
  - **PDF tables** (`analyzers.pdf`): `value_patterns` (what marks a line as a value and not a column heading), `header_max_words`, `table_min_columns`, `table_min_labels`. The rule that a 16-letter TPC-DS code is a value was in code, written without any retail word, so the shapes test had not flagged it; it is now the preset's second value pattern.
  - **Older saved settings:** a list of accepted format names (`schemas`) is read as one layout per name with the paths the code used to assume; a schema 1 document gets each analyzer's missing options from the rules that were built in.
  - **Form editor:** the Documents tab has a card per chat format and the PDF table rules.
  - **Second layout by configuration alone:** `test_crawler_analyzers.py` reads a differently shaped export (messages under `data.events`, sender under `user.id`, roles on a `members` list) with only a layout added, and one with the role on each message.
  - **Same output:** sample tests without re-recording. On the development corpus with settings version 4, with every document read again: a rules crawl (`crawl_962e5cd2…`, 301 analyzed) and an LLM crawl (301 from cache), both identical to their baselines with scorecards.
  - **Left in code by choice:** which MIME type goes to which analyzer; the email analyzer (the standard message format); Helios's own table-row format; and the names of segment types and of the fields a chat message is stored with (`sender`, `role`, `timestamp`).
  - **Shapes:** unchanged at 22 uses in 2 files (`evaluate.py`, `access_check.py`); nothing in this task was visible to that test.
- [ ] **CG-8** UI. A structured editor for every section of the settings and the mapping, with class, table and column pickers, validation messages, and "try this on sample documents" for patterns, cues and anchors. Absorbs the structured settings form left over from CR-9. *Done when:* the `retail-returns` preset can be rebuilt from an empty configuration in the UI without typing JSON, and gives the same output.
  - **Mapping half (2026-10-07, uncommitted; not seen in a browser):** a **Mapping** tab on the Crawler page (`features/crawler/MappingTab.tsx`, `MappingForm.tsx`): versions with Edit and Activate, the shipped example, a Form/JSON switch over one document, Check (the problems list from `mappings:validate`), Check and save, Activate.
    - *Classes and tables:* per class its table, key, other identifiers, name columns and name templates, with tables and columns offered from the published semantic model and the columns Helios DS marked as identifiers suggested for the key.
    - *Case records:* per anchor its identifiers (column, whole value or last digits, number or text, which class it names), joined classes with their relationship, the date (a column, or a key into a table of dates), classes kept on a joined table, and related records with their own joins and date.
    - *Relationships and thresholds.* Attribute bindings, glossary concepts and resolution scope are not in the form; it keeps them unchanged and says so. They are edited in the JSON view.
    - **Asking the warehouse:** `POST /api/v1/ontology/mappings:probe`, as the signed-in user, nothing written. For a class: row count, number of different keys (a key that is not unique is called out) and five rows of its identifier columns. For a case record: the query a crawl would run, without a document's values, and its first rows. Only a mapping that validates is probed, since names go into the SQL as written. Run against Impala for `Customer` (100,000 rows, key unique), `Store` and the `Return` anchor: each under 2 seconds.
    - **For the pickers:** `GET /api/v1/ontology/mappings/models` (published models, active ontology's classes) and `/mappings/models/{model}` (tables, columns with label, type and profiled role, joins).
    - **Tests:** 4 API tests in `test_index_mappings.py` (the probe against the clinic warehouse of CG-9), 8 UI tests in `MappingForm.test.tsx`.
    - **The "done when" is not met.** Nobody has rebuilt the retail preset and mapping from empty in the UI; the pieces exist, the end-to-end exercise has not been done, and the UI has only been exercised by component tests. Also open: "try this anchor on sample documents" (the probe runs the query without a document's values); the Ontology page still shows the mapping captured at publish time.
  - **Settings half built 2026-10-06 (not viewed in a browser).**
  - **Form editor** (`ui/src/features/crawler/SettingsForm.tsx`): six tabs (Documents, Identifiers, Names, Cases, Claims, LLM crawler) covering every section of the settings; rules as cards, word lists as chips, class and claim pickers from the active ontology. It edits the same document as the JSON view, which remains under a Form/JSON switch. Versions and presets moved into a collapsible block above the editor.
  - **Try-it panel** (`TryPanel.tsx`, `POST /api/v1/crawler/settings:try`, `apps/helios/crawler/tryout.py`): applies one rule of the unsaved settings, with the crawler's own code, to the latest crawl's passages or to pasted text, and shows the matches, the commonest matched values, and cues cancelled by a negation or hedge. Covers patterns, PDF labels, contextual phrases, class cues and claim cues. Nothing is written; the warehouse is not asked.
  - **With nothing crawled yet:** the panel says so and offers pasted text; a crawl with no rules also produces passages to try rules on.
  - **Not covered:** the dictionary settings (ordinary words, thresholds), case linking and resolution tuning cannot be tried, because they need the warehouse dictionary or a whole crawl. Column names are typed, not picked from the semantic model. No difference view between a draft and the active version.
- [~] **CG-9** *(Harness and offline proof done 2026-10-07, uncommitted; a real second corpus configured in the UI is not.)* Second domain. A small corpus in a different industry from Helios-DS (extends C-12), configured in the UI with no code change; the scoring harness no longer assumes the `tpcds` source. *Done when:* it is scored, and the list of known violations is empty.
  - **The harness:** `evaluate.py` no longer names a source or a relationship. A `ScoringProfile` (warehouse database, answer-key predicate to ontology relationship, case classes) is built from the run's own settings and mapping (`profile_for`); the relationship list is the new settings section `evaluation.truth_relationships`, shipped in the retail preset. A run now records the mapping it used (`settings.mapping`: model, version, hash, database). `access_check.py` takes its warehouse table from the active mapping or `--warehouse-table`.
  - **Shapes: 0.** `baselines/crawler_shapes.json` is empty and `test_crawler_second_domain.py` asserts the scan finds nothing.
  - **The second domain** (`tests/second_domain.py`): an outpatient clinic. A DuckDB warehouse (patients, clinicians, visits), a mapping with a `Visit` anchor (text visit code, joins to patient and clinician, a plain date column), settings (three identifier patterns, two PDF labels, a chat export layout unlike the practice corpus's, two claims with their own speakers and cues), five documents (two emails, a chat, two PDF summaries) and a hand-built answer key. Crawled and scored by the unchanged crawler: every mention found and resolved (20 of 20, none to the wrong patient of two named Dana), both cases tied to their visit, `AttendedBy` and `SeenBy` at precision and recall 1.0, 3 of 3 claims with their evidence; a negated prescription and a patient's own "not prescribed" are not claims. With empty settings the same code reads no chat and finds no identifier or claim.
  - **What this is not.** It is an offline test: DuckDB, not Impala; configured in a Python file, **not in the UI**; five documents written by the same person who wrote the configuration, so the perfect scores show the code carries no retail assumption the clinic trips over, and say nothing about accuracy on a real second corpus. The "configured in the UI" and "small corpus" parts of the task (C-12) are still open.
  - **Same output:** settings version 5 (the preset with `evaluation`) activated; full rules crawl `crawl_0b0e7809…` and full LLM crawl `crawl_5475675d…` (301 from cache) both identical to their baselines, scorecards included.
- [x] **CG-10** *(Done 2026-10-07, uncommitted.)* Coverage signals for runs without ground truth: share of passages with no mention, identifier-like strings no pattern matched, cases with no anchor row, claims dropped and why. *Done when:* they show on a run, and a deliberately broken configuration is obvious from them.
  - **Counts on every run** (`crawler/coverage.py`, in `counts`): `segments_without_mentions`, `assets_without_links`, `unmatched_identifiers`, `unknown_labels`, `cases_unresolved`, and claims dropped by reason: `claims_dropped_undefined` (a cue for a claim with no definition), `_speaker`, `_weak_cue`, `_no_subject`, `_no_object`.
  - **Examples** in a new table `helios_index.coverage` (signal, value, count, example; top 20 per signal): identifier-like strings no pattern matched, grouped by shape (`AA-9999`), and PDF lines that look like labels but are not configured.
  - **API and UI:** `GET /crawler/runs/{id}` returns `coverage` and `mapping`; the run panel has a "What the crawl could not use" block (not seen in a browser).
  - **Tests** (`test_crawler_coverage.py`): removing a pattern makes its identifiers appear as unmatched with their shape; removing a claim definition, a speaker rule or a role shows up under the matching reason; an empty configuration is obvious from the counts.
  - **On the development corpus** (`crawl_0b0e7809…`): 154 of 1,003 passages with no mention, 0 documents without a link, 0 unmatched identifiers, 0 unresolved cases, 217 cues dropped as weak and 30 for the speaker, 393 unknown label lines. The top two are real gaps in the retail preset (`Inspection` and `Refund`, 100 documents each). The rest are noise: report titles and table cells such as `Music / rock` read as labels. The label signal needs tightening before it is trusted.
  - **Not done:** no threshold or warning when a signal is high; no comparison between runs.

**Order:** CG-0, CG-1, then CG-2 and CG-3 (small, prove the method), CG-4 (the hard one), CG-5, CG-6, CG-7, CG-8, CG-9. CG-10 can start any time after CG-1. CR-L2 (hybrid) should wait for CG-4 and CG-5, since it reuses the case and claim code they replace.

### Later

- [ ] **CR-10** `model_assisted` tier: an LLM for leftover contextual references and borderline candidates, through `helios_core.llm`, measured as the gain over CR-5. *Done when:* scored with and without it on the same corpus.
- [ ] **CR-11** *(Built 2026-10-05 and 2026-10-06, commit `83ac412`; open only for scoring the golden questions.)* Retrieval for question answering: segment embeddings in LanceDB, plus an MCP tool (`search_evidence`, `explain`) that joins structured and unstructured results. Golden questions are then answered end to end, not just at the retrieval level.
  - **Embeddings:** `shared/helios_core/index/evidence.py`. The crawl embeds its passages at the end of a run (`all-MiniLM-L6-v2`, local) into `state/evidence.lance`; each source keeps its latest crawl only. A passage is a whole email (body under its subject and sender), a window of six chat messages, or a PDF page; subject and sender lines are not passages. A failed embedding is counted on the run (`embedding_failed`) and does not fail it. Development corpus: 334 passages from 1,003 segments.
  - **Tools:** `search_evidence` (by meaning, one passage per document, with the linked entities and their warehouse keys) and `entity_claims` (replaces the planned `explain`). Both need `query.execute` and are audited. The MCP server loads the model at startup.
  - **Joining:** the assistant uses a passage's keys as filters in `run_query`. Seen working once on Mistral medium (three customers from emails, then their return totals).
  - **UI:** passages and claims are shown under the answer in Talk; the answer path has a document lane and a link for keys carried into the query (`console/trace_documents.py`).
  - **Dependencies:** `lancedb` 0.39.0 and `sentence-transformers` 6.1.0, installed by hand; optional.
  - **Open:**
    - the 60 golden questions have not been run, so there is no end-to-end score;
    - document text is not filtered per user (needs DS-7);
    - the delegated credential lasts 60 seconds, so a question whose tool calls take longer fails.
- [ ] **CR-12** Images (after the text crawler meets its targets; Helios-DS Phase 5).

---

### Handover: rebuild the environment from nothing (after the first end-to-end crawl)

*Added 2026-10-01.* Today, a newcomer with a different data lake could not easily rebuild the system:
- **Scattered steps:** each database has its own script or command, and nothing gives the order.
- **Hard-coded values:** several scripts hard-code this environment's bucket and paths.
- **Undocumented manual work:** users, Ranger and RAZ policies, and data connections are described only inside task notes.

- [ ] **HO-1** Runbook, `docs/environment-setup.md`: from an empty environment to a working system, in order, each step with a verification command:
  - TPC-DS;
  - the `helios_ds` and `helios_ground_truth` tables and the crawler view;
  - `helios_index`;
  - machine users, Ranger and RAZ grants, and data connections (exact grants, as tables);
  - Workbench projects, runtimes, Jobs and Applications, with every environment variable;
  - generating, reviewing and approving a corpus (and how to confirm it matches a recorded one, by manifest and question-set hashes; dataset IDs differ per lakehouse because the source fingerprint uses Iceberg snapshot IDs);
  - publishing and activating the ontology;
  - crawler settings.

  *Done when:* someone with a fresh environment can follow it end to end without help.
- [ ] **HO-2** One parameterised bootstrap command (e.g. `python -m setup.bootstrap`):
  - creates every database and table from the existing DDL modules (Helios-DS, `helios_index`) in the right order;
  - reads the bucket, prefix, data-connection and database names from settings, not code;
  - is safe to rerun;
  - `--check` reports what exists, what's missing, and which manual steps remain.

  *Done when:* it builds an empty lakehouse to the same schema as this one, and `--check` passes.
- [ ] **HO-3** Script the TPC-DS load end to end: generate (DuckDB `dsdgen`), upload to any bucket, stage, convert to Iceberg, compute stats. Generate `stage.sql` and `iceberg.sql` for any bucket instead of hard-coding `applied-ai-buk-d5eff1ab`. *Done when:* the load runs against a different bucket with no edits.
- [ ] **HO-4** Export the Ranger policies (crawler, Helios API and generator identities) and RAZ rules as importable JSON, with the machine-user steps learned in CR-0d. *Done when:* an admin can import them and the access check passes.

## Decisions (made 2026-10-01)

1. **Ontology versions are stored in `helios_index.ontology_versions`** (CR-0c), because index rows reference the version they were made under.
2. **No LLM in the v1 baseline.** CR-3 to CR-6 are deterministic; the `model_assisted` tier is CR-10, measured as a gain over the baseline.
3. **Vectors and LanceDB are deferred to CR-11.** Version 1 retrieval is entity-centric (question → entities → linked segments) through the graph.
4. **The retail pack declares the claim vocabulary** (e.g. `PACKAGING_DAMAGED`, `RETURN_REASON`, `REFUND_REQUESTED`, `REFUND_APPROVED`), as a real retail tenant's pack would.
5. **Crawler runtime:** the existing Helios runtime, with `pypdf` and `rapidfuzz` installed into the project's dependency directory (`pip --target $CDSW_PROJECT_DIR/.helios-python`, as for the console). No custom image.

## Targets (spec, for reference; v1 reports against them, the gates come later)

| Measure | Target |
|---|---|
| Entity extraction (direct / alias) | F1 ≥ 0.95 |
| Relationship extraction | F1 ≥ 0.90 |
| Citation points to intended evidence | ≥ 0.95 |
| Unauthorized evidence exposed | 0 (once C-06 adds access lists) |
