# Ontology Visualizer and Graph DB Burn-Down

Plan for hosting Memgraph in the Helios project and building an ontology visualizer in the Helios UI. It comes before the first Helios crawler.
Source: `ontology/README.md` and the LinkML files under `ontology/`, plus a survey of the Helios UI, API and deployment (`apps/helios`, `docs/ui-deployment.md`, `docs/ui-api-networking.md`, `docs/ui-backend-contract.md`).

**Status (2026-10-01):** O-0, O-2, O-3, O-4 (with O-4b's graph view) and O-5 are done, each in its own commit (O-2 `4c27153` + `a08f8c0`; O-3 `3673366`; O-4 `07eeb24`, `1015f48`, `a2d16f7`; O-5 `6f5cb83`). O-1 is open only for the runtime image's build files, which are not in the repo. Published versions are now recorded in the lakehouse (`helios_index.ontology_versions`, crawler task CR-0c), and the on-disk store is a cache rebuilt from it. The status below is the earlier one.

**Status (2026-09-30):** O-0 done — the Helios Graph Application runs Memgraph in the Workbench and answers `RETURN 1` through the gateway. O-1 items 1–4 landed with it; the materialise and read endpoints plus startup rebuild remain. No ontology parser yet, and `linkml` is not installed.

**How to use:** work top to bottom. A task is done only when its **Done when** check passes. Check the box and add the commit hash.

---

## Context

The ontology is a **schema**, not data:
- **Classes in three layers:** `core/core.yaml` → `packs/retail/retail.yaml` → `customers/<tenant>/extension.yaml`. Each layer only adds leaves.
- **An `is_a` hierarchy:** e.g. Thing → EnterpriseEntity → Person → Customer.
- **Attributes with ranges:** e.g. `Item.brand`, `Person.affiliated_with → Organization`.
- **The relationship vocabulary:** SameAs, PossiblySameAs, Mentions, About, ReferencesRecord, EvidenceFor, DerivedFrom and so on, plus the `ResolutionTier` enum.
- **The Ossie → ontology mapping** (`mappings/ossie/tpcds.yaml`): which Ossie element becomes which class, which columns identify instances, and the resolution thresholds.

That is roughly 45 classes and about 100 attributes and mappings. The crawler's instances (entities, assets, segments, mentions) will later go into the same Memgraph database, so the viewer and graph service are designed to hold both.

## Architecture

```
Browser ──► Helios UI (new Ontology page)
              │  fetch, SSO cookie
              ▼
           Helios API  ── /api/v1/ontology/*  (checks ontology.read / ontology.edit)
              │  HTTPS + service token (never reaches the browser)
              ▼
           Helios Graph  (NEW Workbench Application)
              ├─ graph gateway (FastAPI on CDSW_APP_PORT): fixed, parameterised Cypher only
              └─ memgraph (Bolt on 127.0.0.1:7687, never exposed)
```

- **Memgraph gets its own Application ("Helios Graph").** A Workbench Application exposes one authenticated HTTP port, and Bolt cannot go through that proxy. The Application runs Memgraph bound to localhost, plus a small HTTP gateway in front of it. This follows the MCP app's pattern:
  - service-token access, with authorization enforced in the Helios API;
  - the browser never sees a Bolt address or the token;
  - nobody can send arbitrary Cypher.
- **Memgraph is a disposable copy** (`ontology/README.md`: "validate, snapshot to Iceberg as `ontology_version`, materialise into Memgraph"). Published versions live in the lakehouse; Memgraph is rebuilt from them. If the Application restarts, the gateway rebuilds the active version. A Memgraph data directory on project storage can speed restarts, but nothing depends on it.
- **The later crawler uses the same shape.** The crawler writes to `helios_index` in the lakehouse, and the gateway rebuilds its Memgraph graph from there. Crawler Jobs never need direct Bolt access.

## Graph model in Memgraph

- **Nodes:**
  - `(:OntologyVersion {version, hash})`
  - `(:Class {name, layer: core|pack|customer, abstract, description, kind: entity|event|asset|concept|relationship})`
  - `(:Attribute {name, range, multivalued, owner})`
  - `(:Enum)`, `(:EnumValue)`
  - `(:OssieElement {model, element})`, `(:GlossaryTerm)`
- **Edges:**
  - `IS_A`
  - `HAS_ATTRIBUTE` (declared and inherited)
  - `RANGE` (attribute → class or enum)
  - `MAPS_TO` from an Ossie element to a class, carrying its identifiers
  - `MATERIALISES_AS` from an Ossie relationship to a relationship type
  - `IN_VERSION`, so several versions can coexist while a new one is published and swapped in (blue/green, per the README)
- **Parsing:** LinkML files are read with `linkml-runtime`'s SchemaView, which resolves imports and inherited slots. The parser lives in `shared/helios_core/ontology/` so the crawler's resolver can reuse it. It produces a normalised node and edge list with a content hash, which the gateway loads.

## Viewer (Helios UI)

- **Placement:** a new **Ontology** page under *Build* in `apps/helios/ui/src/components/PrimaryNavigation.tsx`, route in `App.tsx`, requiring `ontology.read`.
- **Rendering:** React Flow (`@xyflow/react`, already used by the canvas), plus `elkjs` for automatic hierarchical layout.
- **v1 features:**
  - hierarchy view (the `is_a` tree) and relationship view (attribute ranges between classes);
  - filters by layer (core, retail pack, tenant) and by kind;
  - search;
  - an inspector showing the description, declared and inherited attributes, Ossie mappings and identifiers, and which glossary terms point at the class;
  - a version picker;
  - highlighting of broken mappings (Ossie elements that don't exist in the published model).
- The canvas's existing Ontology lens (`CanvasPage.tsx`, fed by `/api/v1/models/{id}/graph?lens=ontology`) stays as is for now. It could later read from Memgraph.

---

## Tasks

- [x] **O-0** Spike: prove Memgraph runs in a Workbench Application pod. Check install, start as a non-root user, Bolt on `127.0.0.1`, behaviour under the pod's memory limits, and restart behaviour. Try both install routes (see decision 1). *Done when:* a throwaway Application answers `RETURN 1` through a minimal HTTP gateway. — `92e7e6e`, `1691c8d`

  **Findings.** Decision 1 settled: a custom runtime image (PBJ Workbench, Python 3.11) with the Memgraph binary and a Bolt driver baked in. The `.deb`-into-project-storage route was not needed.
  - Memgraph reads `/etc/memgraph/memgraph.conf` unless `MEMGRAPH_CONFIG` says otherwise. The packaged file targets a root-run systemd service and points `--log-file` into root-owned `/var/log/memgraph`, so it exits **13 (EACCES)** under the `cdsw` user before logging anything. The Application now supplies an empty config and passes every flag explicitly.
  - `--memory-limit` is derived from the pod's cgroup ceiling (60%, leaving room for uvicorn and the O-2 parser) rather than left unbounded.
  - Data and log directories are node-local under `/tmp/helios-graph`. Memgraph is a disposable copy, and project storage is NFS — a poor fit for a database.
  - The gateway boots **degraded** rather than aborting when Memgraph will not start, so `/health` and `/v1/diagnostics` stay reachable. A pod that refuses to boot answers nothing.
  - Code was written at the O-1 location (`apps/helios/graph/`), so items 1–4 of O-1 are already in place.
- [ ] **O-1** Helios Graph Application:
  - [ ] the runtime image or install script — **deferred by choice.** The runtime is built and registered in the Runtime Catalog, but its `Dockerfile`/`build.sh` are not in the repo, so the image is only reproducible from the registry. Add `apps/helios/runtimes/helios-graph/` when convenient.
  - [x] `apps/helios/graph/app.py`, which starts Memgraph and the gateway on `CDSW_APP_PORT` — `92e7e6e`
  - [x] health and readiness endpoints — `92e7e6e`
  - [x] service-token authentication (`HELIOS_GRAPH_TOKEN`) — `92e7e6e`
  - [x] `POST /v1/ontology/{version}:materialise` (idempotent) and read endpoints
  - [x] rebuild of the active version on startup

  *Done when:* the Application starts from scratch, loads a version, and rebuilds it after a restart. **Code complete and unit-tested against a fake Bolt client (43 tests); the deployed round trip is unverified** because there is no parser yet to produce a real version. Closing this needs either a hand-written payload POSTed to `:materialise`, or O-2.

  **Design notes.**
  - The normalised graph contract lives in `shared/helios_core/ontology/`, so O-2's parser and the gateway agree on one shape and the crawler's resolver can reuse it. `content_hash` is a SHA-256 over a canonicalised form — node and edge order do not affect it, which is what makes republishing idempotent.
  - Node labels and edge types are a **closed whitelist**. Cypher cannot parameterise a label or relationship type, so the loader interpolates them; that is only safe because every one is checked against the whitelist first. Every other value is a bound parameter, and there is a test asserting no caller value reaches the statement text.
  - Versions coexist: every node carries `version` and `key`, and `IN_VERSION` links it to its `OntologyVersion` node. The blue/green swap is therefore a pointer change — `:materialise` with `activate: false`, then `:activate`.
  - A version node is marked `complete: false` while loading, so a crash mid-load is visible and gets reloaded rather than trusted.
  - **Decision 2 moved to O-2**, where the lakehouse snapshot actually happens. For O-1 the gateway keeps its own content-addressed payload cache on project storage (`$CDSW_PROJECT_DIR/.helios-graph/ontology/`) and rebuilds from it. That cache is explicitly *not* a system of record: a miss just means the publisher must materialise again.
- [x] **O-2** *(Done: `4c27153`, `a08f8c0`; mapping files are read since CR-0b, and versions are recorded in `helios_index` since CR-0c.)* Parser and publish:
  - LinkML → normalised graph in `helios_core.ontology`;
  - validation in CI: `linkml-lint` on core, pack and extension, and `linkml-validate` of mapping files against `mappings/mapping.schema.yaml`;
  - a mapping check against the published Ossie model;
  - publish means validate, content-hash, snapshot to the lakehouse, then materialise.

  *Done when:* publishing the same files twice gives the same version, and a version with a broken import or unknown mapping target is rejected.
- [x] **O-3** *(Done: `3673366`. `POST /api/v1/ontology/{version}:activate` added with CR-0c.)* Helios API router, audited like the rest of `/api/v1`:
  - `GET /api/v1/ontology/versions`
  - `GET /api/v1/ontology/{version}/graph` (filters: layer, kind, focus, depth)
  - `GET /api/v1/ontology/{version}/classes/{name}`
  - `POST /api/v1/ontology:publish` (`ontology.edit`)

  *Done when:* contract tests pass, and requests without `ontology.read` get 403.
- [x] **O-4** *(Done: `07eeb24`, `1015f48`, `a2d16f7`, then O-4b's graph view and CR-0b's mapping display.)* Ontology page in the Helios UI, as described under Viewer. *Done when:* a user can browse core → retail → tenant, open any class in the inspector, and see every Ossie mapping, with broken mappings highlighted.
- [x] **O-4b** *(Added and built 2026-10-01; graph view, uncommitted.)* The first O-4 page was a class list plus a detail panel; it drew no graph. The page now draws the ontology as a graph with React Flow (`features/ontology/`):
  - the `is_a` forest laid out left to right (arrowhead at the parent, as in UML);
  - attribute ranges drawn as dashed orange edges between classes, each attribute once, from the class that declares it;
  - classes coloured by layer, with abstract classes dashed;
  - layer filters, a toggle for the relationship edges, and search highlighting;
  - clicking a class focuses its neighbourhood and opens an inspector with description, parents, subclasses, relationships and attributes (inherited ones marked "from X").

  The parser now records each class's `layer`, `kind`, `abstract` and `description`, and each attribute's `declared_by` and `inherited`. That changes the content hash, so **republish the ontology** to see layers and kinds. Broken-mapping highlighting arrived with CR-0b (2026-10-01): the parser reads `ontology/mappings/`, the inspector lists each class's Ossie mappings, and classes with a missing element are outlined in red. *Done when:* the hierarchy and relationships are visible without selecting anything, and selecting a class shows its connections.
- [x] **O-5** *(Done: `6f5cb83`, `docs/ontology-deployment.md`.)* Docs: deployment steps for the Helios Graph Application (runtime, entry point, environment variables, token, resources) in `docs/ui-deployment.md` and the in-app documentation. *Done when:* someone else can deploy it from the docs alone.

## Problems in the ontology files to fix before the crawler depends on them

1. **The mapping doesn't match the published Ossie model.** `mappings/ossie/tpcds.yaml` uses `dim_customer`, `dim_store`, `dim_warehouse`, `dim_item` and `dim_promotion`, plus measures `net_revenue` and `return_rate`. `models/published/tpcds.ossie.yaml` uses `customer`, `store_sales` and `store_sales_revenue`, and has no `net_revenue` or `return_rate`.
2. **The retail pack lacks entity types that the Helios-DS ground truth uses:** `Sale` and `Reason`, and possibly `Brand`. The first crawler on the Helios-DS corpus will need them.
3. **The resolution scopes point at `s3://helios-ds/customers/**`** and similar, but the Helios-DS assets live under `s3a://applied-ai-buk-d5eff1ab/helios-db/source/datasets/…`.

## Decisions needed

1. ~~**How to install Memgraph.**~~ **Settled (O-0):** a custom runtime image on PBJ Workbench, Python 3.11, with the Memgraph binary and a Bolt driver baked in. The `.deb`-into-project-storage route was not needed. The runtime is registered in the Runtime Catalog; its build files are not yet in the repo (see O-1).
2. *(Decided 2026-10-01: `helios_index.ontology_versions` plus `ontology_activations`; see CR-0c.)* **Where published ontology versions are stored:** a lakehouse table such as `helios_index.ontology_versions` (next to the crawler's index, matching the README's "the active version is part of the index generation"), or Helios's own metadata store. **Deferred to O-2**, which owns the snapshot step; O-1 needs no answer because the gateway rebuilds from its own payload cache. Two things to weigh when it comes up: the Workbench reaches the lakehouse through Impala, not PyIceberg (`helios_ds.lakehouse.impala`), so the lakehouse route adds Impala config to whichever component publishes; and nothing creates `helios_index.*` today — the Helios-DS generator deliberately never does, and there is a test enforcing that.
3. **Memgraph licence:** Memgraph Community is under the Business Source Licence. That's fine for internal use; confirm it's acceptable for how Helios will be distributed.

## After this

The first crawler on the Helios-DS text corpus comes next, with its own burn-down: the `POST /v1/datasets/{id}:crawl` hook, `helios_index.*`, resolution against the ontology mapping, and projection into Memgraph through the Helios Graph gateway.
