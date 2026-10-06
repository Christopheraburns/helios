# A Fully Configurable Crawler: Plan

**Status (2026-10-06):** CG-0 to CG-4 and CG-6 are done; see the burn-down for what each delivered. The anchor example below was written before the build: the built form differs in detail (`join_on` in place of `on`; lookups name the columns that feed them, not pattern names; there is no `document_ids` or `precedence` key). The shipped mapping, `ontology/mappings/ossie/tpcds.yaml`, is the accurate example. Tasks are in [crawler-burndown.md](crawler-burndown.md) under "Configurable crawler" (CG-0 to CG-10).

## Goal

No knowledge of retail, returns or TPC-DS anywhere in the crawler's code. Everything that makes the rules crawler accurate on the practice corpus becomes configuration that an end user can set in the UI. With that configuration in place, the crawler produces **the same rows and the same scores as today** on the development corpus, deterministically.

Making the configuration easy to produce (simpler screens, or an LLM that proposes it) is deliberately out of scope. It comes after.

## Principle: mechanisms in code, shapes in configuration

The crawler keeps its *mechanisms*: pattern matching, dictionary lookup, grouping documents into cases, querying the warehouse for the one row a case describes, cue-phrase claims, tiers and thresholds. What moves out is every *shape*: which classes exist, how they are identified, what a "case" is, which claims exist and who may make them.

A simple test decides which is which: **could a customer in another industry need a different value?** If yes, it is configuration.

## How the proof works

Two gates, both automatic:

1. **Same output.** The retail behaviour is re-expressed as a configuration preset. Crawling the development corpus with the preset must give the same index rows (same IDs, same content) and the same scorecard as the current code. This is checked after every task, so accuracy cannot drift during the work.
2. **No shapes in code.** A test fails if any crawler source file contains a class name, claim name, table name, column name or domain word from the retail preset. It starts with today's violations listed as known and the list must reach zero.

A third gate proves the point of the exercise: a **second domain** configured with no code change (CG-9).

## Inventory: what is retail-specific today

From an audit of `apps/helios/crawler/` on 2026-10-06.

### Finding mentions (`mentions.py`, `gazetteer.py`)

| Today, in code | What it assumes | Becomes |
|---|---|---|
| `CUES` for Store, Item, Customer | Which words near a name confirm its class ("store", "item", a salutation) | `class_cues`: per class, phrases and patterns, and the window size |
| `_TPCDS_ID` (16 letters A to P) | The business key format | Removed; key formats exist only as identifier patterns |
| `_email_header`: sender's display name is a `Customer` | Who writes emails | `header_rules`: per header and field, the class and columns it names |
| `COMMON_WORDS` | Ordinary words that are also names, including TPC-DS store names and retail vocabulary | `ordinary_words`: a language base list plus customer additions |
| `SHORT_TOKEN`, `MAX_SHARED_INSTANCES`, `MAX_FORM_TOKENS`, `CUE_WINDOW` | Name-likeness thresholds tuned on TPC-DS | `dictionary` thresholds |

### Grouping and resolving (`cases.py`, `resolution.py`)

| Today, in code | What it assumes | Becomes |
|---|---|---|
| `RETURN_CLASS`, `SALE_CLASS`, `JointSchema` | A case is one return of one sale | **Record anchors** (below) |
| `build_query`: return table joined to sale table on paired keys | The two fact tables and how they join | Anchor `related_records` and `joins` |
| `Constraints.tickets`, `tails`; `MOD(ticket, 10^n)` | A ticket number, and a "receipt ending in" partial number | Anchor `lookups`: pattern → column, with a match rule (equals, ends with) |
| `DATE_JOINS` (`date_dim` through surrogate keys) | How TPC-DS stores dates | Anchor `dates`: a column, or a join to a date table |
| `promote`: `"ticket" in k`, return wins over sale | Column naming and precedence | Anchor `lookups` and `precedence` |
| `_class_of_table`: matches `sr_item_sk` to `i_item_sk` by suffix | TPC-DS column naming | Explicit `joins`: column → class |
| `DOCUMENT_ID_NAMES` (`rma`, `case`); document IDs make a `Return` | Which IDs documents carry and what they identify | `key_name` on the pattern; anchor `document_ids` |
| `RETURN_OF`, structural edges from the matched row | Relationship names | Edge names on anchor `joins` and `related_records` |
| `CLASS_PRIORITY` | Which class a document is "about" when unsure | `about.class_priority` |
| `about_target`: `email_subject` is the title | Where a document's title is | `about.title_segments` |
| `source_schema="tpcds"` | The database name | From the mapping |
| `MAX_CANDIDATES`, `CLOSE`, `LOW_SPECIFICITY_FACTOR`, `TOP_POSSIBLE`, `ROW_LIMIT`, `MAX_HUB_ASSETS` | Tuned thresholds | `resolution` thresholds |
| `DATE_FORMATS` | Four English date formats | `dates.formats` |

### Claims (`claims.py`)

| Today, in code | What it assumes | Becomes |
|---|---|---|
| `SHAPES` (four claim types and their subject and object classes) | The claim vocabulary | `claims.predicates`: subject class, object class or value |
| `STRUCTURAL_EDGES`, `UNIT_FIRST` | How to find a claim's subject and object from the case | Per predicate role: an ordered list of where to look |
| `STAFF_ONLY`, `CUSTOMER_ROLES`, `STAFF_SEGMENTS`, `voice()` | Who is speaking, and that only staff approve refunds | `speakers`: rules that assign a role; per predicate, which roles may assert it |
| `GENERIC_WORDS`, `cue_strength()` | Which cue words are weak | Strength set on each cue |
| `CONFIDENCE` | Confidence per strength | `claims.confidence` |
| `ABBREVIATIONS`, `_SENTENCE_END`, `SENTENCE_SEGMENTS`, `SKIPPED_SEGMENTS`, `NEGATION_WINDOW`, `GAP_WORDS` | English sentences; which parts of a document carry claims | `text` rules |

### Reading documents (`analyzers.py`)

| Today, in code | What it assumes | Becomes |
|---|---|---|
| Chat JSON fields: `messages`, `sender`, `role`, `participants`, `thread_id`, `timestamp` | One chat export layout | `chat_layouts`: field paths per layout |

### Also affected

- **The LLM crawler** reads the claim shapes from `claims.SHAPES`; it will read the same configuration.
- **The default settings** are the retail rules. They become a named preset, and the engine's own defaults become empty.
- **The scoring harness** assumes the `tpcds` source name and warehouse keys. It is test tooling, not the crawler; it is listed in CG-9 because a second domain needs it.

## The new configuration

### Record anchors: the generalisation of "a return of a sale"

This is the piece that carries most of the accuracy (alias resolution went from 53% to 99.6% with it), and the hardest to generalise.

An **anchor** is a class whose warehouse row a group of documents is about: a return, an insurance claim, a work order, a hospital visit. An anchor says how documents point at that row and what the row tells us once found.

```yaml
anchors:
  - class: Return
    table: store_returns
    key: [sr_item_sk, sr_ticket_number]
    document_ids: [return_authorization, support_case]   # pattern names; a document carrying one belongs to this anchor
    lookups:                                              # how values in documents constrain the row
      - {pattern: ticket_number, column: sr_ticket_number, match: equals}
      - {pattern: receipt_tail,  column: sr_ticket_number, match: ends_with}
    joins:                                                # what the row names
      - {column: sr_customer_sk, class: Customer, edge: PartyTo}
      - {column: sr_item_sk,     class: Item,     edge: Contains}
      - {column: sr_store_sk,    class: Store,    edge: LocatedAt}
      - {column: sr_reason_sk,   class: Reason,   edge: HasReason}
    colocated:                                            # classes that live on a joined table
      - {via: Item, class: Brand}
    dates:
      - {role: occurred, join: {column: sr_returned_date_sk, table: date_dim, key: d_date_sk, value: d_date}}
    related_records:
      - class: Sale
        table: store_sales
        key: [ss_item_sk, ss_ticket_number]
        on: [[sr_item_sk, ss_item_sk], [sr_ticket_number, ss_ticket_number]]
        edge: ReturnOf
        joins: [...]
        dates: [...]
    precedence: anchor_first            # when a name could mean the anchor's or the related record's
    query:
      min_constraint_kinds: 2           # or any exact lookup
      row_limit: 5
```

Bounds, so this stays a configuration and does not become a query language:

- One anchor row, optional related records joined on stated key pairs, and one level of joins to other classes. No free-form SQL, no expressions.
- Every table and column must exist in the published semantic model, and every class in the active ontology. Checked on save.
- Several anchors may be configured; a case is tried against each in order.
- With no anchor configured, the crawler still groups documents and resolves each mention by its own tier. That is today's behaviour when the warehouse query is unavailable.

### Claims

```yaml
claims:
  predicates:
    REFUND_APPROVED:
      subject: {class: Return,   find: [anchor]}
      object:  {class: Customer, find: [in_unit, {anchor_edge: PartyTo}, single_in_document, single_in_case]}
      speakers: {allowed: [staff], unknown_needs: strong}
      cues:
        - {phrase: "refund * approved", strength: strong}
        - {phrase: "approved",          strength: weak}
      weak_cue_needs_subject_in_unit: true
  confidence: {strong: 0.95, medium: 0.85, weak: 0.7}
speakers:
  roles:
    - {role: customer, when: {segment: message, field: role, in: [customer, buyer, shopper]}}
    - {role: staff,    when: {segment: message}}
    - {role: customer, when: {segment: email_body, author_class: Customer}}
    - {role: staff,    when: {segment: page}}
```

`find` is an ordered list from a fixed menu: `in_unit`, `anchor`, `anchor_edge`, `single_in_document`, `single_in_case`. The menu is the mechanism; the order per role is the shape.

### Where configuration lives

| Concern | Home | Today |
|---|---|---|
| What documents look like and say: patterns, labels, cues, claims, speakers, text rules, layouts | **Crawler settings** (versioned, in `helios_index`) | Has an API and a JSON editor |
| How classes relate to warehouse tables: identifiers, anchors, joins, dates, thresholds | **The ontology mapping** | A file in the repository; no API, no editor |

The second row is a gap: "everything configurable in the UI" requires the mapping to become a stored, versioned document with an API, like the settings. This overlaps the ontology authoring side quest (OA-2, OA-7), which needs the same thing. CG-6 does it once for both.

## Plan

Order matters: the safety net first, then the smallest moves, with the hardest (anchors) once the pattern is proven.

| Task | What | Proof |
|---|---|---|
| **CG-0** | Safety net: a recorded baseline of the development corpus (row IDs, content hashes, scorecard) and the fixture crawls; the "no shapes in code" test with today's violations listed | Baseline reproduced twice from current code |
| **CG-1** | Settings schema 2: engine defaults become empty; today's rules become the `retail-returns` preset; stored version 1 settings read as preset plus their edits | Same output; an empty configuration crawls without error and finds only segments |
| **CG-2** | Mentions: class cues, header rules, ordinary words, dictionary thresholds; remove the built-in ID format | Same output; violations list shrinks |
| **CG-3** | Document identity: key names on patterns, date formats, "about" rules, resolution thresholds, source name from the mapping | Same output |
| **CG-4** | Record anchors: replace the return-and-sale code with the anchor model (lookups, joins, dates, related records, precedence) | Same output, including identical warehouse queries for every case |
| **CG-5** | Claims: predicates, role finding, speakers, cue strengths, text rules; the LLM crawler reads the same shapes | Same output for both crawlers |
| **CG-6** | Mapping as a versioned document with an API (shared with OA-2): identifiers, anchors and thresholds; validation against the semantic model and ontology | A mapping edited through the API drives a crawl; the repository file is no longer read at crawl time |
| **CG-7** | Chat layouts and any remaining analyzer assumptions | Same output; a second chat layout is read by configuration alone |
| **CG-8** | UI: a structured editor for every section, with class, table and column pickers, validation messages, and "try this on sample documents" for patterns, cues and anchors | Each inventory row can be set without typing JSON; the preset can be rebuilt from an empty configuration in the UI |
| **CG-9** | Second domain: a small corpus in a different industry (Helios-DS), configured in the UI with no code change; scoring harness freed from the `tpcds` source name | Scored; the violations list is empty |
| **CG-10** | Coverage signals for runs without ground truth: share of passages with no mention, unmatched identifier-like strings, cases with no anchor row, claims dropped and why | Visible on a run; a deliberately broken configuration is obvious from them |

## What "the same accuracy" does and does not mean

- **On the development corpus: guaranteed**, by the same-output gate. The preset is today's behaviour, moved.
- **On a new customer's data: only as good as the configuration.** The crawler will be *able* to reach the same accuracy; someone still has to supply the patterns, labels, cues, anchors and claim shapes, and get the mapping right. That is roughly 100 settings for the retail case. This is the complexity you expect, and what the later simplification work is for.
- **Not everything can be configured into existence.** Languages other than English need word lists and sentence rules that someone must write; scanned documents need OCR; formats beyond PDF, email, chat and text need new analyzers. Those are mechanisms, and stay development work.

## Risks

1. **Anchors may not fit every domain.** The model is one row, related records and one level of joins. A domain whose "case" spans several unrelated records, or has no single row, will need the model extended. CG-9 is where this is found out; choose the second domain to stress it.
2. **Same-output is strict.** Row IDs are derived from content, so any change in order or tie-breaking shows up. That is the intent, but expect CG-4 to take the longest.
3. **Configuration can now be wrong in many more ways.** Validation on save (CG-6, CG-8) and coverage signals (CG-10) matter as much as the editors.
4. **The settings hash changes**, so the first crawl after each schema change re-analyses every document.
5. **Two homes for configuration** (settings and mapping) is a choice. One combined document would be simpler to explain; two keeps warehouse structure with the ontology, where the Ontology page already shows it. Worth deciding before CG-6.
