import { useEffect, useState } from 'react'
import { api, EvidenceView, Locator, MentionView, ReviewBundle } from '../services/api'
import { Highlighted, Span } from './Highlighted'
import { scenarioLabel } from './ManifestViewer'

interface ReviewWorkspaceProps {
  artifactId: string
  canAct: boolean
  onMarked: (status: string) => void
  onPrevious?: () => void
  onNext?: () => void
}

const statusClass = (status: string) =>
  status === 'ACCEPTED' ? 'status-completed' : status === 'FLAGGED' ? 'status-failed' : 'status-pending'

function spansFor(
  mentions: MentionView[],
  evidence: EvidenceView[],
  match: (l: Locator) => boolean,
  focusEntity: string | null
): Span[] {
  return [
    ...evidence
      .filter(e => match(e.locator) && e.locator.start !== undefined)
      .map(e => ({
        start: e.locator.start as number,
        end: e.locator.end as number,
        kind: 'evidence' as const,
        label: `Claim ${e.claim_type}: ${e.statement ?? ''}`,
      })),
    ...mentions
      .filter(m => match(m.locator) && m.locator.start !== undefined)
      .map(m => ({
        start: m.locator.start as number,
        end: m.locator.end as number,
        kind: 'mention' as const,
        label: `${m.entity_type}: ${m.canonical_name}${m.difficulty ? ` (${m.difficulty})` : ''}`,
        focus: m.entity_id === focusEntity,
      })),
  ]
}

export function ReviewWorkspace({ artifactId, canAct, onMarked, onPrevious, onNext }: ReviewWorkspaceProps) {
  const [bundle, setBundle] = useState<ReviewBundle | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [focusEntity, setFocusEntity] = useState<string | null>(null)

  const load = () => {
    setError(null)
    api
      .getReviewBundle(artifactId)
      .then(setBundle)
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }

  useEffect(() => {
    setBundle(null)
    setComment('')
    setFocusEntity(null)
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifactId])

  const mark = async (status: string) => {
    if ((status === 'FLAGGED' || status === 'COMMENT') && !comment.trim()) {
      setError(status === 'FLAGGED' ? 'Say why you are flagging it.' : 'Write a comment first.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.addMark(artifactId, status, comment)
      setComment('')
      load()
      onMarked(status)
    } catch (err: any) {
      setError(err.response?.data?.detail ?? err.message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return
      if (e.key === 'a' && canAct && !busy) mark('ACCEPTED')
      if (e.key === 'n' && onNext) onNext()
      if (e.key === 'p' && onPrevious) onPrevious()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (error && !bundle) return <div className="alert alert-error">{String(error)}</div>
  if (!bundle) return <div className="loading">Loading artifact…</div>

  const { preview, mentions, evidence } = bundle
  const artifact = preview.artifact
  const entities = Array.from(
    new Map(mentions.map(m => [m.entity_id, m])).values()
  ).sort((a, b) => a.entity_type.localeCompare(b.entity_type))

  return (
    <div className="review-workspace">
      <div className="review-toolbar">
        <div>
          <span className={`status-badge ${statusClass(bundle.status)}`}>{bundle.status}</span>{' '}
          <span className="chip">{artifact.artifact_type}</span>
          <strong>{artifact.template_id}</strong>
          <p className="muted">
            {bundle.scenario ? `${scenarioLabel(bundle.scenario.scenario_type)}: ` : ''}
            {bundle.scenario?.headline}
          </p>
        </div>
        <div className="review-nav">
          <button className="btn btn-small" onClick={onPrevious} disabled={!onPrevious} title="p">
            ← Prev
          </button>
          <button className="btn btn-small" onClick={onNext} disabled={!onNext} title="n">
            Next →
          </button>
        </div>
      </div>

      <div className="review-actions">
        <textarea
          placeholder="Comment (required to flag)"
          value={comment}
          onChange={e => setComment(e.target.value)}
          rows={2}
        />
        <div className="review-buttons">
          <button className="btn btn-primary" disabled={!canAct || busy} onClick={() => mark('ACCEPTED')} title="a">
            Accept
          </button>
          <button className="btn btn-danger" disabled={!canAct || busy} onClick={() => mark('FLAGGED')}>
            Flag
          </button>
          <button className="btn" disabled={!canAct || busy} onClick={() => mark('COMMENT')}>
            Comment
          </button>
        </div>
      </div>
      {error && <div className="alert alert-error">{String(error)}</div>}

      <div className="review-columns">
        <section className="review-artifact">
          <h4>Artifact</h4>
          {preview.kind === 'email' && preview.email && (
            <div className="mail">
              <table className="mail-headers">
                <tbody>
                  {Object.entries(preview.email.headers).map(([name, value]) => (
                    <tr key={name}>
                      <th>{name}</th>
                      <td>
                        {name === 'Subject' ? (
                          <Highlighted
                            text={value}
                            spans={spansFor(mentions, evidence, l => l.part === 'subject', focusEntity)}
                          />
                        ) : (
                          value
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <pre className="mail-body">
                <Highlighted
                  text={preview.email.body.replace(/\r\n/g, '\n')}
                  spans={spansFor(mentions, evidence, l => l.part === 'body', focusEntity)}
                />
              </pre>
            </div>
          )}
          {preview.kind === 'chat' && preview.chat && (
            <div className="chat">
              {preview.chat.messages.map(m => (
                <div key={m.message_id} className="chat-message">
                  <div className="chat-meta">
                    <strong>{m.sender_name}</strong>
                    <span className="muted"> {new Date(m.timestamp).toLocaleString()}</span>
                  </div>
                  <div className="chat-text">
                    <Highlighted
                      text={m.text}
                      spans={spansFor(mentions, evidence, l => l.message_id === m.message_id, focusEntity)}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
          {preview.kind === 'pdf' && (
            <>
              <iframe className="pdf-frame" src={artifact.content_uri} title={artifact.artifact_id} />
              <p className="muted">
                PDF locators are page + text; the located passages are listed under Ground truth.
              </p>
            </>
          )}
          <p className="legend muted">
            <span className="gt-mention">entity mention</span> <span className="gt-evidence">claim evidence</span>{' '}
            (hover for details; click an entity on the right to find all its mentions)
          </p>
        </section>

        <section className="review-truth">
          <h4>Ground truth</h4>
          <h5>Claims and evidence ({evidence.length})</h5>
          <ul className="gt-list">
            {evidence.map(e => (
              <li key={e.evidence_id}>
                <span className="chip">{e.claim_type}</span> <span className="muted">{e.truth_status}</span>
                <div>{e.statement}</div>
                <blockquote>
                  {e.excerpt}
                  {e.locator.page ? <span className="muted"> (page {e.locator.page})</span> : null}
                </blockquote>
              </li>
            ))}
            {evidence.length === 0 && <li className="muted">No claim evidence in this artifact.</li>}
          </ul>

          <h5>Entities mentioned ({entities.length})</h5>
          <ul className="gt-list">
            {entities.map(m => {
              const surfaces = Array.from(
                new Set(
                  mentions
                    .filter(x => x.entity_id === m.entity_id)
                    .map(x => (x.difficulty && x.difficulty !== 'direct' ? `"${x.surface_form}" (${x.difficulty})` : `"${x.surface_form}"`))
                )
              )
              return (
                <li
                  key={m.entity_id}
                  className={`clickable ${focusEntity === m.entity_id ? 'selected' : ''}`}
                  onClick={() => setFocusEntity(focusEntity === m.entity_id ? null : m.entity_id)}
                >
                  <span className="chip">{m.entity_type}</span> {m.canonical_name}
                  <div className="muted">as: {surfaces.join(', ')}</div>
                </li>
              )
            })}
          </ul>

          <h5>Relationships</h5>
          <ul className="gt-list compact">
            {bundle.relationships.map((r, i) => (
              <li key={i} className={r.from_this_artifact ? '' : 'muted'}>
                {r.source} <strong>{r.predicate}</strong> {r.target}
              </li>
            ))}
          </ul>

          {bundle.scenario && (
            <details className="technical">
              <summary>TPC-DS facts ({Object.keys(bundle.scenario.facts).length})</summary>
              <table className="data-table facts">
                <tbody>
                  {Object.entries(bundle.scenario.facts).map(([k, v]) => (
                    <tr key={k}>
                      <td className="mono">{k}</td>
                      <td>{v === null || v === undefined ? '—' : String(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}

          <h5>Review history ({bundle.marks.length})</h5>
          <ul className="gt-list compact">
            {bundle.marks.map(m => (
              <li key={m.mark_id}>
                <span className={`status-badge ${statusClass(m.status)}`}>{m.status}</span>{' '}
                <strong>{m.reviewer}</strong>{' '}
                <span className="muted">{new Date(m.created_at).toLocaleString()}</span>
                {m.comment && <div>{m.comment}</div>}
              </li>
            ))}
            {bundle.marks.length === 0 && <li className="muted">Not reviewed yet.</li>}
          </ul>
        </section>
      </div>
    </div>
  )
}
