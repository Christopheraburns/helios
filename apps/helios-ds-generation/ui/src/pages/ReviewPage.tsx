import { useCallback, useEffect, useMemo, useState } from 'react'
import { AuditTrail } from '../components/AuditTrail'
import { short, stateClass } from '../components/ManifestViewer'
import { ReviewWorkspace } from '../components/ReviewWorkspace'
import { api, DatasetReview, DatasetSummary, WhoAmI } from '../services/api'
import '../styles/DatasetBrowser.css'
import '../styles/Review.css'

type StatusFilter = '' | 'UNREVIEWED' | 'ACCEPTED' | 'FLAGGED'

export function ReviewPage() {
  const [me, setMe] = useState<WhoAmI | null>(null)
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null)
  const [datasetId, setDatasetId] = useState<string | null>(null)
  const [review, setReview] = useState<DatasetReview | null>(null)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('')
  const [typeFilter, setTypeFilter] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [decided, setDecided] = useState<string | null>(null)

  const loadDatasets = useCallback(() => {
    api
      .listDatasets('IN_REVIEW')
      .then(list => {
        setDatasets(list)
        setDatasetId(list.length > 0 ? list[0].dataset_id : null)
      })
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [])

  useEffect(() => {
    api.whoami().then(setMe).catch(() => setMe(null))
    loadDatasets()
  }, [loadDatasets])

  const onDecided = (message: string) => {
    setDecided(message)
    setReview(null)
    loadDatasets()
  }

  const loadReview = useCallback(() => {
    if (!datasetId) return
    api
      .getDatasetReview(datasetId)
      .then(setReview)
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [datasetId])

  useEffect(() => {
    setReview(null)
    setSelected(null)
    loadReview()
  }, [loadReview])

  const items = useMemo(
    () =>
      (review?.items ?? []).filter(
        i => (!statusFilter || i.status === statusFilter) && (!typeFilter || i.artifact_type === typeFilter)
      ),
    [review, statusFilter, typeFilter]
  )

  useEffect(() => {
    if (!selected && items.length > 0) setSelected(items[0].artifact_id)
  }, [items, selected])

  const index = items.findIndex(i => i.artifact_id === selected)
  const go = (offset: number) => {
    const next = items[index + offset]
    if (next) setSelected(next.artifact_id)
  }

  const onMarked = (status: string) => {
    loadReview()
    if (status === 'ACCEPTED') {
      // Move on to the next unreviewed artifact after this one, if any.
      const after = items.slice(index + 1).find(i => i.status === 'UNREVIEWED')
      if (after) setSelected(after.artifact_id)
    }
  }

  const counts = review?.counts
  const reviewed = counts ? counts.total - counts.unreviewed : 0

  return (
    <div className="review-page">
      <div className="review-header">
        <div>
          <h2>Review</h2>
          {me?.user ? (
            <span className="muted">Reviewing as {me.user}</span>
          ) : (
            <div className="alert alert-warning">
              Workbench did not pass your identity to this Application, so review actions are disabled
              (marks are never recorded anonymously). Headers received: {me?.headers_seen.join(', ') || '—'}.
            </div>
          )}
        </div>
        <div className="review-dataset">
          <label htmlFor="review-dataset">Dataset in review</label>
          <select
            id="review-dataset"
            value={datasetId ?? ''}
            onChange={e => setDatasetId(e.target.value || null)}
          >
            {datasets?.length === 0 && <option value="">No datasets are in review</option>}
            {datasets?.map(d => (
              <option key={d.dataset_id} value={d.dataset_id}>
                {short(d.dataset_id, 13)} · {d.rendered_artifact_count} artifacts ·{' '}
                {d.created_at ? new Date(d.created_at).toLocaleDateString() : ''}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error && <div className="alert alert-error">{String(error)}</div>}
      {decided && <div className="alert alert-success">{decided}</div>}

      {counts && (
        <div className="review-progress">
          <div className="progress-bar">
            <div
              className="progress-fill"
              style={{ width: `${counts.total ? (100 * reviewed) / counts.total : 0}%` }}
            />
          </div>
          <span>
            {reviewed} of {counts.total} reviewed · <strong>{counts.accepted}</strong> accepted ·{' '}
            <strong>{counts.flagged}</strong> flagged · {counts.comments} comments
          </span>
          <span className="muted"> (flags are advisory and don't block approval)</span>
        </div>
      )}

      {review && datasetId && (
        <DecisionPanel review={review} canAct={Boolean(me?.user)} onDecided={onDecided} />
      )}

      {review && (
        <div className="review-layout">
          <aside className="review-list">
            <div className="review-filters">
              <select value={statusFilter} onChange={e => setStatusFilter(e.target.value as StatusFilter)}>
                <option value="">All statuses</option>
                <option value="UNREVIEWED">Unreviewed</option>
                <option value="ACCEPTED">Accepted</option>
                <option value="FLAGGED">Flagged</option>
              </select>
              <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}>
                <option value="">All types</option>
                {Array.from(new Set(review.items.map(i => i.artifact_type)))
                  .sort()
                  .map(t => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
              </select>
            </div>
            {items.map(i => (
              <button
                key={i.artifact_id}
                className={`review-item ${i.artifact_id === selected ? 'selected' : ''} status-${i.status.toLowerCase()}`}
                onClick={() => setSelected(i.artifact_id)}
              >
                <span>
                  <span className="chip">{i.artifact_type}</span>
                  <span className="review-status">{i.status}</span>
                  {i.comment_count > 0 && <span className="muted"> · {i.comment_count} 💬</span>}
                </span>
                <span className="review-headline">{i.headline ?? i.template_id}</span>
              </button>
            ))}
            {items.length === 0 && <div className="empty-state">Nothing matches the filters.</div>}
          </aside>

          <section className="review-main">
            {selected ? (
              <ReviewWorkspace
                artifactId={selected}
                canAct={Boolean(me?.user)}
                onMarked={onMarked}
                onPrevious={index > 0 ? () => go(-1) : undefined}
                onNext={index >= 0 && index < items.length - 1 ? () => go(1) : undefined}
              />
            ) : (
              <div className="empty-state">Select an artifact to review.</div>
            )}
          </section>
        </div>
      )}
      {review && datasetId && (
        <details className="technical review-history">
          <summary>History of this dataset</summary>
          <AuditTrail datasetId={datasetId} refreshKey={review.counts.total - review.counts.unreviewed} />
        </details>
      )}
      {datasets && datasets.length === 0 && (
        <div className="empty-state">
          No datasets are waiting for review. Datasets reach review once their artifacts are rendered and
          validated.
        </div>
      )}
    </div>
  )
}

/** Approve or reject the dataset under review (R-06). Flags never block approval;
 * failed validation does, and the API reports why. */
function DecisionPanel({
  review,
  canAct,
  onDecided,
}: {
  review: DatasetReview
  canAct: boolean
  onDecided: (message: string) => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [problems, setProblems] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { dataset, counts, lineage } = review
  const readyInLineage = lineage.filter(m => m.state === 'READY')

  const run = async (kind: 'approve' | 'reject') => {
    setError(null)
    setProblems(null)
    if (kind === 'reject' && !note.trim()) {
      setError('Say why the dataset is rejected: it records what to fix before regenerating.')
      return
    }
    const question =
      kind === 'approve'
        ? `Approve dataset ${short(dataset.dataset_id, 13)}? It becomes READY and visible to the crawler` +
          (readyInLineage.length ? `, and ${readyInLineage.length} earlier dataset(s) will be superseded.` : '.') +
          (counts.flagged ? ` ${counts.flagged} artifact(s) are flagged (advisory).` : '') +
          (counts.unreviewed ? ` ${counts.unreviewed} artifact(s) are unreviewed.` : '')
        : `Reject dataset ${short(dataset.dataset_id, 13)}? This is final; fix the config or templates and regenerate.`
    if (!window.confirm(question)) return
    setBusy(true)
    try {
      const result =
        kind === 'approve'
          ? await api.approveDataset(dataset.dataset_id, note.trim())
          : await api.rejectDataset(dataset.dataset_id, note.trim())
      onDecided(
        kind === 'approve'
          ? `Dataset ${short(dataset.dataset_id, 13)} approved and READY.` +
              (result.superseded.length
                ? ` Superseded: ${result.superseded.map(d => short(d, 13)).join(', ')}.`
                : '')
          : `Dataset ${short(dataset.dataset_id, 13)} rejected.`
      )
    } catch (err: any) {
      const detail = err.response?.data?.detail
      if (detail?.problems) setProblems(detail.problems)
      else setError(typeof detail === 'string' ? detail : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="decision-panel">
      <div className="decision-summary">
        <strong>Decision</strong>
        <span className="muted">
          {' '}
          Flags are advisory; approval is blocked only if the dataset fails re-validation.
        </span>
        {lineage.length > 0 && (
          <div className="decision-lineage">
            Same config (lineage):{' '}
            {lineage.map(m => (
              <span key={m.dataset_id} className="lineage-member">
                <span className="mono">{short(m.dataset_id, 13)}</span>{' '}
                <span className={`status-badge ${stateClass(m.state)}`}>{m.state ?? '—'}</span>
              </span>
            ))}
            {readyInLineage.length > 0 && <div className="muted">Approving supersedes the READY one(s).</div>}
          </div>
        )}
      </div>
      <div className="review-actions">
        <textarea
          rows={2}
          placeholder="Approval note (optional) or rejection reason (required)"
          value={note}
          onChange={e => setNote(e.target.value)}
          disabled={!canAct || busy}
        />
        <div className="review-buttons">
          <button className="btn btn-primary" disabled={!canAct || busy} onClick={() => run('approve')}>
            Approve dataset
          </button>
          <button className="btn btn-danger" disabled={!canAct || busy} onClick={() => run('reject')}>
            Reject dataset
          </button>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {problems && (
        <div className="alert alert-error">
          Approval blocked: the dataset failed validation.
          <ul>
            {problems.map(p => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
