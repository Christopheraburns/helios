import { Fragment, useEffect, useMemo, useState } from 'react'
import { ArtifactPreview } from './ArtifactPreview'
import { api, GoldenQuestion } from '../services/api'
import { short } from './ManifestViewer'

const KIND_LABELS: Record<string, string> = {
  structured: 'Structured',
  unstructured: 'Unstructured',
  resolution: 'Entity resolution',
  joined: 'Joined',
  cross_document: 'Cross-document',
  no_answer: 'No answer',
}

const KIND_HELP: Record<string, string> = {
  structured: 'The answer is a TPC-DS value; the SQL that returns it is stored.',
  unstructured: 'The answer is a claim stated in the documents, with its evidence passages.',
  resolution: 'Asked by an alias that identifies exactly one entity in the corpus.',
  joined: 'Needs the documents to find the case, and TPC-DS for the value.',
  cross_document: 'The answer is built from claims across several artifacts.',
  no_answer: 'A real TPC-DS item no document discusses: the right answer is to abstain.',
}

/** A dataset's golden questions and their answers (task C-08). Read-only; curation
 * during review arrives with C-11. */
export function GoldenQuestions({ datasetId }: { datasetId: string }) {
  const [questions, setQuestions] = useState<GoldenQuestion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [kind, setKind] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [preview, setPreview] = useState<string | null>(null)

  useEffect(() => {
    api
      .listGoldenQuestions(datasetId)
      .then(setQuestions)
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [datasetId])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const q of questions ?? []) c[q.kind ?? '?'] = (c[q.kind ?? '?'] ?? 0) + 1
    return c
  }, [questions])

  if (error) return <div className="alert alert-error">{String(error)}</div>
  if (!questions) return <div className="loading">Loading golden questions…</div>
  if (questions.length === 0)
    return (
      <div className="empty-state">
        No golden questions. Datasets generated before generator 0.3.1 don't have them.
      </div>
    )

  const shown = kind ? questions.filter(q => q.kind === kind) : questions
  return (
    <div className="golden">
      <p className="muted">
        The answer key for evaluating the crawler and Helios: {questions.length} questions, computed from TPC-DS
        and the ground truth, asked as <span className="mono">{questions[0].principal_id}</span>.
      </p>
      <div className="scenario-filters">
        <select value={kind} onChange={e => setKind(e.target.value)}>
          <option value="">All kinds ({questions.length})</option>
          {Object.keys(KIND_LABELS)
            .filter(k => counts[k])
            .map(k => (
              <option key={k} value={k}>
                {KIND_LABELS[k]} ({counts[k]})
              </option>
            ))}
        </select>
      </div>
      {kind && <p className="muted">{KIND_HELP[kind]}</p>}
      <table className="data-table">
        <thead>
          <tr>
            <th>Kind</th>
            <th>Question</th>
            <th>Answer</th>
          </tr>
        </thead>
        <tbody>
          {shown.map(q => (
            <Fragment key={q.query_id}>
              <tr
                className={`clickable ${open === q.query_id ? 'selected' : ''}`}
                onClick={() => setOpen(open === q.query_id ? null : q.query_id)}
              >
                <td className="nowrap">
                  <span className="chip">{KIND_LABELS[q.kind ?? ''] ?? q.kind}</span>
                  {q.difficulty && q.difficulty !== 'direct' && <span className="chip">{q.difficulty}</span>}
                </td>
                <td>{q.question}</td>
                <td>{q.answer}</td>
              </tr>
              {open === q.query_id && (
                <tr>
                  <td colSpan={3}>
                    <QuestionDetail question={q} onPreview={setPreview} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      {preview && <ArtifactPreview artifactId={preview} onClose={() => setPreview(null)} />}
    </div>
  )
}

function QuestionDetail({ question: q, onPreview }: { question: GoldenQuestion; onPreview: (id: string) => void }) {
  const r = q.result
  return (
    <div className="golden-detail">
      {r.alias && (
        <p>
          <strong>Alias:</strong> "{r.alias}" resolves to {r.canonical_name}
        </p>
      )}
      {q.required_structured && (
        <>
          <h5>Required TPC-DS result</h5>
          <pre className="json">{q.required_structured.sql}</pre>
          <span className="muted">
            rows: {JSON.stringify(q.required_structured.rows)} · sha256 {short(q.required_structured.rows_sha256, 12)}
          </span>
        </>
      )}
      {r.claims && r.claims.length > 0 && (
        <>
          <h5>Required claims</h5>
          <ul className="source-refs">
            {r.claims.map(c => (
              <li key={c.claim_id}>
                <span className="chip">{c.claim_type}</span> {c.statement}
              </li>
            ))}
          </ul>
        </>
      )}
      {r.items && r.items.length > 0 && (
        <>
          <h5>Expected set</h5>
          <ul className="source-refs">
            {r.items.map(i => (
              <li key={i.rma}>
                {i.item} at {i.store} ({i.rma})
              </li>
            ))}
          </ul>
        </>
      )}
      {r.evidence && r.evidence.length > 0 && (
        <>
          <h5>Required evidence ({r.evidence.length})</h5>
          <ul className="gt-list compact">
            {r.evidence.map(e => (
              <li key={e.evidence_id}>
                <blockquote>{e.excerpt}</blockquote>
                <button className="btn btn-small" onClick={() => onPreview(e.artifact_id)}>
                  Preview {short(e.artifact_id, 10)}
                </button>
              </li>
            ))}
          </ul>
          {r.evidence_mention_tiers && r.evidence_mention_tiers.length > 0 && (
            <span className="muted">Mention tiers inside the evidence: {r.evidence_mention_tiers.join(', ')}</span>
          )}
        </>
      )}
      {r.abstain && <p className="muted">Correct response: say that no document discusses it.</p>}
    </div>
  )
}
