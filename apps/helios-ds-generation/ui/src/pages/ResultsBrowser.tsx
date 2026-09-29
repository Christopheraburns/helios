import React, { useState } from 'react'
import '../styles/ResultsBrowser.css'

interface ResultsBrowserProps {}

export function ResultsBrowser({}: ResultsBrowserProps) {
  const [datasetId, setDatasetId] = useState('')
  const [view, setView] = useState<'stats' | 'artifacts' | 'schema'>('stats')

  const handleViewStats = () => {
    if (!datasetId) {
      alert('Please enter a dataset ID')
      return
    }
    setView('stats')
  }

  const handleViewArtifacts = () => {
    if (!datasetId) {
      alert('Please enter a dataset ID')
      return
    }
    setView('artifacts')
  }

  const handleViewSchema = () => {
    if (!datasetId) {
      alert('Please enter a dataset ID')
      return
    }
    setView('schema')
  }

  return (
    <div className="results-browser">
      <h2>Browse Results</h2>

      <div className="results-controls">
        <div className="form-group">
          <label htmlFor="dataset-id">Dataset ID</label>
          <input
            id="dataset-id"
            type="text"
            placeholder="Enter dataset ID to browse results"
            value={datasetId}
            onChange={e => setDatasetId(e.target.value)}
          />
        </div>

        <div className="view-buttons">
          <button className="btn" onClick={handleViewStats}>
            Statistics
          </button>
          <button className="btn" onClick={handleViewArtifacts}>
            Artifacts
          </button>
          <button className="btn" onClick={handleViewSchema}>
            Schema
          </button>
        </div>
      </div>

      {datasetId && view === 'stats' && (
        <div className="results-content">
          <div className="placeholder">
            <p>Statistics for dataset {datasetId.substring(0, 8)}...</p>
            <p>Loading artifact counts, entity statistics, and scenario breakdowns...</p>
          </div>
        </div>
      )}

      {datasetId && view === 'artifacts' && (
        <div className="results-content">
          <div className="placeholder">
            <p>Artifacts for dataset {datasetId.substring(0, 8)}...</p>
            <p>Displaying generated PDFs, emails, images, etc. with source provenance...</p>
          </div>
        </div>
      )}

      {datasetId && view === 'schema' && (
        <div className="results-content">
          <div className="placeholder">
            <p>Schema for dataset {datasetId.substring(0, 8)}...</p>
            <p>Iceberg schemas for helios_ds.*, helios_ground_truth.*, helios_index.*</p>
          </div>
        </div>
      )}

      {!datasetId && (
        <div className="empty-state">
          <p>Enter a dataset ID to browse results.</p>
        </div>
      )}
    </div>
  )
}
