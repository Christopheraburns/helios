# How the Helios Crawler Analyzes an Asset (v1, deterministic)

This document describes, step by step, how the first Helios crawler turns a document into index rows: which asset types it reads, which checks it applies and in what order, what each check produces, and what a user can configure. Version 1 uses **no language model**. Every decision is a rule, a dictionary lookup, a pattern or a structured join, so it is repeatable, explainable and measurable. Where a language model would later help, and how it would be measured, is described at the end.

Related: [crawler-burndown.md](crawler-burndown.md) (tasks CR-2 to CR-10), [ontology/README.md](../ontology/README.md), [ontology-burndown.md](ontology-burndown.md).

**Status (2026-10-01):** design agreed (decisions in section 8); implementation is CR-0e and CR-2 onward.

---

## 1. What the crawler starts from

The crawler works backwards from the data it already has:

| Input | What it provides | Where it lives |
|---|---|---|
| **The asset list** | Each asset's ID, type, MIME type, location, SHA-256 and size. Nothing about which story or template it came from. | `helios_ds.crawlable_artifacts` (READY datasets only) |
| **The asset bytes** | The file itself. | S3, read by the locator above |
| **The ontology** | Which classes exist (Customer, Item, Store, Sale, Return, Reason, Brand…), how they relate, and the claim vocabulary. | `ontology/`, published version in `helios_index.ontology_versions` |
| **The mapping** | For each class, which TPC-DS table and columns identify an instance: key, other unique IDs, display name, aliases. Also which classes to look for per source, and the confidence thresholds per tier. | `ontology/mappings/ossie/tpcds.yaml` |
| **The warehouse** | The real customers, items, stores, sales and returns, and how they join. | `tpcds` (read-only for the crawler) |

The crawler never reads the ground truth, the generation manifests or the Helios-DS base tables. What it learns about a document, it learns from the document and the warehouse.

---

## 2. Which asset types it can analyze

| Asset type | Recognised by | Status in v1 |
|---|---|---|
| **PDF with a text layer** | `application/pdf` and the `%PDF` signature | Supported. Text is extracted per page; there is no OCR. |
| **Email** (`.eml`, RFC 5322) | `message/rfc822` plus parseable headers | Supported: headers, plus the `text/plain` body. HTML-only bodies are converted to plain text (CR-3 option). |
| **Chat thread** (Helios-DS neutral JSON) | `application/json` with `"schema": "helios-ds/chat-thread/1.0"` | Supported: one segment per message. |
| Scanned PDF (no text layer) | A PDF whose pages yield no text | Recorded as an asset, flagged `no_text`, not analyzed (needs OCR: later). |
| Images, audio, video | MIME type | Recorded as assets, flagged `unsupported`, not analyzed. Images follow once the text crawler meets its targets (CR-12). |
| Anything else | | Recorded as `unsupported`. |

The type is decided by the declared MIME type **and** a check of the bytes. If they disagree, the asset is flagged `type_mismatch` and skipped, rather than guessed at. Every skipped asset still gets an `assets` row with its reason, so coverage is visible.

---

## 3. The pipeline for one asset

```
fetch & verify → detect type → parse into segments → normalise
   → find mentions → generate candidates → link documents into cases
   → resolve jointly against the warehouse → resolve contextual references
   → decide links (SameAs / PossiblySameAs) → derive relationships → extract claims → write
```

Steps 1 to 5 look at one asset at a time. Steps 6 to 10 also look across assets, because one real-world case is usually spread over several documents.

### Step 1. Fetch and verify

- Read the bytes by the locator from the view.
- **Check** that the SHA-256 and size equal the recorded values. A mismatch means the asset changed or is damaged: it is recorded as `integrity_failed` and not analyzed.
- **Incremental crawling:** if the asset's content hash equals the one in the last successful crawl of the same source, its rows are carried forward and it is not re-read (CR-2).

### Step 2. Detect the type

MIME type plus content signature, as in section 2. This selects the analyzer.

### Step 3. Parse into segments

Each analyzer splits the asset into **segments**: the smallest parts that evidence can point to. Each segment has its text and a locator in the same format the ground truth uses, so scoring can compare locations exactly.

**Email.**

| Segment | Content | Locator |
|---|---|---|
| `email_header` | From (display name and address), To, Date | `{"header": "From"}` etc. |
| `email_subject` | The subject line | `{"part": "subject", "start", "end"}` |
| `email_body` | The plain-text body, with `\n` line endings | `{"part": "body", "start", "end"}` |

The sender's display name and address are treated as structured fields: they are the strongest identity signal in an email.

**Chat.** The JSON is validated against the declared schema; invalid threads are flagged `invalid`. There is one `message` segment per message, with its sender name and timestamp kept as metadata. Locator: `{"message_id", "start", "end"}`.

**PDF.** One `page` segment per page, with text from the PDF's text layer, in reading order. Two structures are recognised deterministically, because reports use them everywhere:

- **Label / value pairs.** A short line that matches a known field label is followed by its value on the next line. Examples from the corpus: `RMA number` → `RMA-6326426`, `Customer ID` → `AAAAAAAADPDEAAAA`, `Original receipt ticket` → `166147`. The label lexicon is configurable (section 5); a label tells the crawler what *kind* of value follows.
- **Tables.** A run of header lines (`Item ID`, `Description`, `Brand`, …) followed by the same number of value lines is read as one row. Each value is tagged with its column header.

The same labels and headers, in any layout, give the same result. The crawler does not hard-code any one report template.

### Step 4. Normalise

Text is Unicode-normalised (NFC), and whitespace is collapsed for matching only. Every match is mapped back to the **original** character offsets, so a locator always points at the exact bytes in the document.

### Step 5. Find mentions

Four deterministic extractors run over every segment. Each mention records which extractor found it (`extractor`) and why (`extractor_detail`), so every result can be traced.

**5a. Identifier patterns** (regular expressions, configurable):

| Pattern | Proposed class | Example |
|---|---|---|
| TPC-DS business ID: 16 letters A–P (true of all 18,000 item IDs and 100,000 customer IDs at SF1) | Item, Customer or Store (decided in step 6 by lookup) | `AAAAAAAAGLMDAAAA` |
| Email address | Customer | `Wilma.Graham@t.edu` |
| Ticket number in context ("ticket N", "receipt ticket N") | Sale | `ticket 205079` |
| Partial ticket ("receipt ending in NNNN") | Sale (partial key) | `receipt ending in 5079` |
| Return authorization ("RMA-N") | Return (document identifier) | `RMA-3056773` |
| Support case ("CS-N") | Return (document identifier) | `CS-954939` |
| Money and dates | Values for joins and claims, not entities | `$310.40`, `June 14, 2001` |

RMA and case numbers do not exist in TPC-DS. They identify a case *within the documents*, so they are used to link documents (step 7), not to look up rows.

**5b. Gazetteer: names built from the warehouse.** Using the mapping's identifier columns, the crawler builds a dictionary from TPC-DS of every surface form that names an instance:

| Class | Built from (mapping) | Example surface forms |
|---|---|---|
| Customer | display `c_first_name c_last_name`; secondary `c_email_address` | `Angela Raymond`, `Angela.Raymond@7T.edu` |
| Item | display `i_product_name`; secondary `i_item_id` | `ationoughtationation` |
| Store | display `s_store_name`; secondary `s_store_id` | `ese` |
| Brand | alias `i_brand` | `scholarnameless #8` |
| Reason | alias `r_reason_desc` | `Package was damaged` |

Matching is longest-match first, case-insensitive, on word boundaries. Each surface form also gets a **specificity**: how many instances share it, and how common it is as an ordinary word. Some TPC-DS names are short or common English words (stores named `ought`, `able`, `bar`), so low-specificity forms only count as mentions when a context cue sits next to them ("store", "at", a nearby `#ID`), or when step 8 confirms them.

**5c. Composite aliases (templates).** These are names people use that combine columns:

| Template | Class | Example |
|---|---|---|
| `{c_salutation} {c_last_name}` | Customer | `Mrs. Raymond` |
| `{s_city} store` | Store | `Midway store` |
| `{i_color} {i_class} item` | Item | `blanched fragrances item` |

These are often ambiguous, and are kept as candidates to be settled jointly in step 8. In TPC-DS SF1, 9 of the 12 store rows are in Midway, so "the Midway store" alone leaves 9 candidates. Many customers share a salutation and surname. Even store names are not unique: the 12 store rows have 8 distinct names.

**5d. Contextual references.** These are definite noun phrases from a per-class list: "the item", "the product", "this return", "the customer", "that store". They are recorded with no entity yet and resolved in step 9.

### Step 6. Generate candidates and score them

For each mention, the crawler looks up which instances it could refer to, and scores each candidate by tier:

| Tier (`resolved_by`) | Rule | Score |
|---|---|---|
| `exact_key` | The text equals a primary or secondary key of exactly one instance (an item ID, a customer ID, an email address) | 1.0 |
| `alias` | Display name, alias column or composite alias | Based on specificity: 1 / number of instances sharing it, reduced for common words |
| `fuzzy` | Near-match to a display name, for typos and case (rapidfuzz ratio) | Ratio, only at or above `fuzzy_min_score` (0.85) |

A TPC-DS business ID found by pattern is looked up in every class whose mapping lists that column (Item, Customer, Store), and takes the class where it exists.

### Step 7. Link documents into cases

A case is usually spread over several documents: an email, a report and a chat about the same return. The crawler finds those groups itself; the story grouping is hidden. Documents are joined into one **case cluster** when they share a strong identifier:

- the same RMA or support-case number;
- the same ticket number;
- the same customer email address;
- the same TPC-DS business ID together with a date within a window (configurable).

Clusters are computed with union-find, so A–B and B–C put A, B and C together. The resulting clusters are recorded, and scored against the ground truth's story grouping as a measure in their own right.

### Step 8. Resolve jointly against the warehouse

This is the main new method of v1. Within a case cluster, the candidates for each class are combined and checked against TPC-DS: **which warehouse rows are consistent with everything the documents say?**

Example (case RMA-6326426):
- The chat says "Angela.Raymond@7T.edu" (Customer, exact key) and "the Midway store" (Store, ambiguous: several stores are in Midway).
- The PDF says ticket `166147`, item `AAAAAAAAFCOBAAAA` and store `#AAAAAAAAEAAAAAAA`, with return date June 14, 2001.
- The crawler queries `store_returns`, joined to its keys and `date_dim` following the published model's relationships, for rows where customer, item, ticket and store match. Exactly one row matches, so:
  - the Return and Sale are identified;
  - "the Midway store" is resolved to that row's store (`resolved_by = joint`);
  - the case is anchored to a warehouse record.

Rules:
- **Join paths come from the published Ossie model** (`store_returns__sr_customer_sk__customer` and so on), not from hand-written SQL. The ticket-number join between returns and sales, which TPC-DS has no foreign key for, is the one declared exception (`ReturnOf`).
- **A unique match promotes** every participating mention to SameAs, with score from the strength of the constraints.
- **No match, or several, promotes nothing.** Candidates stay as they are.
- **Partial keys** ("receipt ending in 5079") are constraints too: ticket numbers ending in 5079.
- Queries run against `tpcds` with the crawler's read-only identity, batched per cluster.

### Step 9. Resolve contextual references

"The item", "the product", "this return" and so on are linked when the asset or its cluster contains **exactly one** resolved instance of that class (`resolved_by = contextual`). If there are none, or more than one, the reference stays unresolved. No guessing.

### Step 10. Decide links

| Outcome | When |
|---|---|
| **SameAs** | The best candidate's score is at or above its tier's threshold (alias 0.92, fuzzy 0.85; exact key and unique joint matches always), and no other candidate is close |
| **PossiblySameAs** | A candidate is plausible but below threshold, or tied. The top candidates are kept for review or for the later LLM tier. |
| No link | No candidate |

Every link records its tier, score and evidence segments. Precision over recall: a wrong SameAs is worse than a missing one.

### Step 11. Derive relationships

- **Mentions** (asset → entity): every SameAs link.
- **About** (asset → entity): the entity the asset is mainly about, by a fixed rule: mentioned in the subject or title, or most often, with ties broken by class priority (Return > Item > Customer).
- **Structural relationships** (entity → entity) come from the warehouse row found in step 8, not from text: ReturnOf, Contains, LocatedAt, PartyTo, HasReason (`resolved_by = structured`).

### Step 12. Extract claims

Claims use the predicates the retail pack declares: `PACKAGING_DAMAGED`, `RETURN_REASON`, `REFUND_REQUESTED`, `REFUND_APPROVED`.

- **Cue lexicon per predicate** (configurable): e.g. `PACKAGING_DAMAGED`: crushed, torn, dented, damaged packaging, scuffed…; `REFUND_APPROVED`: refund approved, approve it, refund queued…
- **Unit:** one sentence or chat message. A claim needs a cue *and* a resolved subject in the same unit or cluster: the Item or Return for damage; the Customer and Return for refund requests.
- **Negation and hedging guard:** "not damaged", "no damage", "if it was approved" block the claim.
- **Labeled fields count as cues:** "Reason code on file: Package was damaged" gives `RETURN_REASON` with the Reason entity as its object.
- **Evidence:** the sentence or message, with its exact locator.
- **Confidence:** fixed per cue strength (exact phrase, then synonym, then weak cue).

**Overfitting risk.** Cue lexicons can be tuned to one generator's phrasing. To keep the measurements honest, lexicons are written from general retail language, and accuracy is also measured on a **held-out corpus** generated with a different seed and phrase banks. A large gap between the two exposes over-tuning.

### Step 13. Write

All rows for the run go to `helios_index`, each stamped with the crawl run and ontology version: assets, segments, mentions, entities, links, relationships, claims and evidence. A failed run's rows are removed. The run's settings (section 5) are stored with it, so every result can be reproduced and explained.

---

## 4. Worked example: one case end to end

The documents (from the development corpus):

- **Email** from `Wilma Graham <Wilma.Graham@t.edu>`, subject "Problem with my ableeseantiantiought (ticket 205079)", mentioning `blanched fragrances item (AAAAAAAAGLMDAAAA)`, "receipt ending in 5079", "Package was damaged" and RMA-3056773.
- **PDF return report** for RMA-3056773, with labeled fields (store, ticket, customer ID) and an item table.
- **Chat** about RMA-3056773.

What the crawler does:

| Step | Result |
|---|---|
| 5 | Mentions: the customer's name and email (header and signature), the product name and ticket (subject), the item ID, colour + class alias and receipt tail (body), the reason (body), the RMA |
| 6 | The item ID and email address are exact keys (1.0). "Wilma Graham" is an alias whose score depends on how many customers share the name. "blanched fragrances item" is an ambiguous composite alias. |
| 7 | Email, PDF and chat share RMA-3056773, so they form one case cluster |
| 8 | `store_returns` filtered by customer, item and ticket 205079 (consistent with "ending in 5079") gives exactly one row: the Return, Sale and Store are identified, and the ambiguous aliases are promoted (`joint`) |
| 9 | "the product" and "the item" link to the one resolved Item |
| 11 | Mentions edges; About = the Return; ReturnOf, Contains and HasReason from the row |
| 12 | `PACKAGING_DAMAGED` ("Box arrived torn; the product scratched."), `RETURN_REASON` ("The return slip says: Package was damaged."), `REFUND_REQUESTED` ("When will the refund post?") |

---

## 5. What is configurable, and by whom

**Decided 2026-10-01:** every setting lives in the lakehouse, so it can be changed without touching code, through the Helios API and a settings editor in the Helios UI. There are two kinds:

- **Crawler settings (operator):** stored as versions in `helios_index.crawler_settings`, plus an activation log, like ontology versions. The API validates each version against a typed schema before saving it. Versions are immutable: an edit saves a new version, and activating it takes effect from the next crawl. Each crawl run records the settings version and hash it used, so every result can be reproduced and explained.
- **Identity rules (ontology owner):** how each class is identified (keys, IDs, display, alias columns, alias templates), which classes to look for per source, and the tier thresholds. These are part of the governed ontology mapping, reviewed and **published** to the lakehouse with each ontology version (`helios_index.ontology_versions`). The crawler reads them from the published version, never from files. For now they are edited as YAML and published through the API; editing them in the UI can come later through the same publish step, so they stay reviewed.

| Setting | Scope | Stored in | Edited by |
|---|---|---|---|
| Which source to crawl (dataset, S3 prefix) | per run | Crawl request (recorded in `crawl_runs.settings`) | Operator, when starting a crawl |
| Analyzers on or off | per asset type (MIME) | Crawler settings `analyzers` | Operator |
| Parser options: PDF page limit; which email headers count for identity; HTML handling | per asset type | Crawler settings `analyzers.<type>` | Operator |
| Identifier patterns | per class | Crawler settings `patterns` | Operator |
| PDF field-label lexicon (label → kind of value) | per label | Crawler settings `pdf.labels` | Operator |
| Contextual phrases ("the item"…) | per class | Crawler settings `contextual` | Operator |
| Case-linking identifiers and date window | per source | Crawler settings `cases` | Operator |
| **Claim cue lexicons and negation words** (decision 3) | per predicate | Crawler settings `claims` | Operator. The predicates themselves come from the retail pack. |
| Which classes to look for | per source pattern | Ontology mapping `resolution.scope` | Ontology owner (published) |
| How each class is identified: key, IDs, display, alias columns | per class | Ontology mapping `identifiers` | Ontology owner |
| **Composite alias templates** (decision 2; added 2026-10-01) | per class | Ontology mapping `identifiers.alias_templates`, e.g. `"{c_salutation} {c_last_name}"`, `"{s_city} store"`, `"{i_color} {i_class} item"` | Ontology owner |
| Confidence thresholds per tier | per mapping | Ontology mapping `resolution.thresholds` | Ontology owner |
| Join paths for joint resolution | automatic | Published Ossie model plus mapping relationships | (derived) |

So: **yes**, analysis is configurable by asset type (which analyzers run, with which options) and by source (which classes to look for). It is all stored in the lakehouse and edited without code changes, and every crawl records exactly which settings and ontology version it used.

---

## 6. What is measured at each step (CR-8)

| Step | Measure (against the hidden ground truth) |
|---|---|
| 3 | Every ground-truth evidence passage falls inside a crawler segment |
| 5 | Mention precision and recall, by tier (direct / alias / contextual) and class |
| 6–10 | Resolution accuracy by tier; SameAs precision; how many references stay PossiblySameAs |
| 7 | Case clusters compared with the hidden story grouping (pairwise precision and recall) |
| 11 | Relationship precision and recall |
| 12 | Claim precision and recall; evidence locator agreement |
| All | Golden questions at the retrieval level; no-answer questions must have no linked documents |

Each run's scores show which deterministic step loses accuracy, which is exactly what tells us where an LLM would earn its place.

---

## 7. Where a language model fits later (CR-10), and how it is kept honest

A model is added only as a separate tier (`model_assisted`) for what the rules leave undecided:

- **contextual references** that rules leave unresolved (several candidates in the cluster);
- **PossiblySameAs** links: choosing among the kept candidates, never inventing new ones;
- **claims** in sentences with no cue-lexicon match.

Constraints:
- It never overrides `exact_key`, `joint` or `structured` results.
- It can only choose candidates the deterministic steps produced, or answer "none".
- Its links are SameAs only at or above `model_assisted_min_confidence` (0.80).
- The same corpus is scored with and without it, so its gain (or harm) is measured, not assumed.

---

## 8. Decisions (made 2026-10-01)

1. **Settings live in the lakehouse,** editable through the API and a Helios UI editor, with no code changes. Crawler settings are versioned in `helios_index.crawler_settings`; identity rules are part of the published ontology mapping (section 5).
2. **Alias templates are in the mapping:** `identifiers.alias_templates`. Done 2026-10-01 for Customer, Store and Item, with every referenced column checked against the published model.
3. **Claim cue lexicons are operator settings,** in the crawler settings.
4. **Held-out corpus: yes.** A second, smaller Helios-DS dataset with a different seed, scored alongside the development corpus to expose over-tuning. A different seed changes which customers, items and stores appear, and which phrase variants are chosen; it does not change the phrase banks. So a follow-up variant with alternate phrase banks would test cue-lexicon over-tuning more strictly (Helios-DS task C-12).
5. **PDF parsing with `pypdf`** for v1. Layout-aware extraction only if table reading proves unreliable.
