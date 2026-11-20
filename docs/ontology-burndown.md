# Helios ontology — burndown

The plan: [ontology-plan.md](ontology-plan.md) (identity: [ontology-identity.md](ontology-identity.md)).
Moved from the Horus repo on 2026-11-20, where it was section 14 of Horus's burndown.
Each sprint ends with its outcome working through the library API, covered by tests;
check a task when its work is done and tested, with the date and the commit.

Sprint order (from the plan): S0, S1, S2, then S3 and S4, S5, S6, S7, S8, S9, S10, S11,
S14, then the first host's S12 (in the Horus repo), S13 and S15. 270 points, of which
S12 (10) and the Horus parts of S13 (8) are tracked in the Horus repo.

## Decided before S0

- [x] **D.1** The split between Helios and a host: generic screens ship from Helios as `@helios/ontology-ui`; a reusable `helios-ontology-server` reference host; the Kafka adapter in Helios; hosts own sign-in, deployment, their domain and their pipeline. *(2026-11-19, in the plan's "Helios or the host".)* The test for a host's gaps: if a different user would take the same action, Helios changes (2026-11-20).
- [x] **D.2** Identity keys gain `exclusive` and `concurrencyWindow`; source trust is declared on each source (`vouchesFor`). *(2026-11-20, from Horus's identity rules and the evidence from its lake: [ontology-identity.md](ontology-identity.md).)*
- [x] **D.3** A first semantic model from a host's lake, reviewed and shipped as product content: Horus's `horus_lake` (2026-11-20), which found Track H in [helios-decomposition-burndown.md](helios-decomposition-burndown.md).

## Open questions (from the plan)

- **Q1** Postgres as a requirement for the entity index, with SQLite for laptops, or an index plugin interface from day one? (blocks S4)
- **Q2** Do the maritime and aviation packs become Helios packs, or stay the host's? (S13)
- **Q3** Does the ontology wait for helios-crawl (phase 10), or does entity resolution start with structured identity only? (S11)


## S0 Foundations (13)

The definition exists and is versioned. Nothing can be queried yet.

- [ ] **S0.1** Stable ids, API names, display names, descriptions, status — 2
- [ ] **S0.2** Branches and change sets in the Helios registry; published revisions and activation, as for Ossie models — 3
- [ ] **S0.3** Definition schema, validation and YAML round-trip; registry migrations — 3
- [ ] **S0.4** Layering: core, packs, extensions; add-only check — 2
- [ ] **S0.5** Audit records through `AuditSink` — 1
- [ ] **S0.6** Base type catalog and primary-key and title rules, including reserved types — 2

## S1 Object types over Ossie (26)

- [ ] **S1.1** Object type with kind, primary key and title, backed by an Ossie dataset — 5
- [ ] **S1.2** Properties bound to Ossie fields, with search, sort and aggregate flags; checked against the published Ossie revision — 5
- [ ] **S1.3** Unbacked types for action-created objects — 2
- [ ] **S1.4** Shared properties — 3
- [ ] **S1.5** Value types with constraints and versions — 4
- [ ] **S1.6** Metrics bound to object types from Ossie metrics — 2
- [ ] **S1.7** Identity keys on entity types: scheme, strength, scope, validity, `exclusive` and `concurrencyWindow` (D.2); a value-check hook a pack supplies; `maxSpeed` on `Located` types; stored as identifier rows with validity and status (`active`, `contested`, `ended`) — 3
- [ ] **S1.8** Time-series observation types: series key, sample time, values with units, default downsampling — 3

## S2 Links and interfaces (23)

- [ ] **S2.1** Link types with two named sides; side names unique per type — 3
- [ ] **S2.2** Foreign-key links, reusing verified Ossie joins — 3
- [ ] **S2.3** Join-table links, including a generated empty table — 3
- [ ] **S2.4** Object-backed links and their many-to-one prerequisites — 3
- [ ] **S2.5** Several links per pair; self-links — 1
- [ ] **S2.6** Interfaces: extends, implements, conformance check — 4
- [ ] **S2.7** Link and action constraints on interfaces — 2
- [ ] **S2.8** Core interfaces `Actor`, `Located` (geopoint optional, local frame, `containedIn`) and `Occurrence` — 2
- [ ] **S2.9** `OntologyGraph` and LinkML generated from the definition (compatibility with Helios's stable contracts) — 2

## S3 Proposals and review for the definition (13)

- [ ] **S3.1** Propose object types and kinds from harvest and profile — 4
- [ ] **S3.2** Propose foreign-key links from verified joins; shared properties from matching fields — 3
- [ ] **S3.3** Review: accept, edit, reject each proposal; publish — 4
- [ ] **S3.4** Round-trip test: warehouse to published definition with no hand-written YAML — 2

## S4 Entity storage (21)

- [ ] **S4.1** Indexer from an Ossie dataset through a Helios engine, with an explicit service grant — 5
- [ ] **S4.2** Upsert interface for stream-fed base rows — 2
- [ ] **S4.3** Edit log stored apart from base rows — 5
- [ ] **S4.4** Read merge: overrides, creates, deletes; post-commit index update — 5
- [ ] **S4.5** Copy of the edit and action logs to Iceberg; optional materialization — 2
- [ ] **S4.6** Dataset replacement: drop or move edits — 2

## S5 Object sets (25)

- [ ] **S5.1** Object set structure: base, interface, filter, search-around, time range, set operations — 4
- [ ] **S5.2** Filters, including geopoint bounding box — 4
- [ ] **S5.3** Observation sets compiled through the Helios compiler, run as the caller; time bound required — 5
- [ ] **S5.4** Search-around across all backings and across the entity/observation boundary — 3
- [ ] **S5.5** Aggregations: count, sum, min, max, grouped; metrics through Ossie — 3
- [ ] **S5.6** Sort, page, project — 2
- [ ] **S5.7** `traverse` with depth and `asOf`, on the graph view or recursive SQL — 3
- [ ] **S5.8** `within` over `containedIn`, transitive — 1

## S6 Actions (24)

- [ ] **S6.1** Parameters and their five sources — 3
- [ ] **S6.2** Rules folded to one edit per object, including interface variants and links created with a new object — 7
- [ ] **S6.3** Illegal combinations rejected; later write wins on one property — 2
- [ ] **S6.4** Submission criteria, operators and failure messages — 4
- [ ] **S6.5** Machine actors and proposal-only action types — 2
- [ ] **S6.6** Transactional apply and the action log — 3
- [ ] **S6.7** State machines: states and transitions bound to action types; other writes to the status rejected — 3

## S7 Functions (13)

- [ ] **S7.1** Versioned registry and publish — 3
- [ ] **S7.2** Query functions over object sets — 3
- [ ] **S7.3** Edit functions return a batch; a direct run writes nothing; the pre-batch search rule tested — 4
- [ ] **S7.4** Exclusive function rule, edits declaration checked, version pin; runner interface the host implements — 3

## S8 Governance (16)

- [ ] **S8.1** Viewer, editor and owner on the definition — 3
- [ ] **S8.2** Object and property policies with the weight limit, evaluated against `Caller.attributes` — 5
- [ ] **S8.3** Enforcement inside object sets; primary key excluded; hidden versus nulled — 2
- [ ] **S8.4** Policies compiled to Ranger row filters and column masks where expressible (helios-cloudera) — 3
- [ ] **S8.5** Marking hook with an allow-all evaluator — 2
- [ ] **S8.6** Policy test as a chosen user — 1

## S9 Side effects, commit stream and Kafka adapter (11)

- [ ] **S9.1** Before and after webhooks with the failure rules — 3
- [ ] **S9.2** Notifications from pre-edit state, after commit — 2
- [ ] **S9.3** Commit stream interface; usage counters — 2
- [ ] **S9.4** Criteria test run that writes nothing — 1
- [ ] **S9.5** Kafka adapter in Helios: entity upserts through identity keys; commit stream to a topic — 3

## S10 Tools and reference server (16)

- [ ] **S10.1** Ontology tools in helios-serve; served by helios-mcp — 6
- [ ] **S10.2** `helios-ontology-server`: HTTP API over object sets, objects, actions and the definition; OpenAPI — 5
- [ ] **S10.3** Sign-in and caller-attribute interface that each host implements — 3
- [ ] **S10.4** TypeScript client (`@helios/ontology-client`) generated from the OpenAPI; S14 builds on it — 2

## S11 Instance proposals and entity resolution (15)

- [ ] **S11.1** Crawler records (entities, mentions, claims, evidence) loaded through actions — 4
- [ ] **S11.2** SameAsAssertion from `PossiblySameAs`, proposal-only, with evidence — 4
- [ ] **S11.3** Merge of two entities as a function-backed action — 3
- [ ] **S11.4** Structured identity from declared identity keys ([ontology-identity.md](ontology-identity.md#resolution)): strong match links, new strong key creates a provisional entity, weak evidence proposes, a strong key from a source that doesn't vouch for its scheme (`vouchesFor`, D.2) proposes, an exclusive key claimed by two entities within its window is contested, conflicting permanent keys are flagged — 5

## S12 Horus host (10)

- [ ] The first host's integration, tracked in the Horus repo (`docs/ONTOLOGY.md`, BURNDOWN.md O13). — 10


## S13 Seeds (18)

- [ ] **S13.1** OSINT pack: interfaces, types, links, value types, actions; crawler records mapped — 5
- [ ] **S13.2** Horus extension over the lake and `ref_*` tables, with its metrics and actions — 6 *(in the Horus repo)*
- [ ] **S13.3** Ask Horus tier 1 reads an object set (Horus burndown 11.4) — 2 *(in the Horus repo)*
- [ ] **S13.4** Manufacturing test pack with its synthetic dataset; the four acceptance questions pass, and its objects render in the S14 components, with no core or component change — 5

## S14 Ontology components (21)

Generic screens, rendered from the definition. Runs after S10 and S11 and before the Horus host, which embeds them.

- [ ] **S14.1** `@helios/ontology-ui` package: React 18 and 19 peers, Next.js client components, theme tokens, a renderer registry for property types and link views — 3
- [ ] **S14.2** Object view: title, visible properties, links, metrics, applicable actions; hidden objects and nulled properties shown as policy says — 4
- [ ] **S14.3** Object-set table and explorer: search, filter, sort, search-around, saved explorations — 4
- [ ] **S14.4** Action form: parameters from the action, criteria messages, proposal or commit, state-machine transitions — 4
- [ ] **S14.5** Review queue for definition and instance proposals: accept, edit, reject — 3
- [ ] **S14.6** Object history and explain (edits, sources, lineage); read-only definition browser — 2
- [ ] **S14.7** Tests against the S0–S11 fixtures, with two unrelated object types and no domain code — 1

## S15 Reference console (5)

A usable UI for a new host, after the components are proven in Horus.

- [ ] **S15.1** Console shell: navigation over object types, explorer, review queue, definition browser; the caller from the server's sign-in plug-in — 3
- [ ] **S15.2** Served as static files by `helios-ontology-server` — 1
- [ ] **S15.3** Test on the manufacturing test pack with the DuckDB sample — 1
