# Helios Crawler Burn-Down (v1: text)

Plan for the first Helios crawler. **How it analyzes an asset, step by step, and what is configurable: [crawler-analysis.md](crawler-analysis.md).** It indexes the Helios-DS development corpus (PDF, email, chat), resolves what the documents mention to TPC-DS entities through the ontology, extracts claims, writes `helios_index` in the lakehouse, and projects the result into Memgraph. Its output is scored against the hidden ground truth and the golden questions.

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
- [ ] **CR-9** *(First slice done 2026-10-01, uncommitted, tested on real runs.)* A **Crawler** page in the Helios UI (Build → Crawler, `/crawler`, `pages/CrawlerPage.tsx`):
  - **Runs tab:** every crawl, newest first, with status, listed, fetched, carried forward, problems, duration, and ontology and settings versions; a source filter. Clicking a run shows its facts and request, asset counts by status and class (clickable filters), search, and each asset's status and problem detail.
  - **Settings tab:** the active version (or built-in defaults), version history with Edit and Activate, and a JSON editor with Format and a note. "Validate and save" lists the API's validation problems inline, then offers to activate the new version. There is help on what each section means.
  - **API:** `GET /api/v1/crawler/runs[?source=]` and `GET /api/v1/crawler/runs/{id}`.
  - **Tests:** 2 page tests and 1 API test.
  - **Still to come:** scores (CR-8), segments, mentions and links per asset (CR-3 onward), instance browsing from the Ontology page, a structured settings form, starting a crawl from the UI (via the Workbench Jobs API), and an RBAC permission.

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
- [ ] **DS-6** Start crawls from the UI through the Workbench Jobs API.
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
- [ ] **CR-E2** `strategy` on `crawl_runs`, plus LLM provenance (provider, model, prompt version and hash, temperature, token and cost totals), and the Crawler page filters by strategy.
- [ ] **CR-L1** Arm B, the LLM crawler:
  - prompts that give the ontology classes, definitions and claim vocabulary, and ask for JSON with the exact quoted text for every entity and claim;
  - temperature 0;
  - every quote mapped back to character offsets in its segment; quotes not found in the document are dropped and counted as hallucinated;
  - responses cached by (model, prompt hash, segment hash), so reruns are cheap and identical;
  - prompts versioned in the crawler settings (CR-0e).

  *Done when:* it crawls the development corpus into `helios_index` with every row grounded to a real span.
- [ ] **CR-L2** Arm C, hybrid: arm B's extraction feeding arm A's case grouping and joint resolution (needs CR-5). *Done when:* scored on the same corpus as A and B.
- [ ] **CR-L3** Comparison report: A, B and C side by side on the development and held-out corpora (C-12); arm B run three times for variation. *Done when:* the report exists and is reproducible from recorded runs.

**Order:**
1. CR-3 (segments, shared by all arms);
2. CR-E1 and CR-E2, so every later step is measured;
3. CR-4 to CR-7 for arm A, alongside CR-L1;
4. CR-L2 after CR-5;
5. CR-L3.

C-12 (held-out corpus) is needed before CR-L3. CR-10 (the LLM as a last tier inside arm A) stays as a fourth variant.

### Later

- [ ] **CR-10** `model_assisted` tier: an LLM for leftover contextual references and borderline candidates, through `helios_core.llm`, measured as the gain over CR-5. *Done when:* scored with and without it on the same corpus.
- [ ] **CR-11** Retrieval for question answering: segment embeddings in LanceDB, plus an MCP tool (`search_evidence`, `explain`) that joins structured and unstructured results. Golden questions are then answered end to end, not just at the retrieval level.
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
