import { useCallback, useEffect, useState } from 'react'
import { ManifestViewer, short, stateClass } from '../components/ManifestViewer'
import { api, DatasetSummary } from '../services/api'
import '../styles/DatasetBrowser.css'

interface DatasetBrowserProps {
  selectedDatasetId: string | null
  onSelect: (datasetId: string) => void
}

export function DatasetBrowser({ selectedDatasetId, onSelect }: DatasetBrowserProps) {
  const [datasets, setDatasets] = useState<DatasetSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

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
              {d.scenario_count} scenarios · {d.planned_artifact_count} planned assets
            </span>
            {d.created_at && (
              <span className="muted">{new Date(d.created_at).toLocaleString()}</span>
            )}
          </button>
        ))}
      </aside>

      <section className="dataset-detail">
        {selectedDatasetId ? (
          <ManifestViewer key={selectedDatasetId} datasetId={selectedDatasetId} />
        ) : (
          <div className="empty-state">Select a dataset to view its manifest.</div>
        )}
      </section>
    </div>
  )
}
