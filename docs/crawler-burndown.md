# Helios Crawler Burn-Down (v1: text)

Plan for the first Helios crawler. It indexes the Helios-DS development corpus (PDF, email, chat), resolves what the documents mention to TPC-DS entities through the ontology, extracts claims, writes `helios_index` in the lakehouse, and projects the result into Memgraph. Its output is scored against the hidden ground truth and the golden questions.

**Status (2026-10-01):** plan only. No crawler, resolver, embedding or `helios_index` code exists. Inputs are ready:
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
- [ ] **CR-0c** Store ontology versions in `helios_index.ontology_versions` (decision 1). Publishing writes the version there, and the gateway rebuilds from it. Bring `docs/ontology-burndown.md` up to date at the same time: O-2 to O-5 are committed but unchecked.
- [ ] **CR-0d** Access for the Helios project:
  - read `helios_ds.datasets` and `helios_ds.artifacts` through a **view** that exposes only the neutral columns (`artifact_id`, `dataset_id`, `artifact_type`, `mime_type`, `source_locator`, `sha256`, `size_bytes`, `semantic_timestamp`), and only for READY datasets;
  - an S3 data connection reading `helios-db/source/datasets/`;
  - no access to `helios_ground_truth` or `_manifests/`. This is S-05 from the Helios-DS burn-down, done here.

  *Done when:* from the Helios project, the view is readable, and reading `helios_ground_truth.claims` or a manifest object is denied.

### Index and connector

- [ ] **CR-1** `helios_index` schema, in a shared module (`shared/helios_core/index/`) that replaces the documentation-only copy in `helios_ds/schemas.py`, with the same pydantic → Impala DDL generation as Helios-DS. Tables:
  - `crawl_runs`;
  - `assets` (asset version = SHA-256), `segments`;
  - `mentions` (surface form, locator, proposed type);
  - `entities` and `entity_links` (SameAs / PossiblySameAs, `resolved_by`, score, evidence segments);
  - `relationships`;
  - `claims` and `claim_evidence`;
  - `ontology_versions`.

  Every row carries `crawl_run_id` and `ontology_version`. *Done when:* `init-tables` creates them in Impala and DDL drift is tested.
- [ ] **CR-2** `helios_ds_s3` connector: enumerate READY datasets and their artifacts through the CR-0d view, fetch bytes by locator, verify SHA-256, and skip unchanged assets (incremental by asset version). *Done when:* it lists the 301 assets of the development corpus and a second crawl fetches nothing.

### Understanding the text

- [ ] **CR-3** Analyzers to segments: email (headers and body), chat (one segment per message), PDF (pages via `pypdf`, keeping text order). *Done when:* every development-corpus asset is segmented, and every ground-truth evidence locator falls inside a crawler segment. That last check runs in the evaluation project.
- [ ] **CR-4** Mention extraction, built backwards from the data:
  - **identifier patterns:** item and customer IDs, ticket numbers, RMA and case numbers, email addresses, "receipt ending in NNNN", money and dates;
  - **a TPC-DS gazetteer** built from the mapping's display, secondary and alias columns: product, store, customer, brand and reason names, salutation plus surname, colour plus class, "<city> store";
  - **contextual references** ("the item", "this return"), which are recorded with no entity yet.

  *Done when:* direct-tier mention recall is ≥ 0.95 on the development corpus (scored by CR-8).
- [ ] **CR-5** Resolution, recording `resolved_by` and evidence on every link:
  - **exact_key:** identifiers;
  - **alias:** gazetteer aliases, scored, with the alias threshold;
  - **fuzzy:** rapidfuzz on names, with the fuzzy threshold;
  - **joint:** settle ambiguous candidates with structured joins among the entities resolved in the same asset. For example, "the Midway store" plus a resolved customer and item gives the `store_returns` row that names one store. This is the main new method of v1.
  - **contextual:** attach "the item" and similar to the asset's resolved entity of that type when there is exactly one.

  *Done when:* alias-tier resolution accuracy is ≥ 0.90 and nothing below threshold is linked as SameAs.
- [ ] **CR-6** Claims: a predicate vocabulary declared in the retail pack (e.g. `PACKAGING_DAMAGED`, `RETURN_REASON`, `REFUND_REQUESTED`, `REFUND_APPROVED`), recognised by phrase rules over segments, with subject and object taken from the resolved entities and evidence spans recorded. *Done when:* claim precision is ≥ 0.90 and recall ≥ 0.80 on the development corpus.

### Projection, scoring and visibility

- [ ] **CR-7** Projection into Memgraph:
  - gateway endpoints to load one crawl run's entities, assets, segments, mentions, links and claims (whitelisted labels, as for the ontology), linked to the ontology classes they instantiate;
  - a Helios API client for the gateway (`HELIOS_GRAPH_GATEWAY_URL` plus token). The API currently reads the graph store files directly.

  *Done when:* deleting Memgraph's data and reloading from `helios_index` gives the same graph counts.
- [ ] **CR-8** Scoring in Helios-DS-Evaluation (its own project; reads ground truth and `helios_index`):
  - mention precision and recall by tier and entity type;
  - entity-resolution accuracy by tier;
  - claim precision and recall;
  - evidence-locator agreement;
  - golden questions at the retrieval level: for each question, are its required entities linked and its required evidence segments indexed? No-answer questions must have no linked documents.

  Results are written to a scores table and shown as a report. *Done when:* a scored report for crawl run × development corpus exists and is reproducible.
- [ ] **CR-9** Visibility in the Helios UI: a crawl-runs page (run, counts, scores) and instance browsing on the Ontology page (from a class to its entities, then their mentions and evidence). *Done when:* you can go from `Customer` to a customer's linked documents and the passages that mention them.

### Later

- [ ] **CR-10** `model_assisted` tier: an LLM for leftover contextual references and borderline candidates, through `helios_core.llm`, measured as the gain over CR-5. *Done when:* scored with and without it on the same corpus.
- [ ] **CR-11** Retrieval for question answering: segment embeddings in LanceDB, plus an MCP tool (`search_evidence`, `explain`) that joins structured and unstructured results. Golden questions are then answered end to end, not just at the retrieval level.
- [ ] **CR-12** Images (after the text crawler meets its targets; Helios-DS Phase 5).

---

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
