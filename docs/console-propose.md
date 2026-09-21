# Reading a proposal

A **proposal** is helios's first draft of what your warehouse means. It is built from a run's harvest and profile results (see *Reading a discovery run*) plus a language model's judgement, and it is the material the review pages will let you accept, edit or reject. Nothing on this page is published: opening it changes nothing in Atlas, in the semantic model, or in the warehouse.

Open it from **Runs → a run → View proposal**. The button appears once the propose job has finished for that run.

---

## What the page is for

The discovery run establishes *facts*: which tables and columns exist, which columns are keys, which key columns refer to which tables. A proposal adds *meaning*: what each table and column is called in business terms, what it means, how it should be used, and which metrics people would want computed from it.

Facts came from statistics and are stated with high confidence. Meaning came from a language model reading the evidence, and every element carries a **confidence** between 0 and 1 that reflects how sure the model was. Treat confidence as a triage aid: review low-confidence items first, and read a 0.95 as "probably right, still check".

The line under the page title says which model produced the draft and how many calls it made.

---

## Datasets

One expandable section per table. Click a table name to open it.

The heading shows:

- the physical table (`database.table`);
- the **business name** helios gave it, e.g. `Store sales`;
- the **kind** — *fact* (transactions or events, with measures to add up), *dimension* (an entity used to slice facts: customer, item, date), *bridge* (links two other tables), *lookup* (a small code table) or *other*;
- the table-level **confidence**.

Below the heading is a one- or two-sentence description of the table, followed by a column table.

| Column | What it means |
|--------|---------------|
| **Column** | The physical column name. |
| **Business name** | A short, human-readable name for the column, e.g. `Net paid` for `ss_net_paid`. This is what a semantic layer or an agent would show to a user. |
| **Role** | How the column should be used. See the list below. |
| **Description** | What the column means in plain language, including units and derivations where they could be inferred (e.g. "Quantity × unit sales price, before coupons and tax"). Where a glossary term is already linked, its definition is preferred. |
| **Refers to** | For foreign keys: the key column in another table this column joins to, taken from the verified relationships in the profile. Empty for everything else. |
| **Glossary** | The Atlas glossary term(s) already linked to this column. If none is linked and helios thinks the column deserves one, it shows `proposed:` followed by the suggested term name. Empty means no term exists and none is proposed (typical for keys and system columns). |

### Column roles

| Role | Meaning | Typical examples |
|------|---------|------------------|
| **identifier** | Uniquely identifies a row in this table — its primary key. | `c_customer_sk`, `i_item_sk` |
| **foreign_key** | Refers to a row in another table; used for joins, never shown to users as a value. | `ss_customer_sk`, `ss_store_sk` |
| **time** | A date/time, or a key into a date or time table. Drives "by month", "last quarter". | `ss_sold_date_sk`, `d_date` |
| **measure** | A numeric quantity meant to be aggregated (summed, averaged). | `ss_net_paid`, `ss_quantity` |
| **dimension** | A category or attribute used to group or filter. | `cd_gender`, `i_category`, `s_state` |
| **attribute** | Descriptive information carried along but not useful for grouping. | `c_email_address`, `i_item_desc` |

Before the model saw the table, helios assigned each column a *hint* from the statistics (keys from the profile, numeric columns with many distinct values as measures, and so on). The model can override the hint when the evidence says otherwise — a numeric column that is really a code, for instance — so the role shown is the model's decision informed by the hint.

---

## Relationships

Every join path helios knows about, in one table.

| Column | What it means |
|--------|---------------|
| **From** | The referring column (`database.table.column`). |
| **To** | The key column it refers to. |
| **Source** | Where the decision came from. `profile` — accepted by the discovery run on both name and data evidence; no model involved. `llm_confirmed` — the data matched but the name did not point at the target, and the model judged it a real relationship. `llm_rejected` — the same situation, judged a coincidence. |
| **Accepted** | `yes` or `no`. Only accepted relationships will become joins in the semantic model. |
| **Confidence** | 0.95 for profile relationships; the model's own confidence for the ones it decided. |
| **Reason** | The model's one-line justification, present only for relationships it decided. Read these — they are the fastest way to spot a wrong call. |

---

## Metrics

Candidate business metrics, drawn from the fact tables' measures and from the queries people have actually run against the warehouse.

| Column | What it means |
|--------|---------------|
| **Metric** | The metric's name, with its description underneath. |
| **Dataset** | The fact table it is computed over. |
| **Expression** | The aggregate SQL expression, in terms of that table's physical columns, e.g. `SUM(ss_net_paid)` or `SUM(ss_net_profit) / SUM(ss_net_paid)`. This is what the semantic layer would run. |
| **Source** | `query_history` when the aggregation appears in queries users have run — evidence that someone cares about this number. `derived` when the model proposed it from the measures and domain conventions (totals for every money and quantity column, standard ratios such as margin or return rate). |
| **Confidence** | The model's confidence that the metric is meaningful and the expression is right. |

Metrics that reference a column that doesn't exist are dropped before the page is rendered, so every expression here at least refers to real columns. Whether the arithmetic is *correct* is a review question.

---

## Proposed glossary terms

New business terms helios suggests adding to Atlas, for columns that have no term linked today.

| Column | What it means |
|--------|---------------|
| **Term** | The suggested term name. Where the same concept appears in several tables (a quantity column in store, catalog and web sales, say), helios proposes one term and lists every column under it, so the glossary stays free of duplicates. |
| **Definition** | The suggested short definition. |
| **Columns** | The physical columns the term would be linked to. |
| **Confidence** | The highest confidence among the columns that produced this term. |

Columns that already have a glossary term never appear here — the existing term is kept and shown in the Datasets section instead.

---

## How to read a proposal well

- **Start with the relationships.** A wrong join poisons every metric computed across it. Check every `llm_confirmed` and `llm_rejected` row and its reason.
- **Then the fact tables.** Open each *fact* dataset and scan the roles: a measure marked as a dimension will never be aggregated; a code marked as a measure will be summed nonsensically.
- **Then the metrics.** Compare expressions against how the business actually computes the number. "Net sales" including or excluding returns is the classic disagreement.
- **Glossary terms last.** They are the lowest-risk items and the easiest to fix later.
- **Confidence is relative to this run.** Regenerating the proposal with a different model, or after adding glossary terms, will change both the content and the confidences. The run identifier ties a proposal to the exact evidence it was built from.

---

## Where the files live

The proposal is `runs/<run-id>/propose.json` in the project. It uses helios's own draft format, which is designed to feed all three layers — glossary, semantic model and ontology. Conversion to the Ossie document format happens at publish time, after review.
