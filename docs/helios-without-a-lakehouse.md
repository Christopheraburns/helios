# Helios Without a Lakehouse: Analysis and Point of View

**Status (2026-10-06):** analysis and proposal. Nothing here is built, and no tasks have been added to a burn-down.

## The question

Helios should serve three kinds of customer:

1. a lakehouse **and** unstructured data (what Helios was designed for);
2. a lakehouse only;
3. unstructured data only.

Can Helios crawl and index documents, and be worth using, when there is no semantic model and no warehouse mapping behind the ontology?

## Short answer

Yes, but not by removing pieces. Today the warehouse does three jobs for the document path, and each needs a replacement:

| What the warehouse provides today | Replacement without one |
|---|---|
| **A dictionary** of what exists (customers, items, stores) | The documents themselves, plus optional reference lists the customer uploads |
| **Identity**: an entity *is* a warehouse row | Entities defined by the identifiers and names found in documents, grouped across documents |
| **Somewhere to count**: totals and trends come from warehouse tables | What Helios extracts, published as tables with their own semantic model |

The third is the one that changes the answer to "is it of much value". A documents-only customer does not have to stop at search. Helios can turn their documents into data and then answer counting questions over it with the machinery that already exists.

## Where the lakehouse is assumed today

Checked in the code, not taken from the design.

**Hard stops (nothing runs):**

- **The crawler refuses to start without Impala.** It needs it for the ontology version, the crawler settings and the index tables (`apps/helios/crawler/__main__.py`). A DuckDB index exists as a development option, but the ontology and settings are still read from Impala.
- **Talk to Your Data needs a selected semantic model.** Conversations live under `/models/{model_id}/...`, the page requires one, and both document tools refuse a caller without a model.
- **Permissions are granted on models.** There is nothing else to grant a document-only user access to.

**Silent degradation (it runs, and finds nothing):**

- **Mentions come from a dictionary built from warehouse tables** (`gazetteer.py`: "built from the warehouse through the ontology mapping and nothing else"). With no mapping the crawler prints "mentions are not extracted" and stops at segments.
- **Resolution, relationships and claims all depend on mentions**, so they are empty too.
- **Embedded passages still work**, but carry no linked entities.

**Already independent:**

- Fetching, type detection and splitting into segments.
- The object-store connector (the Helios-DS connector lists files through a warehouse view; the object-store one does not).
- Identifier patterns in the crawler settings (email addresses, case and ticket numbers, dates, amounts).
- Embeddings and search by meaning.
- The LLM crawler's *reading* step.

So a documents-only customer today gets: files fetched and split, and, if they get past the Impala requirement, search by meaning. Nothing else.

## What today's measurements say

The LLM crawler built this week is, in effect, the documents-only experiment. It reads documents with no warehouse reasoning.

| Measure | Rules + warehouse | LLM, no warehouse reasoning |
|---|---|---|
| Mention recall (direct / alias / contextual) | 99.7% / 100% / 100% | 92.3% / 95.6% / 80.7% |
| Mention precision | 92.8% | 72.9% |
| Mentions resolved to the right row | 99.8% | 10.1% |
| Claims | 100% / 100% | 0% / 0% |

Two lessons:

- **Finding things in text does not need a warehouse.** A model with only the ontology's class definitions found over 90% of direct mentions.
- **Knowing what they are does.** Resolution and claims collapsed, because "the right row" is defined as a warehouse row. Without a warehouse there is no row; the question has to become "which mentions refer to the same thing?", which that run did not attempt.

One caution: those figures are on a synthetic corpus built from warehouse data, scored against warehouse keys. They show the shape of the problem, not what a real documents-only customer would see.

## Point of view

### 1. Make the warehouse one source of knowledge, not the foundation

Today the code path is: mapping → dictionary → mentions → resolution. Reframe the first step as **reference sources**, of which a warehouse mapping is one:

- **A warehouse mapping** (today's behaviour, unchanged).
- **A reference list** the customer supplies: a product catalogue, a customer export, an employee list, as a file. It gives a dictionary and stable keys without a lakehouse. Many "documents-only" customers have these.
- **The corpus itself**: entities discovered from what documents say, with no outside list.
- **None**: patterns and the model only.

The dictionary, mention extraction and the tiers of resolution already work from "forms that name instances". They need a different supplier, not a redesign.

### 2. Let entities be defined by the documents

Without a warehouse row, an entity needs another identity. In order of confidence:

1. **A strong identifier in the text**: an email address, account number, case or order number. The crawler already makes entities this way for returns (a return known only by its RMA number). This generalises.
2. **A reference-list key**, when a list is supplied.
3. **A grouping of mentions that co-occur**: the same name with the same email in several documents is one person. The case-grouping step already links documents by shared identifiers; this extends it from "one case" to "one entity".
4. **A name alone**: recorded as a possible match, never a definite one. The existing rule (precision over recall; below the threshold it is "possibly the same") applies as is.

This is weaker than warehouse resolution and should be labelled as such everywhere it is shown. It is also what document-only products do; the difference Helios can offer is that every link states how it was made and on what evidence.

### 3. Turn the documents into data, then model that

This is what gives a documents-only customer more than search.

The crawl index is already a set of tables: documents, passages, entities, relationships, claims, and the fields and table rows recognised in PDFs. Helios can:

- write those as queryable tables (documents by type and date, entities by class, claims by type, extracted fields);
- **generate a semantic model over them automatically**, with datasets, relationships and a starter set of metrics (documents per month, claims by type, entities by class);
- let that model be reviewed and published like any other.

Then Talk to Your Data works for a documents-only customer with no new machinery: "how many damaged-package complaints were there each month?" is a metric query over the claims table, and "what did customers say about them?" is the document search. Both paths, one source.

For a customer with both, the same derived model sits beside their warehouse model, which also answers a question the current design cannot: counts of things that exist only in documents.

### 4. Make the ontology usable without a mapping

- An ontology class needs a mapping only to be resolved to a warehouse row. Without one it can still be found and typed.
- Identifiers become **patterns and reference columns** as well as warehouse columns.
- Publishing currently checks mappings against one fixed semantic model file. That check should apply only when a mapping exists.
- Documents-only customers will not have a domain pack that fits. The ontology authoring side quest matters more for them than for anyone: its "suggestions from documents" (OA-6) becomes the way in. Core alone (people, organisations, documents, claims) is a workable start.

### 5. Give documents a home that is not a semantic model

Conversations, permissions and traces hang off a model. Two ways to fix that:

- **A model with no warehouse datasets.** A documents-only customer gets a model whose datasets are the derived tables in point 3. This needs the fewest changes and is my preference, because point 3 produces such a model anyway.
- **A separate "knowledge base" container.** Cleaner in principle, and a much larger change (permissions, routes, the page, traces).

### 6. Decide what "no lakehouse" means for storage

Two different customers are hiding in the phrase:

- **No business data in a lakehouse, but the Cloudera platform is there.** Impala and Iceberg are available; Helios keeps its index where it is today. This is likely the common case for a Cloudera customer, and it removes the hardest dependency: only the *knowledge* assumptions (points 1 to 5) need work.
- **No SQL engine at all.** The index, ontology versions and settings need another store. The store already has a DuckDB dialect used for development; it would have to become a supported, durable option, with its own answers for concurrent access and scale.

I would build for the first and not commit to the second until a customer needs it.

## What each kind of customer gets

| Capability | Lakehouse + documents | Lakehouse only | Documents only (proposed) |
|---|---|---|---|
| Semantic model from discovery | Yes | Yes | Generated from extracted data |
| Questions over tables | Yes | Yes | Yes, over extracted data |
| Search documents by meaning | Yes | n/a | Yes |
| Entities found in documents | Yes | n/a | Yes |
| Entities tied to a system of record | Yes | n/a | Only with a reference list |
| Claims with evidence | Yes | n/a | Yes, between document-defined entities |
| Join documents to warehouse figures | Yes | n/a | No; joins are to extracted data |
| Ontology | Pack + mapping | Optional | Core + authored from documents |

**Lakehouse only** is close to working today. The gaps are cosmetic: the guided journeys and the assistant's wording assume a crawl, and the Talk assistant is told about document tools that will report "unavailable". These should adapt to what is configured.

## Where Helios's value is for documents-only

The original pitch, working backwards from trusted warehouse data, does not apply. What remains distinctive:

- **Provenance.** Every entity, link and claim points to the exact passage, with how it was derived and how confident.
- **Governed meaning.** A published, versioned ontology and semantic model over the documents, reviewed by a person, instead of an opaque index.
- **Counting as well as searching.** Most document tools retrieve passages. Extracted, modelled data lets users ask how many and how it is changing.
- **A path to more.** When the customer later connects a system of record, the same entities gain real keys and everything already extracted becomes joinable.

Where it will be weaker, and should say so: entity identity is less certain; extraction relies more on a model, so it costs more and makes more mistakes (27% of the LLM crawler's mentions were wrong on the development corpus, and 4.5% of what it returned was not in the document at all); and there is no ground truth to score against unless the customer labels a sample.

## Suggested order

1. **Run without a mapping.** Let the crawler use patterns and the LLM reading step when there is no warehouse mapping, and make entities from strong identifiers. Smallest change; documents-only stops being empty.
2. **Group mentions into entities across documents**, with each link's basis recorded. Measure it: the development corpus can score "same entity or not" without using warehouse keys.
3. **Publish the index as tables with a generated semantic model**, and allow a model with no warehouse datasets. This is where the value appears.
4. **Reference lists** as a source of entities.
5. **Ontology authoring from documents** (OA-6), so a customer can go beyond core.
6. **Make journeys, the assistant and Talk adapt** to which of the three situations applies.

## Risks and open questions

1. **Quality without a warehouse is unmeasured on real data.** Step 2 needs its own evaluation before anything is promised.
2. **Cost.** Documents-only leans on the LLM for every document. The rules crawler's patterns should stay first, with the model for what they miss.
3. **Wrong merges are worse than missed ones.** Grouping by name will sometimes merge two people. The possible-match tier and a way for a person to split or merge entities are needed.
4. **Per-user access to document text** is still open (crawler item DS-7). It matters more when documents are the whole product.
5. **Two entity populations.** A customer who starts documents-only and later adds a warehouse needs their document-defined entities reconciled with warehouse rows. The design should assume this will happen.
6. **Which "no lakehouse"?** Point 6: confirm whether any target customer truly has no SQL engine.
