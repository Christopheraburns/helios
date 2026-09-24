import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import {
  ProposalCollection,
  ProposalItem,
  ReviewDecision,
  ReviewSection,
  ReviewSummary,
} from "../api/client";
import { ApplicationContextState } from "../hooks/useApplicationContext";

const sections: ReviewSection[] = [
  "datasets",
  "fields",
  "relationships",
  "metrics",
  "glossary_terms",
];
const sectionLabels: Record<ReviewSection, string> = {
  datasets: "Datasets",
  fields: "Fields",
  relationships: "Relationships",
  metrics: "Metrics",
  glossary_terms: "Glossary terms",
};
const decisions: ReviewDecision[] = ["pending", "accept", "edit", "reject"];
const pageSize = 25;

interface ReviewWorkspaceProps {
  context: ApplicationContextState;
  runId: string;
}

export default function ReviewWorkspace({
  context,
  runId,
}: ReviewWorkspaceProps) {
  const modelId = context.selectedModelId;
  const location = useLocation();
  const [summary, setSummary] = useState<ReviewSummary>();
  const [collection, setCollection] = useState<ProposalCollection>();
  const [section, setSection] = useState<ReviewSection>("datasets");
  const [decision, setDecision] = useState<ReviewDecision | "">("");
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [busyId, setBusyId] = useState("");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [threshold, setThreshold] = useState(0.85);

  const loadCollection = useCallback(
    async (offset = 0, append = false) => {
      if (!modelId) return;
      append ? setLoadingMore(true) : setLoading(true);
      setError("");
      try {
        const result = await context.loadModelRunProposals(modelId, runId, {
          section,
          ...(decision ? { decision } : {}),
          ...(query ? { query } : {}),
          offset,
          limit: pageSize,
        });
        setCollection((current) =>
          append && current
            ? { ...result, items: [...current.items, ...result.items] }
            : result,
        );
      } catch (reason) {
        setError(messageOf(reason, "Proposals could not be loaded."));
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [context.loadModelRunProposals, decision, modelId, query, runId, section],
  );

  const refreshSummary = useCallback(async () => {
    if (!modelId) return;
    const result = await context.loadModelReview(modelId, runId);
    setSummary(result);
  }, [context.loadModelReview, modelId, runId]);

  useEffect(() => {
    void Promise.all([refreshSummary(), loadCollection()]).catch((reason) => {
      setError(messageOf(reason, "The review workspace could not be loaded."));
      setLoading(false);
    });
  }, [loadCollection, refreshSummary]);

  async function afterMutation(nextSummary: ReviewSummary, message: string) {
    setSummary(nextSummary);
    setFeedback(message);
    await Promise.all([loadCollection(), refreshSummary()]);
    context.refreshModelOverview();
  }

  async function decide(
    item: ProposalItem,
    nextDecision: "accept" | "reject" | "edit",
    overrides?: Record<string, unknown>,
    note?: string,
  ) {
    if (!modelId) return;
    setBusyId(item.id);
    setError("");
    try {
      const result = await context.decideModelProposal(modelId, runId, {
        section: item.section,
        element_id: item.id,
        decision: nextDecision,
        overrides,
        note,
      });
      await afterMutation(result.summary, `Saved ${nextDecision} decision.`);
    } catch (reason) {
      setError(messageOf(reason, "The decision could not be saved."));
    } finally {
      setBusyId("");
    }
  }

  async function cascade(
    item: ProposalItem,
    nextDecision: "accept" | "reject",
  ) {
    if (!modelId || item.section !== "datasets") return;
    setBusyId(item.id);
    setError("");
    try {
      const result = await context.decideModelDataset(
        modelId,
        runId,
        item.proposal.table,
        nextDecision,
      );
      await afterMutation(
        result.summary,
        `${nextDecision === "accept" ? "Accepted" : "Rejected"} the dataset and ${Math.max((result.changed ?? 1) - 1, 0)} fields.`,
      );
    } catch (reason) {
      setError(messageOf(reason, "The dataset decision could not be saved."));
    } finally {
      setBusyId("");
    }
  }

  async function bulkAccept() {
    if (!modelId) return;
    setBusyId("bulk");
    setError("");
    try {
      const result = await context.bulkAcceptModelProposals(
        modelId,
        runId,
        threshold,
      );
      await afterMutation(
        result.summary,
        `Accepted ${result.changed ?? 0} pending proposals.`,
      );
    } catch (reason) {
      setError(messageOf(reason, "Bulk acceptance failed."));
    } finally {
      setBusyId("");
    }
  }

  async function reset() {
    if (
      !modelId ||
      !window.confirm(
        `Clear all review decisions in ${sectionLabels[section]}? This cannot be undone.`,
      )
    ) {
      return;
    }
    setBusyId("reset");
    setError("");
    try {
      const result = await context.resetModelReview(modelId, runId, section);
      await afterMutation(
        result.summary,
        `Reset decisions in ${sectionLabels[section]}.`,
      );
    } catch (reason) {
      setError(messageOf(reason, "Review decisions could not be reset."));
    } finally {
      setBusyId("");
    }
  }

  async function publish() {
    if (!modelId) return;
    setBusyId("publish");
    setError("");
    try {
      await context.publishModelReview(modelId, runId);
      await refreshSummary();
      await loadCollection();
      context.refreshModelOverview();
      setFeedback("The reviewed model was published successfully.");
    } catch (reason) {
      setError(messageOf(reason, "Publication failed validation."));
    } finally {
      setBusyId("");
    }
  }

  function applyQuery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setQuery(queryInput.trim());
  }

  const canMutate = Boolean(
    collection?.available_actions.includes("decide"),
  );
  const totalPending = useMemo(
    () =>
      summary
        ? sections.reduce(
            (total, item) => total + summary.sections[item].pending,
            0,
          )
        : 0,
    [summary],
  );

  return (
    <section className="review-workspace" aria-labelledby="review-title">
      <div className="review-workspace__header">
        <div>
          <p className="section-eyebrow">Human review</p>
          <h2 id="review-title">Proposal workspace</h2>
          <p>
            Review proposed semantic elements before publishing. Dimensions are
            represented as field roles, not separate review items.
          </p>
        </div>
        {summary && (
          <span className="count-badge">{totalPending} pending</span>
        )}
      </div>

      {summary && (
        <>
          <div className="review-section-tabs" aria-label="Proposal sections" role="group">
            {sections.map((item) => {
              const counts = summary.sections[item];
              return (
                <button
                  aria-pressed={section === item}
                  className={section === item ? "review-section-tab review-section-tab--active" : "review-section-tab"}
                  id={`review-tab-${item}`}
                  key={item}
                  onClick={() => setSection(item)}
                  type="button"
                >
                  <strong>{sectionLabels[item]}</strong>
                  <span>{counts.total} total · {counts.pending} pending</span>
                </button>
              );
            })}
          </div>

          <div className="review-counts" aria-label={`${sectionLabels[section]} decision counts`}>
            {decisions.map((item) => (
              <span key={item}>
                <strong>{summary.sections[section][item]}</strong> {item}
              </span>
            ))}
          </div>
        </>
      )}

      <div className="review-controls">
        <label>
          Decision
          <select
            value={decision}
            onChange={(event) =>
              setDecision(event.currentTarget.value as ReviewDecision | "")
            }
          >
            <option value="">All decisions</option>
            {decisions.map((item) => (
              <option key={item} value={item}>{label(item)}</option>
            ))}
          </select>
        </label>
        <form onSubmit={applyQuery} role="search">
          <label htmlFor="proposal-query">Search this section</label>
          <div>
            <input
              id="proposal-query"
              maxLength={200}
              onChange={(event) => setQueryInput(event.currentTarget.value)}
              placeholder="Name, table, column…"
              value={queryInput}
            />
            <button className="button button--secondary" type="submit">Search</button>
          </div>
        </form>
        {query && (
          <button
            className="text-button"
            onClick={() => {
              setQueryInput("");
              setQuery("");
            }}
            type="button"
          >
            Clear search
          </button>
        )}
      </div>

      {summary && (
        <div className="review-operations">
          <label>
            Accept pending at confidence ≥
            <input
              aria-label="Minimum confidence"
              max={1}
              min={0}
              onChange={(event) => {
                const value = event.currentTarget.valueAsNumber;
                if (Number.isFinite(value)) setThreshold(value);
              }}
              required
              step={0.05}
              type="number"
              value={threshold}
            />
          </label>
          <button
            className="button button--secondary"
            disabled={
              !summary.available_actions.includes("bulk_accept") ||
              !Number.isFinite(threshold) ||
              busyId !== ""
            }
            onClick={() => void bulkAccept()}
            type="button"
          >
            Accept above threshold
          </button>
          <button
            className="button button--secondary"
            disabled={!summary.available_actions.includes("reset") || busyId !== ""}
            onClick={() => void reset()}
            type="button"
          >
            Reset section
          </button>
        </div>
      )}

      <div aria-live="polite">
        {error && <p className="review-feedback review-feedback--error">{error}</p>}
        {!error && feedback && <p className="review-feedback review-feedback--success">{feedback}</p>}
      </div>

      <div
        aria-labelledby={`review-tab-${section}`}
        id="proposal-list"
        role="region"
      >
        {loading ? (
          <div className="activity-loading" role="status">
            <span className="spinner" aria-hidden="true" />
            Loading proposals…
          </div>
        ) : collection?.items.length ? (
          <ul className="proposal-list">
            {collection.items.map((item) => (
              <ProposalRow
                busy={busyId === item.id}
                canCascade={collection.available_actions.includes("cascade")}
                canMutate={canMutate}
                item={item}
                key={item.id}
                locationSearch={location.search}
                metricWarning={
                  item.section === "metrics"
                    ? summary?.preflight_issues[item.proposal.name]
                    : undefined
                }
                onCascade={cascade}
                onDecision={decide}
              />
            ))}
          </ul>
        ) : (
          <p className="review-empty">No proposals match these filters.</p>
        )}
      </div>

      {collection?.page.has_more && (
        <button
          className="button button--secondary review-load-more"
          disabled={loadingMore}
          onClick={() => void loadCollection(collection.items.length, true)}
          type="button"
        >
          {loadingMore ? "Loading…" : `Load more (${collection.items.length} of ${collection.page.total})`}
        </button>
      )}

      {summary && (
        <aside className="publish-panel" aria-labelledby="publish-title">
          <div>
            <p className="section-eyebrow">Publication</p>
            <h3 id="publish-title">
              {summary.publish_ready ? "Ready to publish" : "Review required"}
            </h3>
            <p>
              {summary.reviewed_at
                ? `Last reviewed ${formatDateTime(summary.reviewed_at)} by ${summary.reviewed_by ?? "unknown reviewer"}.`
                : "No reviewer decisions have been recorded."}
            </p>
            {summary.publication && (
              <p className="publish-panel__published">
                Published{publicationDate(summary.publication)}.
              </p>
            )}
          </div>
          <button
            className="button button--primary"
            disabled={
              !summary.publish_ready ||
              !summary.available_actions.includes("publish") ||
              busyId !== ""
            }
            onClick={() => void publish()}
            type="button"
          >
            {busyId === "publish" ? "Publishing…" : "Publish reviewed model"}
          </button>
          {(Object.keys(summary.preflight_issues).length > 0 ||
            summary.validation_errors.length > 0) && (
            <div className="publish-panel__issues">
              {Object.entries(summary.preflight_issues).map(([metric, issue]) => (
                <p key={metric}><strong>{metric}:</strong> {issue}</p>
              ))}
              {summary.validation_errors.length > 0 && (
                <>
                  <h4>Validation feedback</h4>
                  <ul>
                    {summary.validation_errors.map((item, index) => (
                      <li key={`${item}-${index}`}>{item}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
        </aside>
      )}
    </section>
  );
}

function ProposalRow({
  item,
  busy,
  canMutate,
  canCascade,
  locationSearch,
  metricWarning,
  onDecision,
  onCascade,
}: {
  item: ProposalItem;
  busy: boolean;
  canMutate: boolean;
  canCascade: boolean;
  locationSearch: string;
  metricWarning?: string;
  onDecision: (
    item: ProposalItem,
    decision: "accept" | "reject" | "edit",
    overrides?: Record<string, unknown>,
    note?: string,
  ) => Promise<void>;
  onCascade: (
    item: ProposalItem,
    decision: "accept" | "reject",
  ) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const canvasSearch = new URLSearchParams(locationSearch);
  canvasSearch.set("review_run_id", item.canvas.review_run_id);
  canvasSearch.set("lens", item.canvas.lens);
  canvasSearch.set("focus_node_id", item.canvas.focus_node_id);
  canvasSearch.set("element_id", item.canvas.element_id);
  if (item.canvas.related_node_ids?.length) {
    canvasSearch.set("related_node_ids", item.canvas.related_node_ids.join(","));
  }
  const allowed = (action: "accept" | "reject" | "edit") =>
    canMutate && item.available_actions.includes(action);

  return (
    <li className={`proposal-row proposal-row--${item.review.decision}`}>
      <div className="proposal-row__main">
        <div>
          <div className="proposal-row__heading">
            <h3>{proposalTitle(item)}</h3>
            <span className={`decision-badge decision-badge--${item.review.decision}`}>
              {label(item.review.decision)}
            </span>
            {item.section === "relationships" && item.proposal.accepted === false && (
              <span className="model-rejected-badge">Rejected by model</span>
            )}
          </div>
          <p className="proposal-row__identity">{proposalIdentity(item)}</p>
          <ProposalDetails item={item} />
          {metricWarning && (
            <p className="proposal-warning"><strong>Metric preflight:</strong> {metricWarning}</p>
          )}
          <p className="proposal-row__audit">
            Confidence: {formatConfidence(item.confidence)} · Source: {item.provenance.source ?? "not recorded"}
            {llmSummary(item.provenance.llm)}
          </p>
          {item.review.note && <p className="proposal-note">Reviewer note: {item.review.note}</p>}
        </div>
        <div className="proposal-row__actions" aria-label={`Actions for ${proposalTitle(item)}`}>
          <button
            className="button button--secondary"
            disabled={busy || !allowed("accept")}
            onClick={() => void onDecision(item, "accept")}
            type="button"
          >
            Approve
          </button>
          <button
            className="button button--secondary"
            disabled={busy || !allowed("reject")}
            onClick={() => void onDecision(item, "reject")}
            type="button"
          >
            Reject
          </button>
          <button
            className="button button--secondary"
            disabled={busy || !allowed("edit")}
            onClick={() => setEditing((value) => !value)}
            type="button"
            aria-expanded={editing}
          >
            Edit
          </button>
          {item.available_actions.includes("view_in_canvas") && (
            <Link
              className="button button--secondary"
              to={{ pathname: "/canvas", search: `?${canvasSearch}` }}
            >
              View in Canvas
            </Link>
          )}
        </div>
      </div>
      {item.section === "datasets" && canCascade && (
        <div className="proposal-row__cascade">
          <span>Apply to dataset and all fields:</span>
          <button disabled={busy} onClick={() => void onCascade(item, "accept")} type="button">
            Approve all
          </button>
          <button disabled={busy} onClick={() => void onCascade(item, "reject")} type="button">
            Reject all
          </button>
        </div>
      )}
      {editing && (
        <ProposalEditor
          item={item}
          onCancel={() => setEditing(false)}
          onSave={async (overrides, note) => {
            await onDecision(item, "edit", overrides, note);
            setEditing(false);
          }}
        />
      )}
    </li>
  );
}

function ProposalDetails({ item }: { item: ProposalItem }) {
  switch (item.section) {
    case "datasets":
      return (
        <p>{item.proposal.description || "No description."} {item.proposal.kind && <span>Kind: {item.proposal.kind}.</span>}</p>
      );
    case "fields":
      return (
        <p>
          <strong>Semantic role:</strong> {item.proposal.role ?? "unassigned"} · Type: {item.proposal.type ?? "unknown"}
          {item.proposal.refers_to ? ` · Refers to ${item.proposal.refers_to}` : ""}
          {item.proposal.description ? ` — ${item.proposal.description}` : ""}
        </p>
      );
    case "relationships":
      return (
        <p>
          {item.proposal.from}.{item.proposal.from_column} → {item.proposal.to}.{item.proposal.to_column}
          {item.proposal.reason ? ` — ${item.proposal.reason}` : ""}
        </p>
      );
    case "metrics":
      return (
        <p>
          Dataset: {item.proposal.dataset} · Expression: <code>{item.proposal.expression || "—"}</code>
          {item.proposal.description ? ` — ${item.proposal.description}` : ""}
        </p>
      );
    case "glossary_terms":
      return (
        <p>
          {item.proposal.definition || "No definition."}
          {item.proposal.columns?.length ? ` Columns: ${item.proposal.columns.join(", ")}.` : ""}
        </p>
      );
  }
}

function ProposalEditor({
  item,
  onSave,
  onCancel,
}: {
  item: ProposalItem;
  onSave: (overrides: Record<string, unknown>, note: string) => Promise<void>;
  onCancel: () => void;
}) {
  const original = { ...item.proposal, ...(item.review.overrides ?? {}) };
  const [values, setValues] = useState<Record<string, string>>(() =>
    editorFields(item).reduce(
      (result, field) => ({
        ...result,
        [field.name]: valueString(original[field.name as keyof typeof original]),
      }),
      {},
    ),
  );
  const [note, setNote] = useState(item.review.note ?? "");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const overrides: Record<string, unknown> = { ...values };
    if (item.section === "fields" && values.refers_to === "") {
      overrides.refers_to = null;
    }
    try {
      await onSave(overrides, note);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="proposal-editor" onSubmit={submit}>
      <h4>Edit {sectionLabels[item.section].toLowerCase()} proposal</h4>
      <div className="proposal-editor__fields">
        {editorFields(item).map((field) => (
          <label key={field.name}>
            {field.label}
            {field.type === "textarea" ? (
              <textarea
                onChange={(event) => setValues({ ...values, [field.name]: event.currentTarget.value })}
                required={field.required}
                value={values[field.name] ?? ""}
              />
            ) : field.options ? (
              <select
                onChange={(event) => setValues({ ...values, [field.name]: event.currentTarget.value })}
                value={values[field.name] ?? ""}
              >
                {field.options.map((option) => <option key={option}>{option}</option>)}
              </select>
            ) : (
              <input
                onChange={(event) => setValues({ ...values, [field.name]: event.currentTarget.value })}
                required={field.required}
                value={values[field.name] ?? ""}
              />
            )}
          </label>
        ))}
        <label>
          Reviewer note
          <textarea maxLength={2000} onChange={(event) => setNote(event.currentTarget.value)} value={note} />
        </label>
      </div>
      <div className="proposal-editor__actions">
        <button className="button button--primary" disabled={saving} type="submit">
          {saving ? "Saving…" : "Save edit"}
        </button>
        <button className="button button--secondary" disabled={saving} onClick={onCancel} type="button">
          Cancel
        </button>
      </div>
    </form>
  );
}

interface EditorField {
  name: string;
  label: string;
  type?: "textarea";
  required?: boolean;
  options?: string[];
}

function editorFields(item: ProposalItem): EditorField[] {
  switch (item.section) {
    case "datasets":
      return [
        { name: "name", label: "Business name", required: true },
        { name: "kind", label: "Kind", options: ["fact", "dimension", "bridge", "lookup", "other"] },
        { name: "description", label: "Description", type: "textarea" },
      ];
    case "fields":
      return [
        { name: "name", label: "Business name", required: true },
        { name: "role", label: "Semantic role", options: ["identifier", "foreign_key", "time", "measure", "dimension", "attribute"] },
        { name: "refers_to", label: "Refers to (database.table.column or blank)" },
        { name: "description", label: "Description", type: "textarea" },
      ];
    case "relationships":
      return [
        { name: "to", label: "Target table", required: true },
        { name: "to_column", label: "Target column", required: true },
      ];
    case "metrics":
      return [
        { name: "name", label: "Name", required: true },
        { name: "expression", label: `Expression (aggregate over ${item.proposal.dataset} columns)`, required: true },
        { name: "description", label: "Description", type: "textarea" },
      ];
    case "glossary_terms":
      return [
        { name: "name", label: "Term", required: true },
        { name: "definition", label: "Definition", type: "textarea", required: true },
      ];
  }
}

function proposalTitle(item: ProposalItem): string {
  switch (item.section) {
    case "datasets":
    case "fields":
    case "metrics":
    case "glossary_terms":
      return item.proposal.name || item.id;
    case "relationships":
      return `${item.proposal.from_column} → ${item.proposal.to_column}`;
  }
}

function proposalIdentity(item: ProposalItem): string {
  switch (item.section) {
    case "datasets":
      return item.proposal.table;
    case "fields":
      return `${item.proposal.table}.${item.proposal.column}`;
    case "metrics":
      return item.proposal.dataset;
    case "relationships":
      return item.id;
    case "glossary_terms":
      return item.proposal.columns?.join(", ") || item.id;
  }
}

function llmSummary(value: Record<string, unknown> | null): string {
  if (!value) return "";
  const provider = typeof value.provider === "string" ? value.provider : "";
  const model = typeof value.model === "string" ? value.model : "";
  const name = [provider, model].filter(Boolean).join(" / ");
  return name ? ` · Model: ${name}` : "";
}

function valueString(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function formatConfidence(value: number | null): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "not recorded";
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function publicationDate(value: Record<string, unknown>): string {
  return typeof value.published_at === "string"
    ? ` ${formatDateTime(value.published_at)}`
    : "";
}

function label(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1).replaceAll("_", " ");
}

function messageOf(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}
