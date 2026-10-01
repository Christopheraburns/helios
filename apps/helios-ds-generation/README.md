# Helios-DS-Generation

Deterministic synthetic-asset generator for Helios. It turns the TPC-DS business world into native artifacts (PDF, email, chat, image, audio, video) with known ground truth. The work is tracked in [docs/helios-ds-burndown.md](../../docs/helios-ds-burndown.md).

## Layout

| Path | Contents |
|---|---|
| `src/helios_ds/` | Generator core: config, IDs and seeds, TPC-DS adapters (Impala, DuckDB), scenario planner, templates, S3/local object store, lakehouse sinks (Impala, PyIceberg for tests), manifests, lifecycle, pipeline, CLI |
| `src/helios_ds_generation/` | FastAPI `/v1` API and dashboard back end |
| `templates/<artifact_type>/<template_id>/` | Template descriptors (`template.yaml`); renderer assets arrive with each generator |
| `schemas/lakehouse_impala.sql` | Generated Impala DDL for `helios_ds.*` and `helios_ground_truth.*` (regenerate with `python -m helios_ds.lakehouse`) |
| `runtimes/` | `helios-ds-runtime` and `helios-ds-media-runtime` Dockerfiles, plus `build.sh` |
| `workbench/` | Workbench Job scripts |
| `app.py` | Workbench Application entry point (API and dashboard) |
| `tests/`, `fixtures/tiny/` | Unit, deterministic, integration and benchmark tests |

## Develop and test

```bash
cd apps/helios-ds-generation
pytest -q               # generates a tiny TPC-DS with DuckDB on first use
ruff check src tests workbench && mypy
```

Lakehouse tests run against both sink implementations: the PyIceberg SQL-catalog sink (SQLite by default; set `HELIOS_DS_TEST_CATALOG_URI=postgresql+psycopg2://...` for PostgreSQL, as CI does), and the SQL sink that Workbench uses with Impala, run on DuckDB as a stand-in.

## Plan a dataset (Foundation pipeline)

The pipeline goes TPC-DS → scenario planner → deterministic plan → generation manifest in the object store, plus `helios_ds` rows. The dataset ends in `IN_REVIEW`; only `approve` makes it `READY`, which is the only state the crawler may see.

```bash
export PYTHONPATH=src:../../shared:$PYTHONPATH

# Workbench: tpcds and helios_ds/helios_ground_truth through Impala, artifacts in S3
python -m helios_ds plan --tpcds impala:tpcds --lakehouse impala \
  --store s3a://applied-ai-buk-d5eff1ab/helios-db/source

# Offline: generated TPC-DS, local Iceberg catalog, local files
python -m helios_ds plan --init-tables \
  --tpcds duckdb-generate:1 \
  --lakehouse sql:sqlite:////tmp/helios-ds/catalog.db --warehouse file:///tmp/helios-ds/warehouse \
  --store file:/tmp/helios-ds/objects

python -m helios_ds approve <dataset_id> --approver user:<you> --lakehouse ... --store ...
```

Re-running `plan` with the same inputs is a reproducibility check. It recomputes the manifest and fails with `DeterminismIntegrityError` if the result differs from what was published. It never overwrites.

Backend URIs:

| Option / env var | Values |
|---|---|
| `--tpcds` / `HELIOS_DS_TPCDS` | `impala[:database]` (Workbench), `duckdb:/path.duckdb`, `duckdb-generate:<sf>` |
| `--lakehouse` / `HELIOS_DS_LAKEHOUSE` | `impala` (Workbench), `sql:<sqlalchemy uri>` with `--warehouse` (offline) |
| `--store` / `HELIOS_DS_OBJECT_STORE` | `s3a://bucket/prefix` via the Workbench data connection (`HELIOS_DS_S3_CONNECTION`, default `S3 Object Store`), `file:/path` |

## Workbench setup

| Data | Where it lives | How the generator reaches it |
|---|---|---|
| `tpcds.*` (source) | Existing Iceberg lakehouse | Impala Virtual Warehouse |
| `helios_ds.*`, `helios_ground_truth.*` | **The same lakehouse as `tpcds`**: new databases in the same Metastore | Impala Virtual Warehouse |
| Generated artifacts and manifests | `s3a://applied-ai-buk-d5eff1ab/helios-db/source` | Workbench data connection `S3 Object Store` (boto3, authorized through Ranger/RAZ; no AWS keys in the project) |

The S3 path was verified from a Workbench session on 2026-09-29: a probe object was written, re-written as a no-op, rejected when the bytes differed, and deleted. The Impala path hasn't run yet because no Impala credentials are configured in this project. To finish setting up:

1. **Impala connection:** set the same project environment variables the Helios apps use: `IMPALA_HOST` (host or CDW JDBC URL), `WORKLOAD_USER` and `WORKLOAD_PASSWORD`.
2. **Tables:** run `schemas/lakehouse_impala.sql` in Hue or impala-shell, or run `python -m helios_ds init-tables --lakehouse impala`. The workload user needs Ranger rights to create the `helios_ds` and `helios_ground_truth` databases, and to INSERT and DELETE in them. DELETE on Iceberg v2 tables needs a recent Impala; the generator uses it only to clean up after an interrupted publish.
3. **Ground-truth isolation:** in Ranger, deny `helios_ground_truth` (and `helios_ds`) to the crawler and Helios query principals (task S-05).
4. **Object store:** the project needs the `S3 Object Store` data connection (Project Settings → Data Connections); set `HELIOS_DS_S3_CONNECTION` if it has another name. Its Ranger/RAZ policy must allow read and write under `helios-db/source/`.
5. **Job:** create a Job for `workbench/plan_dataset.py` on `helios-ds-runtime`, with `HELIOS_DS_TPCDS=impala:tpcds`, `HELIOS_DS_LAKEHOUSE=impala` and `HELIOS_DS_OBJECT_STORE=s3a://applied-ai-buk-d5eff1ab/helios-db/source`.

### Generation jobs (API → Workbench Jobs)

`POST /v1/generations` (or **Submit** in the dashboard) records a job in `helios_ds.generation_jobs` / `helios_ds.job_events` and starts a run of the **`helios-ds-generate`** Workbench Job with `HELIOS_DS_JOB_ID` set. The worker ([workbench/run_generation.py](workbench/run_generation.py)) runs the pipeline and records RUNNING and then SUCCEEDED or FAILED; `GET /v1/jobs/{id}` reads the state back from the lakehouse. Re-running a job is safe.

- **Create or update the Workbench Job:** `python helios/apps/helios-ds-generation/workbench/setup_jobs.py`. This sets helios-ds-runtime, 2 vCPU / 4 GiB, and the lakehouse, TPC-DS and object-store settings. Override the sizes with `HELIOS_DS_WORKER_CPU` and `HELIOS_DS_WORKER_MEMORY`.
- **API/UI Application (`app.py`):** run it on `helios-ds-runtime` with `HELIOS_DS_LAKEHOUSE=impala` (the Impala variables and `HELIOS_DS_API_KEY` come from the project). `HELIOS_DS_DISPATCHER` defaults to `workbench`; `inline` runs the worker inside the API process, for local development only.
- **Job state is an event log:** job state is append-only, and the first SUCCEEDED, FAILED or CANCELLED event is final. Events are written at milestones only, because each lakehouse write takes about 1–2 s through Impala.

### Rendering artifacts (phase 3+)

A template can be rendered once its directory has a `renderer.py` (`render(ctx) -> RenderedArtifact`) and, usually, a `phrases.yaml` phrase bank. No ML model is involved:
- **Content:** TPC-DS facts from the scenario plan.
- **Structure:** the renderer's layout.
- **Wording:** seeded choices from the phrase bank (`{name}` fills a value; `{@name}` places a tracked entity mention).
- **Case values:** values shared by a story's artifacts (RMA number, case number, staff, timeline) derive from the scenario seed, so they agree.

Shared helpers are in `src/helios_ds/render/`. Changing a template directory changes the dataset ID; changing shared render code needs a generator version bump. Currently rendered: `return_report` (PDF), `damaged_item` (email) and `support_return` (chat), all for damaged product returns. Files are stored at `datasets/<dataset_id>/artifacts/<artifact_id>.<ext>`.

### Ground truth and review

Each render also writes the hidden answer key to `helios_ground_truth`. It covers:
- **Entities:** customers, items, stores, returns, sales, reasons, brands and the artifacts themselves.
- **Mentions:** with exact locators.
- **Relationships:** business edges such as `REFERS_TO_SALE`, and artifact edges such as `MENTIONS` and `DISCUSSES`.
- **Claims:** for example `PACKAGING_DAMAGED`, with a plain-language statement.
- **Evidence:** the exact passage supporting each claim.

Each mention records how hard it is to resolve (C-05): `direct` (canonical name or official ID), `alias` (another identifying name, such as "Midway store", "receipt ending in 1892", the customer's email or "Mrs. Beatty"), or `contextual` ("the item", "this return"). Every locator is checked against the rendered bytes before it's written. `init-tables` also migrates existing tables additively (`ALTER TABLE ... ADD COLUMNS`).

**Golden questions (C-08).** Every new dataset also gets its answer key, generated with the ground truth into `helios_ground_truth.expected_queries`/`expected_results`: about 10 each of structured, unstructured, entity-resolution, joined, cross-document and no-answer questions, with computed answers and the evidence they require. See the manifest viewer's **Golden questions** tab.

The dashboard's **Review** tab (ADR 0001, stage B) walks a dataset in `IN_REVIEW`. Each artifact sits beside its ground truth, with mentions highlighted and evidence underlined. Reviewers mark it accepted, flagged or commented (advisory). Marks need the authenticated Workbench user, taken from the Application proxy's identity header: `GET /v1/whoami` shows what the API receives. Review screens show hidden ground truth, so this Application must stay in the generation project.

The Review tab's **Decision** panel approves or rejects the dataset (R-06). Approval re-validates the published dataset (flags never block it), records the approver, and moves every other `READY` dataset generated from the same config to `SUPERSEDED`, so one dataset per lineage is crawler-visible. Rejection needs a reason and is final: fix the config or templates, then regenerate. Once a dataset is decided, review marks close. **History** shows the audit trail (R-07): every lifecycle event, decision and review mark, rebuilt from `helios_ds.dataset_lifecycle` and `helios_ds.review_marks`. After upgrading, run `python -m helios_ds init-tables --lakehouse impala` once to add `dataset_lifecycle.related_dataset_id`.

**Deleting datasets.** In **Datasets & Manifests**, **Delete dataset** (type the first 8 characters of the ID to confirm) removes a dataset you no longer need. It deletes the manifest and artifacts from the object store, and the dataset's rows from every `helios_ds` and `helios_ground_truth` table. It keeps `dataset_lifecycle` (with a `DELETED` event recording who, when and why), `generation_runs` and job history, so the audit trail survives. Datasets that are still `CREATING` or `VALIDATING` can't be deleted. Generating the same config again recreates the dataset under the same ID. If deletion stops part-way (for example, the S3 connection is denied `DeleteObject`), the dataset is already `DELETED` and hidden from the crawler, and deleting it again finishes the job. It needs `DeleteObject` on the S3 prefix and Ranger `DELETE` on the tables.

### Datasets & Manifests (dashboard)

The **Datasets & Manifests** tab (or **View manifest** on a finished job) shows each published dataset. It has these tabs: a summary (target vs planned assets, eligible vs chosen TPC-DS records, lifecycle), a searchable, paginated scenario browser with one-line stories and per-scenario facts, source rows and planned assets, the TPC-DS source fingerprint, templates, config, and a download of the exact manifest file. It's backed by `GET /v1/datasets`, `/v1/datasets/{id}`, `/v1/datasets/{id}/manifest`, `/manifest/raw`, `/scenarios` and `/scenarios/{scenario_id}`. Manifests are read by their recorded location, checked against their SHA-256, and cached.

The manifest shows each scenario's type and source facts, which is close to the answer key, so this Application must never be reachable by the crawler or Helios query users.

Object-store layout under the prefix: crawlable artifacts go in `datasets/`, generator-internal manifests in `_manifests/`, and health-check probes in `_healthcheck/`. The source connector must only enumerate `datasets/`.
