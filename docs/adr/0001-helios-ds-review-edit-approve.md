# ADR 0001: Review, edit and approve in Helios-DS-Generation

- **Status:** Accepted, 2026-09-30
- **Deciders:** Helios-DS project owner
- **Burn-down task:** R-01 (`docs/helios-ds-burndown.md`)
- **Amended 2026-09-30:** stage C (curation overlays) and the S-04 roles are cut. Helios-DS-Generation now produces one text-first development corpus for the Helios crawler, so corrections are made by editing templates and regenerating (stage B). Decisions 1, 4, 5, 6 and 7 stand; decisions 2 (stage C) and 3 no longer apply.

## Context

The Helios-DS specification requires reproducibility: the same TPC-DS snapshot, configuration, seed, template bundle and generator version must produce the same entities, claims, manifests and (on the reference runtime) artifact bytes. Anything not produced that way must be frozen and versioned before it enters the benchmark.

The project also needs people to review generated assets, correct them, and approve or reject datasets before the Helios crawler may see them.

Editing a published dataset in place would break reproducibility, would invalidate the hidden ground truth (whose evidence locators point at exact pages, characters and messages), and is blocked by the object store's write-if-hash-matches rule. The following already exist:

- **Lifecycle:** `CREATING → VALIDATING → IN_REVIEW → READY`, with `FAILED` and `REJECTED` as exits; only `READY` datasets are crawler-visible.
- **Approval:** `approve()`, which re-validates before `READY`.
- **Identity:** a `dataset_id` derived from all generation inputs.

## Decision

### 1. Edits never modify a dataset; an edited dataset is a new dataset

A dataset's files, rows and ground truth are immutable once published. Every correction produces a new dataset with its own `dataset_id`, rendered, validated and reviewed like any other. The original stays as it was, for audit and comparison.

### 2. Delivered in two stages

**Stage B, "reject and regenerate" (now).** Reviewers review a dataset, then approve or reject it. Problems are fixed at the source: config, templates or phrase banks, all in Git. Regenerating then produces a new dataset. This needs no new generation machinery. It adds review screens, advisory flags and comments, approve and reject in the UI, superseding, and an audit trail.

**Stage C, "curation overlays" (later).** Reviewers can correct individual assets through overlays: versioned, declarative change records applied on top of a base dataset. All four overlay kinds are in scope:

| Kind | Example | Effect |
|---|---|---|
| **Exclude** | Drop one email | The artifact and its ground truth are omitted; golden queries citing it are flagged for update |
| **Presentation override** | Scenario Y's email uses the polite tone; never use phrase Z | Affected artifacts re-render with the directive. Overrides may change wording, tone, layout or difficulty tier, **never TPC-DS facts** |
| **Metadata change** | Restrict a PDF to `legal`; mark a claim `INTENDED_AMBIGUOUS` | ACL or ground-truth rows change; artifact bytes do not |
| **Pinned replacement** | A reviewer uploads a corrected PDF | Stored as a frozen artifact with provenance (who, when, why, original hash). Its ground truth must be supplied with it and pass locator validation; it is the last kind to be built |

The overlay bundle, the ordered overlay records of one edited dataset, is hashed canonically. That hash is added to the generation identity, so base inputs plus the same bundle always reproduce the same edited dataset. The edited dataset records its **parent** `dataset_id` and **overlay bundle hash**. Operational fields (author, timestamps, comments) are recorded but excluded from the hash.

### 3. Overlays are stored in the lakehouse

Overlays live in a new `helios_ds.overlays` table (one row per overlay record, keyed by bundle), written through the same Impala path as the other `helios_ds` tables. The UI can then create overlays directly, and they're governed by the same Ranger policies. Template and phrase-bank changes (stage B) remain in Git, where the spec places governed definitions.

### 4. An approved dataset supersedes earlier ones in its lineage

A **lineage** is the set of datasets generated from the same generation config (same `config_hash`), plus any datasets derived from them through overlays (which inherit their parent's lineage). Approving a dataset moves every other `READY` dataset in its lineage to a new terminal state, **`SUPERSEDED`**, so exactly one dataset per lineage is `READY` and crawler-visible.

A stage-B fix (new templates, same config) is therefore a new dataset in the same lineage, and its approval retires the previous version. Changing the config itself (for example, the counts or the scenario mix) starts a new lineage.

Lifecycle after this decision:

```
CREATING → VALIDATING → IN_REVIEW → READY → SUPERSEDED
                 ↓            ↓
               FAILED      REJECTED
```

### 5. Review flags are advisory

Reviewers can accept, flag or comment on individual artifacts. Flags and comments are recorded and shown, but **do not block approval**. Approval is still blocked by failed validation, as `approve()` already enforces.

### 6. The Workbench user approves

Until the security milestone (S-04) introduces reviewer and approver roles, any authenticated Workbench user of the generation project may approve or reject. The approver's identity is taken from the authenticated user that the Workbench Application proxy passes to the API, and recorded with the lifecycle event and the audit trail. How the proxy exposes that identity is verified first in R-06; if it isn't available, the API must refuse approvals rather than record an anonymous approver.

### 7. Isolation

Review screens show hidden ground truth, so they run only in the generation project's Application, never where crawler or Helios query credentials exist (see S-05).

## Consequences

- **Reproducible:** every published dataset, edited or not, can be regenerated from recorded inputs. Stage B works with the existing pipeline.
- **Storage duplication:** each edited dataset stores a full copy of its artifacts under its own `dataset_id` (artifact IDs are namespaced by dataset). This is negligible for text artifacts (a few MB per dataset); for video (phase 6), content-addressed storage may be introduced to avoid duplication.
- **Lifecycle changes:** a new `SUPERSEDED` state; `approve()` gains superseding and an approver identity; a `reject()` operation is added.
- **Pinned replacements are costly:** they carry the most risk (hand-supplied ground truth), so they're built last within stage C and must pass the same validation as generated artifacts.
- **Golden queries:** exclusions can orphan golden queries; the edit workflow must detect and flag them.

## Alternatives considered

- **Edit in place:** rejected. It breaks reproducibility and ground truth, conflicts with the object store's no-overwrite rule, and violates the spec.
- **Reject and regenerate only, permanently:** kept as stage B, but insufficient alone, because it can't fix a single artifact without changing every artifact from the same template.
- **Overlays stored in Git:** rejected for overlay records. Pull-request review of every curation edit is too heavy, and the UI couldn't write them directly. Git remains the home of templates and phrase banks.
