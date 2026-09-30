import { useEffect, useState } from 'react'
import { api, AuditEvent } from '../services/api'
import { short, stateClass } from './ManifestViewer'

const markClass = (action: string) =>
  action === 'ACCEPTED' ? 'status-completed' : action === 'FLAGGED' ? 'status-failed' : 'status-pending'

/** A dataset's full history (R-07): lifecycle events, decisions and review marks. */
export function AuditTrail({ datasetId, refreshKey = 0 }: { datasetId: string; refreshKey?: number }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [showMarks, setShowMarks] = useState(true)

  useEffect(() => {
    setEvents(null)
    api
      .getDatasetHistory(datasetId)
      .then(setEvents)
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [datasetId, refreshKey])

  if (error) return <div className="alert alert-error">{String(error)}</div>
  if (!events) return <div className="loading">Loading history…</div>

  const shown = showMarks ? events : events.filter(e => e.kind === 'lifecycle')
  return (
    <div className="audit-trail">
      <label className="muted">
        <input type="checkbox" checked={showMarks} onChange={e => setShowMarks(e.target.checked)} /> Include
        review marks ({events.filter(e => e.kind === 'review').length})
      </label>
      <ol className="timeline">
        {shown.map((e, i) => (
          <li key={`${e.occurred_at}-${e.kind}-${e.event_seq ?? e.artifact_id}-${i}`}>
            <span className={`status-badge ${e.kind === 'review' ? markClass(e.action) : stateClass(e.action)}`}>
              {e.kind === 'review' ? `mark: ${e.action}` : e.action}
            </span>
            <span className="muted">
              {new Date(e.occurred_at).toLocaleString()} · {e.actor}
              {e.artifact_id && <> · artifact <span className="mono">{short(e.artifact_id, 10)}</span></>}
              {e.dataset_id !== datasetId && (
                <>
                  {' '}
                  · dataset <span className="mono">{short(e.dataset_id, 13)}</span>
                </>
              )}
            </span>
            {e.note && <span className="reason">{e.note}</span>}
            {e.review_counts && (
              <span className="reason">
                At this decision: {e.review_counts.accepted} accepted, {e.review_counts.flagged} flagged,{' '}
                {e.review_counts.unreviewed} unreviewed of {e.review_counts.total}.
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
