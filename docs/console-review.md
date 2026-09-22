# Reviewing and publishing a proposal

Open it from **Runs → a run → Review and publish**, or from the proposal page.

Every element of the proposal — each dataset, column, relationship, metric and proposed glossary term — carries one of
four states, shown as a coloured bar on its row: **pending** (grey), **accepted** (green), **rejected** (red),
**edited** (orange; accepted with your changes). Only accepted and edited elements go into the published model.
Decisions are saved to `runs/<run-id>/review.json` as you click; the proposal itself is never modified.

## Making decisions

Three levels, and a later decision always overrides an earlier one:

- **Accept all pending with confidence ≥ N** — the bar at the top. Applies to every pending element in every section.
  Relationships the model itself rejected are excluded; accept those individually if you disagree with the model.
- **accept all / reject all** on a dataset — applies to the dataset and every one of its columns.
- **✓ / ✗ / edit** on a row — one element.

**edit** opens the editable fields inline: business name, kind and description for a dataset; business name, role,
description and *refers to* for a column; target table and column for a relationship; name, expression and description
for a metric; name and definition for a term. *Save as edit* records the changes and marks the element edited.

Metrics that cannot be published as written (an expression that reaches into another table, or an unknown column) are
flagged in red on the page. Publishing is blocked while any such metric is accepted: reject it or fix the expression.

**Reset all decisions** clears `review.json` for this run.

## Publishing

**Publish** applies the decisions, builds the Ossie document, validates it against the vendored spec and the model's own
referential rules, and — only if it validates — writes `models/published/<database>.ossie.yaml` (plus `.json` and a
`.publish.json` record of which run and review produced it). Validation failures are listed on the page and nothing is
written. Tick **git commit** to commit the published model and the review file in one step.

Publishing the semantic model does not touch Atlas. Accepted glossary terms are pushed to Atlas by a separate step
(not yet built).