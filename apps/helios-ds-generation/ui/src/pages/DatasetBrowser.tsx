import { useCallback, useEffect, useState } from 'react'
import { ManifestViewer, short, stateClass } from '../components/ManifestViewer'
import { api, DatasetSummary, WhoAmI } from '../services/api'
import '../styles/DatasetBrowser.css'

interface DatasetBrowserProps {
  selectedDatasetId: string | null
  onSelect: (datasetId: string | null) => void
}

// A job may still be writing datasets in these states.
const BUSY_STATES = ['CREATING', 'VALIDATING']

export function DatasetBrowser({ selectedDatasetId, onSelect }: DatasetBrowserProps) {
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [me, setMe] = useState<WhoAmI | null>(null)
  const [deleting, setDeleting] = useState<DatasetSummary | null>(null)

  useEffect(() => {
    api.whoami().then(setMe).catch(() => setMe(null))
  }, [])

  const load = useCallback(() => {
    setError(null)
    api
      .listDatasets()
      .then(list => {
        setDatasets(list)
        if (!selectedDatasetId && list.length > 0) onSelect(list[0].dataset_id)
      })
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [selectedDatasetId, onSelect])

  const onDeleted = (datasetId: string, objects: number) => {
    setDeleting(null)
    setNotice(`Deleted dataset ${short(datasetId, 13)} (${objects} objects). Its history is kept.`)
    const rest = (datasets ?? []).filter(d => d.dataset_id !== datasetId)
    setDatasets(rest)
    onSelect(rest.length > 0 ? rest[0].dataset_id : null)
  }

  const selected = datasets?.find(d => d.dataset_id === selectedDatasetId) ?? null

  useEffect(() => {
    load()
    // Load once on open; the Refresh button reloads on demand.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="dataset-browser">
      <aside className="dataset-list">
        <div className="dataset-list-header">
          <h2>Datasets</h2>
          <button className="btn btn-small" onClick={load}>
            Refresh
          </button>
        </div>
        {error && <div className="alert alert-error">{String(error)}</div>}
        {!datasets && !error && <div className="loading">Loading…</div>}
        {datasets && datasets.length === 0 && (
          <div className="empty-state">No datasets yet. Submit a job to plan one.</div>
        )}
        {datasets?.map(d => (
          <button
            key={d.dataset_id}
            className={`dataset-item ${d.dataset_id === selectedDatasetId ? 'selected' : ''}`}
            onClick={() => onSelect(d.dataset_id)}
          >
            <span className="mono">{short(d.dataset_id, 13)}</span>
            <span className={`status-badge ${stateClass(d.state)}`}>{d.state ?? 'UNKNOWN'}</span>
            <span className="muted">
              {d.scenario_count} scenarios · {d.rendered_artifact_count} of{' '}
              {d.planned_artifact_count} assets rendered
            </span>
            {d.created_at && (
              <span className="muted">{new Date(d.created_at).toLocaleString()}</span>
            )}
          </button>
        ))}
      </aside>

      <section className="dataset-detail">
        {notice && <div className="alert alert-success">{notice}</div>}
        {selected && (
          <div className="dataset-actions">
            <button
              className="btn btn-danger btn-small"
              onClick={() => setDeleting(selected)}
              disabled={!me?.user || BUSY_STATES.includes(selected.state ?? '')}
              title={
                !me?.user
                  ? 'Deleting needs your Workbench identity (see /v1/whoami)'
                  : BUSY_STATES.includes(selected.state ?? '')
                    ? 'A generation job may still be writing this dataset'
                    : 'Delete this dataset'
              }
            >
              Delete dataset
            </button>
          </div>
        )}
        {deleting && (
          <DeleteDialog dataset={deleting} onCancel={() => setDeleting(null)} onDeleted={onDeleted} />
        )}
        {selectedDatasetId ? (
          <ManifestViewer key={selectedDatasetId} datasetId={selectedDatasetId} />
        ) : (
          <div className="empty-state">Select a dataset to view its manifest.</div>
        )}
      </section>
    </div>
  )
}

function DeleteDialog({
  dataset,
  onCancel,
  onDeleted,
}: {
  dataset: DatasetSummary
  onCancel: () => void
  onDeleted: (datasetId: string, objects: number) => void
}) {
  const expected = dataset.dataset_id.slice(0, 8)
  const [typed, setTyped] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      const result = await api.deleteDataset(dataset.dataset_id, reason.trim())
      onDeleted(dataset.dataset_id, result.objects_deleted)
    } catch (err: any) {
      const detail = err.response?.data?.detail
      setError(typeof detail === 'string' ? detail : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="modal" role="dialog" aria-modal="true" onClick={e => e.stopPropagation()}>
        <h3>Delete dataset?</h3>
        <p>
          <span className="mono">{dataset.dataset_id}</span>{' '}
          <span className={`status-badge ${stateClass(dataset.state)}`}>{dataset.state}</span>
        </p>
        <p>
          This permanently removes its manifest and {dataset.rendered_artifact_count} artifacts from the object
          store, and its scenario plans, ground truth and review marks from the lakehouse. Its lifecycle history is
          kept, and generating the same config again recreates it.
        </p>
        {dataset.state === 'READY' && (
          <div className="alert alert-warning">
            This dataset is READY: the crawler and evaluations may be using it. Deleting it removes it for them too.
          </div>
        )}
        <label className="modal-field">
          Reason (optional, kept in the history)
          <input value={reason} onChange={e => setReason(e.target.value)} disabled={busy} />
        </label>
        <label className="modal-field">
          Type <span className="mono">{expected}</span> to confirm
          <input value={typed} onChange={e => setTyped(e.target.value)} disabled={busy} autoFocus />
        </label>
        {error && <div className="alert alert-error">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-small" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-danger btn-small" onClick={submit} disabled={busy || typed.trim() !== expected}>
            {busy ? 'Deleting…' : 'Delete dataset'}
          </button>
        </div>
      </div>
    </div>
  )
}
