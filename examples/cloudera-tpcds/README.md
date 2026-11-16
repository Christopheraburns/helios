# TPC-DS on Cloudera

How to stand up the full TPC-DS warehouse Helios is developed against, in a Cloudera public cloud environment: an SDX data lake (Hive Metastore, Atlas, Ranger, Knox), an Impala Virtual Warehouse, Cloudera AI Workbench, Cloudera AI Inference and S3 storage. helios-cloudera's nightly live test (`cloudera-live` in CI) and the 20-question suite in [`examples/eval`](../eval/README.md) run against it; the repository's published model for it is [`models/published/tpcds.ossie.yaml`](../../models/published/tpcds.ossie.yaml). To try Helios without Cloudera, use `helios init --sample` instead.

**Data:** TPC-DS at scale factor 1 (about 1 GB, 24 tables). It is generated with DuckDB's `tpcds` extension, landed as Parquet in the data lake bucket, staged as external tables, and converted to Iceberg tables in the Impala database `tpcds`.

```
setup/start.py       generate TPC-DS Parquet with DuckDB (run in a Workbench session)
setup/gen_ddl.py     emit stage.sql and iceberg.sql from the generated table directories
setup/stage.sql      CREATE EXTERNAL TABLE ... LIKE PARQUET for each table (database tpcds_stage)
setup/iceberg.sql    CREATE TABLE ... STORED AS ICEBERG AS SELECT ... (database tpcds), COMPUTE STATS
```

A handful of TPC-DS benchmark queries (Impala dialect, from `cloudera/impala-tpcds-kit`) are run against the warehouse, so that Impala's query history holds realistic aggregation patterns for discovery to mine. The kit's literal filter values were generated for a 10 TB scale factor and may need adjusting at SF1.

**Business glossary:** an Atlas glossary named `TPCDS Retail`. It has 138 terms (entities, measures with dsdgen pricing formulas, dimension attributes and derived KPIs), linked to 393 columns. It is the reference: what Helios should approximate from a cold start.

```
atlas-glossary/build_glossary.py        generates the glossary CSVs from a term catalogue in code
atlas-glossary/assign_terms.py          imports a glossary CSV into Atlas and links terms to columns
atlas-glossary/tpcds_glossary_full.csv  all 138 terms, Atlas bulk-import format
atlas-glossary/tpcds_glossary_seed.csv  32 core terms: the "cold start" glossary for discovery experiments
atlas-glossary/tpcds_term_columns.csv   term → table.column assignments
```

From `atlas-glossary/`:

```bash
export ATLAS_BASE="https://<datalake-host>/<datalake-name>/cdp-proxy-api/atlas/api/atlas/v2"
export ATLAS_USER=<workload-username>
export ATLAS_PASS='<workload-password>'
python3 assign_terms.py tpcds_glossary_full.csv                # import + assign
python3 assign_terms.py tpcds_glossary_full.csv --skip-import  # glossary already loaded; only assign
```

Two things to know about Atlas glossary import here:
- The REST import returns `409` if any term already exists; it doesn't update in place. To change definitions, delete the glossary and reload it.
- The `Synonyms` column is a term-to-term relationship rather than free text, so synonyms are folded into each term's long description.

The full glossary is what a well-governed warehouse would have, so loading it before a discovery run hands Helios the answer key. For evaluation, load the seed glossary under its own name and keep the full one hidden.
