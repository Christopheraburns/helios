# Reading a discovery run

The **Runs** section of the helios console shows what helios found when it examined your warehouse. Each run is one pass of the discovery jobs over a set of databases. Nothing on these pages changes your data or your glossary; they are read-only views of what the jobs recorded.

There are three pages: the list of runs, a single run, and a single table within a run.

---

## The Runs page

A table with one row per run.

| Column | What it means |
|--------|---------------|
| **Run** | The run's identifier, which is the UTC date and time the harvest job started, in the form `20260921T170000Z` (21 Sep 2026, 17:00:00 UTC). Newest first. Click it to open the run. |
| **Harvest** | `done` if the harvest stage finished and wrote its results; `—` if not. Harvest reads the catalog: which tables exist, their columns and types, row counts, the glossary, and historical queries. |
| **Profile** | `done` if the profile stage finished. Profile reads the data: statistics for every column, which columns are keys, and which key columns refer to which tables. |
| **Propose** | `done` when the propose stage has produced draft glossary terms and a draft semantic model for this run. This stage is not built yet, so the column shows `—` for now. |
| **Updated** | When files in this run were last written. |

A run with Harvest `done` and Profile `—` usually means the profile job is still running or failed; check the job's log in the Workbench.

---

## The run page

Opened by clicking a run. It has a summary line, three counters, and four tables.

### Harvest summary

One sentence, for example:

> Harvested tpcds via impala: 24 tables, 425 columns, 138 glossary terms, 12 historical queries (sys.impala_query_log unavailable: …; /home/cdsw/helios/setup/queries).

- **databases** — the databases helios was asked to examine (`HELIOS_DATABASES`).
- **engine** — which SQL engine ran the queries (Impala today).
- **tables / columns** — how much of the catalog was read.
- **glossary terms** — how many Atlas glossary terms were loaded, along with the columns they are linked to. These give helios human-authored meaning to work with.
- **historical queries** — how many past SQL statements helios collected, and from where. Two sources are tried: the engine's own query log, and a folder of `.sql` files you provide. If a source was unavailable, the reason is shown in the parentheses. Query history is used later to spot which aggregations people actually run.

### The three counters

These summarise the relationships the profile stage found between tables. A *relationship* is a claim of the form "column X in table A refers to key column Y in table B" — the join path a query would use.

Every candidate relationship is tested against the data: helios takes the distinct values in column X and checks how many of them exist in column Y. The result is the **match ratio**. A candidate with a match ratio of at least 95% passes the data test.

- **Accepted** — passed the data test *and* the column's name points at the target table (for example `ss_customer_sk` points at `customer`). Two independent kinds of evidence agree, so these are treated as facts.
- **Suggested** — passed the data test, but the name gives no hint. The values happen to line up, which for small integer keys can be coincidence (values 1–50 are contained in 1–1000 whether or not the tables are related). These are handed to the propose stage, which has more context to judge them, and to you.
- **Rejected** — looked like a key by name but failed the data test: a meaningful share of the values do not exist in the target. On clean data this list is empty. Anything here is either a wrong guess by the name heuristic or a real data-quality finding, such as orphaned rows.

### Tables

One row per table harvested.

| Column | What it means |
|--------|---------------|
| **Table** | `database.table`. It is a link when the table was profiled; click it for column-level detail. |
| **Columns** | Number of columns. |
| **Rows** | Row count, taken from table statistics when available, otherwise counted directly. `?` means neither was possible. |
| **Primary key candidates** | Columns that uniquely identify a row: no nulls, and an exact count of distinct values equal to the row count. Only columns whose names look like keys (`_sk`, `_id`, `_key`) are considered. A table with none listed is typically a fact table whose rows are identified by a *combination* of columns (ticket number plus item, for example), which helios does not attempt to detect yet. |

### Accepted relationships

| Column | What it means |
|--------|---------------|
| **From** | The referring column, as `database.table.column`. Usually a column in a fact table. |
| **To** | The key column it refers to. Usually the primary key of a dimension table. |
| **Match** | The match ratio: the percentage of distinct values in *From* that exist in *To*. 100% means every value resolves. |
| **Name score** | How strongly the column name points at the target table: `1.0` when a word in the name equals the table name (`customer` → `customer`), `0.9` for a partial match, `0.8` when an abbreviation was recognised (`cdemo` → `customer_demographics`). |
| **Distinct values** | How many distinct non-null values the *From* column holds — the size of the sample the match ratio was measured on. A 100% match over 100,000 values is stronger evidence than over 12. |

If a column could plausibly refer to more than one table, only its best-scoring target is listed.

### Suggested relationships

Same columns as above, without the name score (it is zero by definition). Treat each as a question rather than an answer.

### Rejected candidates

| Column | What it means |
|--------|---------------|
| **From / To** | As above. |
| **Match** | Below 95%. |
| **Unmatched** | The number of distinct *From* values that were not found in *To*. A handful of unmatched values in a large column may be orphaned records; thousands mean the columns are unrelated. |

---

## The table page

Opened by clicking a table name on the run page. It shows how helios sees one table.

The heading line gives the row count and the primary key candidates.

### Columns

One row per column.

| Column | What it means |
|--------|---------------|
| **Column** | The column name. |
| **Type** | The data type as declared in the catalog. |
| **Null rate** | The percentage of rows where this column is empty. High null rates on a key column are a warning; high null rates on an attribute are just information. |
| **Distinct** | The number of different values. Shown exactly when helios verified it (key candidates), otherwise as an approximation marked with `≈`. Approximate counts can be off by a few percent, and more on very small tables. Use it to tell identifiers (distinct ≈ rows) from categories (a handful of distinct values) from measures (many distinct values, numeric type). |
| **Min / Max** | The smallest and largest value. For dates this is the range covered; for amounts, the spread; for codes, a quick sanity check. |
| **Glossary terms** | Business terms from Atlas that are linked to this column. Empty means no term has been linked yet — which, for a well-named column, is exactly the kind of gap the propose stage is meant to fill. |

### Relationships

The accepted relationships that involve this table, in either direction. For a dimension table this lists every fact column that refers to it; for a fact table it lists every dimension it joins to.

---

## Things to know

- **Runs are snapshots.** If you change the warehouse or the glossary, run the jobs again; an old run keeps showing what was true when it ran.
- **Nothing here is published.** Accepted relationships are helios's finding, not yet part of any semantic model. Publishing happens in the review pages, once the propose stage exists.
- **Where the files live.** Each run is a folder under the project's `runs/` directory containing `harvest.json` and `profile.json`; the pages are rendered from those files, and you can open them directly if you want the raw numbers.