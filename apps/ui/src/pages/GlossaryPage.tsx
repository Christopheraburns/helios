import {
  FormEvent,
  useCallback,
  useEffect,
  useState,
} from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import {
  GlossaryAssignableAsset,
  GlossaryTerm,
  GlossaryTermDetail,
  GlossaryTermWrite,
  ModelGlossary,
  ProposalItem,
  ReviewDecision,
} from "../api/client";
import { EmptyState, ErrorState, LoadingState } from "../components/AsyncState";
import { ApplicationContextState } from "../hooks/useApplicationContext";

const pageSize = 24;

interface GlossaryPageProps {
  context: ApplicationContextState;
}

const emptyTerm: GlossaryTermWrite = {
  name: "",
  definition: "",
  long_description: "",
  abbreviation: "",
  examples: [],
};

function errorMessage(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

function contextQuery(context: ApplicationContextState): URLSearchParams {
  const query = new URLSearchParams();
  if (context.selectedOrganizationId) {
    query.set("organization", context.selectedOrganizationId);
  }
  if (context.selectedModelId) query.set("model", context.selectedModelId);
  return query;
}

export default function GlossaryPage({ context }: GlossaryPageProps) {
  const modelId = context.selectedModelId;
  const [searchParams, setSearchParams] = useSearchParams();
  const [summary, setSummary] = useState<ModelGlossary>();
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [queryInput, setQueryInput] = useState(searchParams.get("q") ?? "");
  const [editor, setEditor] = useState<GlossaryTermWrite | null>(null);
  const [creatingGlossary, setCreatingGlossary] = useState(false);
  const [busy, setBusy] = useState(false);

  const query = searchParams.get("q") ?? "";
  const sort =
    searchParams.get("sort") === "definition" ||
    searchParams.get("sort") === "abbreviation"
      ? searchParams.get("sort") as "definition" | "abbreviation"
      : "name";
  const direction = searchParams.get("direction") === "desc" ? "desc" : "asc";
  const page = Math.max(Number(searchParams.get("page") ?? "1") || 1, 1);

  const load = useCallback(async () => {
    if (!modelId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const nextSummary = await context.loadModelGlossary(modelId);
      setSummary(nextSummary);
      if (!nextSummary.glossary) {
        setTerms([]);
        setTotal(0);
        return;
      }
      const collection = await context.loadModelGlossaryTerms(modelId, {
        query,
        sort,
        direction,
        offset: (page - 1) * pageSize,
        limit: pageSize,
      });
      setTerms(collection.items);
      setTotal(collection.total);
    } catch (reason) {
      setError(errorMessage(reason, "The glossary could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, [
    context.loadModelGlossary,
    context.loadModelGlossaryTerms,
    direction,
    modelId,
    page,
    query,
    sort,
  ]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setQueryInput(query);
  }, [query]);

  function updateSearch(values: Record<string, string | null>) {
    const next = new URLSearchParams(searchParams);
    for (const [key, value] of Object.entries(values)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    setSearchParams(next);
  }

  async function createGlossary(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!modelId) return;
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await context.createModelGlossary(modelId, {
        name: String(form.get("name") ?? "").trim(),
        description: String(form.get("description") ?? "").trim(),
      });
      setCreatingGlossary(false);
      setFeedback("Glossary created and linked to this model.");
      await load();
      context.refreshModelOverview();
    } catch (reason) {
      setError(errorMessage(reason, "The glossary could not be created."));
    } finally {
      setBusy(false);
    }
  }

  async function saveTerm(body: GlossaryTermWrite) {
    if (!modelId) return;
    setBusy(true);
    setError("");
    try {
      await context.createModelGlossaryTerm(modelId, body);
      setEditor(null);
      setFeedback("Glossary term created.");
      await load();
    } catch (reason) {
      setError(errorMessage(reason, "The glossary term could not be created."));
    } finally {
      setBusy(false);
    }
  }

  async function importCsv(file: File) {
    if (!modelId) return;
    setBusy(true);
    setError("");
    try {
      const result = await context.importModelGlossary(modelId, file);
      setFeedback(
        `Imported ${result.imported} term${result.imported === 1 ? "" : "s"}${result.failed ? `; ${result.failed} failed` : ""}.`,
      );
      await load();
    } catch (reason) {
      setError(errorMessage(reason, "The glossary CSV could not be imported."));
    } finally {
      setBusy(false);
    }
  }

  async function deleteGlossary() {
    if (
      !modelId ||
      !summary?.glossary ||
      !window.confirm(
        `Delete “${summary.glossary.name}” and all of its terms? This cannot be undone.`,
      )
    ) return;
    setBusy(true);
    setError("");
    try {
      await context.deleteModelGlossary(modelId);
      setFeedback("Glossary deleted.");
      await load();
      context.refreshModelOverview();
    } catch (reason) {
      setError(errorMessage(reason, "The glossary could not be deleted."));
    } finally {
      setBusy(false);
    }
  }

  if (!modelId) {
    return (
      <EmptyState
        title="Select a model"
        message="Glossary terms are scoped to the selected organization and model."
      />
    );
  }
  if (loading && !summary) return <LoadingState label="Loading glossary…" />;

  const canEdit = summary?.available_actions.includes("glossary.edit") ?? false;
  const pageCount = Math.max(Math.ceil(total / pageSize), 1);

  return (
    <div className="glossary-page">
      <header className="glossary-header">
        <div>
          <p className="section-eyebrow">Governance</p>
          <h1>{summary?.glossary?.name ?? "Glossary"}</h1>
          <p>
            {summary?.glossary?.description ||
              "Govern business language and its authorized model mappings."}
          </p>
        </div>
        {summary?.glossary && canEdit && (
          <div className="glossary-header__actions">
            <button className="button button--primary" onClick={() => setEditor(emptyTerm)}>
              New term
            </button>
            <label className={`button button--secondary${busy ? " is-disabled" : ""}`}>
              Import CSV
              <input
                accept=".csv,text/csv"
                disabled={busy}
                hidden
                type="file"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void importCsv(file);
                  event.target.value = "";
                }}
              />
            </label>
            <button className="button button--danger" disabled={busy} onClick={() => void deleteGlossary()}>
              Delete glossary
            </button>
          </div>
        )}
      </header>

      {error && <div className="inline-message inline-message--error" role="alert">{error}</div>}
      {feedback && <div className="inline-message inline-message--success" role="status">{feedback}</div>}

      {!summary?.glossary ? (
        <section className="empty-state glossary-empty">
          <h2>No glossary is linked to this model</h2>
          <p>Create a model-scoped glossary to manage governed terms.</p>
          {canEdit && (
            <button className="button button--primary" onClick={() => setCreatingGlossary(true)}>
              Create glossary
            </button>
          )}
        </section>
      ) : (
        <>
          <div className="glossary-tabs" role="tablist" aria-label="Glossary views">
            <button className="glossary-tab glossary-tab--active" role="tab" aria-selected="true">
              Published terms <span>{total}</span>
            </button>
            <Link className="glossary-tab" role="tab" to={`/governance/proposals?${contextQuery(context)}`}>
              Proposed terms
            </Link>
          </div>

          <section className="glossary-toolbar" aria-label="Glossary controls">
            <form
              className="glossary-search"
              onSubmit={(event) => {
                event.preventDefault();
                updateSearch({ q: queryInput.trim() || null, page: null });
              }}
            >
              <label htmlFor="glossary-search">Search terms</label>
              <div>
                <input
                  id="glossary-search"
                  value={queryInput}
                  onChange={(event) => setQueryInput(event.target.value)}
                  placeholder="Name or definition"
                />
                <button className="button button--secondary" type="submit">Search</button>
              </div>
            </form>
            <label>
              Sort by
              <select
                value={sort}
                onChange={(event) => updateSearch({ sort: event.target.value, page: null })}
              >
                <option value="name">Business term</option>
                <option value="definition">Definition</option>
                <option value="abbreviation">Abbreviation</option>
              </select>
            </label>
            <button
              className="button button--secondary"
              onClick={() => updateSearch({ direction: direction === "asc" ? "desc" : "asc", page: null })}
            >
              {direction === "asc" ? "Ascending" : "Descending"}
            </button>
            <button className="button button--secondary" disabled={loading} onClick={() => void load()}>
              Refresh
            </button>
          </section>

          {loading ? (
            <LoadingState label="Loading glossary terms…" />
          ) : terms.length === 0 ? (
            <EmptyState
              title={query ? "No matching terms" : "No glossary terms"}
              message={query ? "Try a broader search." : "Create a term or import a glossary CSV."}
            />
          ) : (
            <div className="glossary-term-grid">
              {terms.map((term) => (
                <article className="glossary-term-card" key={term.id}>
                  <div className="glossary-term-card__status">
                    <span className="status-pill status-pill--published">Published</span>
                    {term.abbreviation && <span>{term.abbreviation}</span>}
                  </div>
                  <h2>
                    <Link to={`/governance/glossary/terms/${encodeURIComponent(term.id)}?${contextQuery(context)}`}>
                      {term.name}
                    </Link>
                  </h2>
                  <p>{term.definition || "No definition provided."}</p>
                  <Link className="text-link" to={`/governance/glossary/terms/${encodeURIComponent(term.id)}?${contextQuery(context)}`}>
                    View term details
                  </Link>
                </article>
              ))}
            </div>
          )}

          {pageCount > 1 && (
            <nav className="pagination" aria-label="Glossary pages">
              <button
                className="button button--secondary"
                disabled={page <= 1}
                onClick={() => updateSearch({ page: String(page - 1) })}
              >
                Previous
              </button>
              <span>Page {page} of {pageCount}</span>
              <button
                className="button button--secondary"
                disabled={page >= pageCount}
                onClick={() => updateSearch({ page: String(page + 1) })}
              >
                Next
              </button>
            </nav>
          )}
        </>
      )}

      {creatingGlossary && (
        <Modal title="Create model glossary" onClose={() => setCreatingGlossary(false)}>
          <form className="glossary-form" onSubmit={(event) => void createGlossary(event)}>
            <label>Name<input name="name" required maxLength={200} /></label>
            <label>Description<textarea name="description" rows={4} /></label>
            <div className="modal-actions">
              <button className="button button--secondary" type="button" onClick={() => setCreatingGlossary(false)}>Cancel</button>
              <button className="button button--primary" disabled={busy} type="submit">Create glossary</button>
            </div>
          </form>
        </Modal>
      )}
      {editor && (
        <TermEditor
          busy={busy}
          initial={editor}
          onCancel={() => setEditor(null)}
          onSave={saveTerm}
        />
      )}
    </div>
  );
}

export function GlossaryProposalsPage({ context }: GlossaryPageProps) {
  const modelId = context.selectedModelId;
  const runId = context.modelOverview?.lifecycle.latest_run_id ?? "";
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<ProposalItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busyId, setBusyId] = useState("");
  const [queryInput, setQueryInput] = useState(searchParams.get("q") ?? "");
  const [editing, setEditing] = useState<ProposalItem>();
  const decisionParam = searchParams.get("state");
  const decision: ReviewDecision | undefined =
    decisionParam === "pending" ||
    decisionParam === "accept" ||
    decisionParam === "reject" ||
    decisionParam === "edit"
      ? decisionParam
      : undefined;
  const query = searchParams.get("q") ?? "";

  const load = useCallback(async () => {
    if (!modelId || !runId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const result = await context.loadModelRunProposals(modelId, runId, {
        section: "glossary_terms",
        decision,
        query,
        limit: 200,
      });
      setItems(result.items);
      setTotal(result.page.total);
    } catch (reason) {
      setError(errorMessage(reason, "Proposed glossary terms could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, [context.loadModelRunProposals, decision, modelId, query, runId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function decide(
    item: ProposalItem,
    nextDecision: "accept" | "reject" | "edit",
    overrides?: Record<string, unknown>,
  ) {
    if (!modelId || !runId) return;
    setBusyId(item.id);
    setError("");
    try {
      await context.decideModelProposal(modelId, runId, {
        section: "glossary_terms",
        element_id: item.id,
        decision: nextDecision,
        overrides,
      });
      setEditing(undefined);
      setFeedback(`Saved ${nextDecision} decision for ${item.id}.`);
      await load();
      context.refreshModelOverview();
    } catch (reason) {
      setError(errorMessage(reason, "The proposal decision could not be saved."));
    } finally {
      setBusyId("");
    }
  }

  if (!modelId) return <EmptyState title="Select a model" message="Glossary proposals are model-scoped." />;
  if (context.overviewStatus === "loading" || (loading && !runId)) {
    return <LoadingState label="Loading glossary proposals…" />;
  }

  const publishedQuery = contextQuery(context);
  return (
    <div className="glossary-page">
      <header className="glossary-header">
        <div>
          <p className="section-eyebrow">Governance</p>
          <h1>Proposed glossary terms</h1>
          <p>Review discovery evidence before terms become governed model vocabulary.</p>
        </div>
        {runId && <span className="run-id">Run {runId}</span>}
      </header>
      <div className="glossary-tabs" role="tablist" aria-label="Glossary views">
        <Link className="glossary-tab" role="tab" to={`/governance?${publishedQuery}`}>
          Published terms
        </Link>
        <button className="glossary-tab glossary-tab--active" role="tab" aria-selected="true">
          Proposed terms <span>{total}</span>
        </button>
      </div>
      {error && <div className="inline-message inline-message--error" role="alert">{error}</div>}
      {feedback && <div className="inline-message inline-message--success" role="status">{feedback}</div>}
      {!runId ? (
        <EmptyState title="No proposals available" message="Run discovery to generate glossary proposals for this model." />
      ) : (
        <>
          <section className="glossary-toolbar" aria-label="Proposal filters">
            <form
              className="glossary-search"
              onSubmit={(event) => {
                event.preventDefault();
                const next = new URLSearchParams(searchParams);
                queryInput.trim() ? next.set("q", queryInput.trim()) : next.delete("q");
                setSearchParams(next);
              }}
            >
              <label htmlFor="proposal-search">Search proposals</label>
              <div>
                <input id="proposal-search" value={queryInput} onChange={(event) => setQueryInput(event.target.value)} />
                <button className="button button--secondary">Search</button>
              </div>
            </form>
            <label>
              Review state
              <select
                value={decision ?? ""}
                onChange={(event) => {
                  const next = new URLSearchParams(searchParams);
                  event.target.value ? next.set("state", event.target.value) : next.delete("state");
                  setSearchParams(next);
                }}
              >
                <option value="">All states</option>
                <option value="pending">Needs review</option>
                <option value="accept">Approved</option>
                <option value="edit">Approved with edits</option>
                <option value="reject">Rejected</option>
              </select>
            </label>
            <button className="button button--secondary" disabled={loading} onClick={() => void load()}>Refresh</button>
          </section>
          {loading ? (
            <LoadingState label="Loading glossary proposals…" />
          ) : items.length === 0 ? (
            <EmptyState title="No matching proposals" message="No proposed glossary terms match these filters." />
          ) : (
            <div className="proposal-term-list">
              {items.map((item) => {
                if (item.section !== "glossary_terms") return null;
                const canvasQuery = contextQuery(context);
                canvasQuery.set("review_run_id", item.canvas.review_run_id);
                canvasQuery.set("lens", item.canvas.lens);
                canvasQuery.set("focus_node_id", item.canvas.focus_node_id);
                canvasQuery.set("element_id", item.canvas.element_id);
                const confidence =
                  item.confidence === null ? null : Math.round(item.confidence * 100);
                return (
                  <article className="proposal-term-card" key={item.id}>
                    <div className="proposal-term-card__heading">
                      <div>
                        <span className={`status-pill status-pill--${item.review.decision}`}>
                          {item.review.decision === "pending" ? "Needs review" : item.review.decision === "accept" ? "Approved" : item.review.decision === "edit" ? "Approved with edits" : "Rejected"}
                        </span>
                        <h2>{String(item.review.overrides?.name ?? item.proposal.name)}</h2>
                      </div>
                      {confidence !== null && <strong aria-label={`Confidence ${confidence} percent`}>{confidence}% confidence</strong>}
                    </div>
                    <p>{String(item.review.overrides?.definition ?? item.proposal.definition ?? "No definition proposed.")}</p>
                    {item.proposal.columns && item.proposal.columns.length > 0 && (
                      <div className="proposal-evidence">
                        <strong>Supporting mappings</strong>
                        <ul>{item.proposal.columns.map((column) => <li key={column}>{column}</li>)}</ul>
                      </div>
                    )}
                    <div className="proposal-audit">
                      <span>Source: {item.provenance.source ?? item.proposal.source ?? "Unknown"}</span>
                      <span>Decision: {item.review.decision}</span>
                      {item.review.note && <span>Note: {item.review.note}</span>}
                    </div>
                    <div className="proposal-term-card__actions">
                      <Link className="button button--secondary" to={`/canvas?${canvasQuery}`}>View in Canvas</Link>
                      {item.available_actions.includes("accept") && <button className="button button--secondary" disabled={Boolean(busyId)} onClick={() => void decide(item, "accept")}>Approve</button>}
                      {item.available_actions.includes("reject") && <button className="button button--secondary" disabled={Boolean(busyId)} onClick={() => void decide(item, "reject")}>Reject</button>}
                      {item.available_actions.includes("edit") && <button className="button button--primary" disabled={Boolean(busyId)} onClick={() => setEditing(item)}>Edit & approve</button>}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}
      {editing?.section === "glossary_terms" && (
        <TermEditor
          busy={Boolean(busyId)}
          initial={{
            ...emptyTerm,
            name: String(editing.review.overrides?.name ?? editing.proposal.name),
            definition: String(editing.review.overrides?.definition ?? editing.proposal.definition ?? ""),
          }}
          onCancel={() => setEditing(undefined)}
          onSave={(body) => decide(editing, "edit", { name: body.name, definition: body.definition })}
        />
      )}
    </div>
  );
}

export function GlossaryTermPage({ context }: GlossaryPageProps) {
  const { termId = "" } = useParams();
  const navigate = useNavigate();
  const modelId = context.selectedModelId;
  const [detail, setDetail] = useState<GlossaryTermDetail>();
  const [actions, setActions] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [assetQuery, setAssetQuery] = useState("");
  const [assets, setAssets] = useState<GlossaryAssignableAsset[]>([]);

  const load = useCallback(async () => {
    if (!modelId || !termId) return;
    setLoading(true);
    setError("");
    try {
      const result = await context.loadModelGlossaryTerm(modelId, termId);
      setDetail(result.term as GlossaryTermDetail);
      setActions(result.available_actions);
    } catch (reason) {
      setError(errorMessage(reason, "The glossary term could not be loaded."));
    } finally {
      setLoading(false);
    }
  }, [context.loadModelGlossaryTerm, modelId, termId]);

  useEffect(() => {
    void load();
  }, [load]);

  const canEdit = actions.includes("glossary.edit");

  async function save(body: GlossaryTermWrite) {
    if (!modelId) return;
    setBusy(true);
    setError("");
    try {
      await context.updateModelGlossaryTerm(modelId, termId, body);
      setEditing(false);
      setFeedback("Glossary term updated.");
      await load();
    } catch (reason) {
      setError(errorMessage(reason, "The glossary term could not be updated."));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!modelId || !detail || !window.confirm(`Delete “${detail.name}”?`)) return;
    setBusy(true);
    try {
      await context.deleteModelGlossaryTerm(modelId, termId);
      navigate({ pathname: "/governance", search: contextQuery(context).toString() });
    } catch (reason) {
      setError(errorMessage(reason, "The glossary term could not be deleted."));
      setBusy(false);
    }
  }

  async function findAssets() {
    if (!modelId) return;
    setBusy(true);
    try {
      const result = await context.loadGlossaryAssignableAssets(modelId, assetQuery);
      setAssets(result.items);
    } catch (reason) {
      setError(errorMessage(reason, "Assignable model attributes could not be loaded."));
    } finally {
      setBusy(false);
    }
  }

  async function assign(assetId: string) {
    if (!modelId) return;
    setBusy(true);
    try {
      await context.assignModelGlossaryTerm(modelId, termId, assetId);
      setFeedback("Attribute mapping added.");
      setAssets([]);
      await load();
    } catch (reason) {
      setError(errorMessage(reason, "The attribute mapping could not be added."));
    } finally {
      setBusy(false);
    }
  }

  async function unassign(assignmentId: string) {
    if (!modelId) return;
    setBusy(true);
    try {
      await context.unassignModelGlossaryTerm(modelId, termId, assignmentId);
      setFeedback("Attribute mapping removed.");
      await load();
    } catch (reason) {
      setError(errorMessage(reason, "The attribute mapping could not be removed."));
    } finally {
      setBusy(false);
    }
  }

  if (!modelId) return <EmptyState title="Select a model" message="Glossary terms are model-scoped." />;
  if (loading && !detail) return <LoadingState label="Loading glossary term…" />;
  if (error && !detail) return <ErrorState title="Glossary term unavailable" message={error} onRetry={() => void load()} />;
  if (!detail) return null;

  const back = `/governance?${contextQuery(context)}`;
  return (
    <div className="glossary-page">
      <Link className="back-link" to={back}>← Back to glossary</Link>
      <header className="glossary-detail-header">
        <div>
          <div className="glossary-term-card__status">
            <span className="status-pill status-pill--published">Published</span>
            {detail.abbreviation && <span>{detail.abbreviation}</span>}
          </div>
          <h1>{detail.name}</h1>
          <p>{detail.definition || "No definition provided."}</p>
        </div>
        {canEdit && (
          <div className="glossary-header__actions">
            <button className="button button--primary" onClick={() => setEditing(true)}>Edit term</button>
            <button className="button button--danger" disabled={busy} onClick={() => void remove()}>Delete term</button>
          </div>
        )}
      </header>
      {error && <div className="inline-message inline-message--error" role="alert">{error}</div>}
      {feedback && <div className="inline-message inline-message--success" role="status">{feedback}</div>}

      <div className="glossary-detail-grid">
        <section className="content-card">
          <h2>Definition</h2>
          <p>{detail.long_description || detail.definition || "No extended definition is available."}</p>
          {detail.examples.length > 0 && (
            <>
              <h3>Examples</h3>
              <ul>{detail.examples.map((example) => <li key={example}>{example}</li>)}</ul>
            </>
          )}
        </section>
        <section className="content-card">
          <div className="content-card__heading">
            <div>
              <h2>Mapped model attributes</h2>
              <p>Only physical assets authorized for this model are shown.</p>
            </div>
            <span className="count-badge">{detail.assignments.length}</span>
          </div>
          {detail.assignments.length === 0 ? (
            <p className="muted-text">No authorized attributes are mapped.</p>
          ) : (
            <ul className="assignment-list">
              {detail.assignments.map((assignment) => {
                const query = contextQuery(context);
                query.set("lens", assignment.canvas_lens);
                query.set("focus_node_id", assignment.canvas_element_id);
                query.set("element_id", assignment.canvas_element_id);
                return (
                  <li key={assignment.id}>
                    <div><strong>{assignment.name}</strong><span>{assignment.type}</span></div>
                    <div>
                      <Link className="text-link" to={`/canvas?${query}`}>Show in Canvas</Link>
                      {canEdit && <button className="button-link button-link--danger" disabled={busy} onClick={() => void unassign(assignment.id)}>Remove</button>}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
          {canEdit && (
            <div className="asset-picker">
              <label htmlFor="asset-search">Map an attribute</label>
              <div>
                <input id="asset-search" value={assetQuery} onChange={(event) => setAssetQuery(event.target.value)} placeholder="Search authorized attributes" />
                <button className="button button--secondary" disabled={busy} onClick={() => void findAssets()}>Find</button>
              </div>
              {assets.length > 0 && (
                <ul>
                  {assets.map((asset) => (
                    <li key={asset.id}>
                      <span>{asset.name}<small>{asset.dataset_id}</small></span>
                      <button className="button button--secondary" disabled={busy} onClick={() => void assign(asset.id)}>Map</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </section>
      </div>
      {editing && (
        <TermEditor
          busy={busy}
          initial={detail}
          onCancel={() => setEditing(false)}
          onSave={save}
        />
      )}
    </div>
  );
}

function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="modal-title"
        aria-modal="true"
        className="modal-panel"
        role="dialog"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-panel__header">
          <h2 id="modal-title">{title}</h2>
          <button aria-label="Close" className="icon-button" onClick={onClose}>×</button>
        </div>
        {children}
      </section>
    </div>
  );
}

function TermEditor({
  initial,
  busy,
  onCancel,
  onSave,
}: {
  initial: GlossaryTermWrite;
  busy: boolean;
  onCancel: () => void;
  onSave: (body: GlossaryTermWrite) => Promise<void>;
}) {
  const [form, setForm] = useState<GlossaryTermWrite>(initial);
  return (
    <Modal title={initial.name ? `Edit ${initial.name}` : "Create glossary term"} onClose={onCancel}>
      <form
        className="glossary-form"
        onSubmit={(event) => {
          event.preventDefault();
          void onSave({
            ...form,
            name: form.name.trim(),
            definition: form.definition.trim(),
            long_description: form.long_description.trim(),
            abbreviation: form.abbreviation.trim(),
            examples: form.examples.map((item) => item.trim()).filter(Boolean),
          });
        }}
      >
        <label>Business term<input required maxLength={200} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
        <label>Definition<textarea rows={3} value={form.definition} onChange={(event) => setForm({ ...form, definition: event.target.value })} /></label>
        <label>Extended definition<textarea rows={5} value={form.long_description} onChange={(event) => setForm({ ...form, long_description: event.target.value })} /></label>
        <label>Abbreviation<input maxLength={100} value={form.abbreviation} onChange={(event) => setForm({ ...form, abbreviation: event.target.value })} /></label>
        <label>Examples <span>(one per line)</span><textarea rows={4} value={form.examples.join("\n")} onChange={(event) => setForm({ ...form, examples: event.target.value.split("\n") })} /></label>
        <div className="modal-actions">
          <button className="button button--secondary" type="button" onClick={onCancel}>Cancel</button>
          <button className="button button--primary" disabled={busy || !form.name.trim()} type="submit">Save term</button>
        </div>
      </form>
    </Modal>
  );
}
