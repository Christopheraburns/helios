import { useEffect, useState } from 'react'
import { api, ArtifactPreviewData } from '../services/api'

const formatTime = (iso: string) => new Date(iso).toLocaleString()

interface ArtifactPreviewProps {
  artifactId: string
  onClose?: () => void
}

/** Shows one rendered artifact in place: PDF inline, email as a message, chat as a thread. */
export function ArtifactPreview({ artifactId, onClose }: ArtifactPreviewProps) {
  const [preview, setPreview] = useState<ArtifactPreviewData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setPreview(null)
    setError(null)
    api
      .getArtifactPreview(artifactId)
      .then(p => !cancelled && setPreview(p))
      .catch(err => !cancelled && setError(err.response?.data?.detail ?? err.message))
    return () => {
      cancelled = true
    }
  }, [artifactId])

  if (error) return <div className="alert alert-error">{String(error)}</div>
  if (!preview) return <div className="loading">Loading file…</div>

  const { artifact } = preview
  return (
    <div className="artifact-preview">
      <div className="preview-header">
        <div>
          <span className="chip">{artifact.artifact_type}</span>
          <strong>{artifact.template_id}</strong>
          <span className="muted">
            {' '}
            v{artifact.template_version} · {(artifact.size_bytes / 1024).toFixed(1)} KB ·{' '}
            {formatTime(artifact.semantic_timestamp)}
          </span>
          {artifact.headline && <p className="muted">{artifact.headline}</p>}
        </div>
        <div className="preview-actions">
          <a className="btn btn-small" href={artifact.content_uri} target="_blank" rel="noreferrer">
            Open in new tab
          </a>
          <a className="btn btn-small btn-primary" href={`${artifact.content_uri}?download=true`}>
            Download
          </a>
          {onClose && (
            <button className="btn btn-small" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>

      {preview.kind === 'pdf' && (
        <iframe className="pdf-frame" src={artifact.content_uri} title={artifact.artifact_id} />
      )}

      {preview.kind === 'email' && preview.email && (
        <div className="mail">
          <table className="mail-headers">
            <tbody>
              {Object.entries(preview.email.headers).map(([name, value]) => (
                <tr key={name}>
                  <th>{name}</th>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <pre className="mail-body">{preview.email.body}</pre>
        </div>
      )}

      {preview.kind === 'chat' && preview.chat && (
        <div className="chat">
          <p className="muted">
            #{preview.chat.channel} ·{' '}
            {preview.chat.participants.map(p => `${p.name} (${p.role})`).join(', ')}
          </p>
          {preview.chat.messages.map(m => (
            <div key={m.message_id} className="chat-message">
              <div className="chat-meta">
                <strong>{m.sender_name}</strong>
                <span className="muted"> {formatTime(m.timestamp)}</span>
              </div>
              <div className="chat-text">{m.text}</div>
            </div>
          ))}
        </div>
      )}

      {preview.kind === 'other' && (
        <p className="muted">No inline preview for {artifact.mime_type}; use Download.</p>
      )}
    </div>
  )
}
