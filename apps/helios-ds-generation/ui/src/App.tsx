import { useCallback, useState } from 'react'
import { JobSubmission } from './pages/JobSubmission'
import { JobMonitor } from './pages/JobMonitor'
import { DatasetBrowser } from './pages/DatasetBrowser'
import './styles/App.css'

type Tab = 'submit' | 'monitor' | 'datasets'

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('submit')
  const [refreshJobs, setRefreshJobs] = useState(0)
  const [selectedDatasetId, setSelectedDatasetId] = useState<string | null>(null)

  const viewManifest = useCallback((datasetId: string) => {
    setSelectedDatasetId(datasetId)
    setActiveTab('datasets')
  }, [])

  const handleJobSubmitted = () => {
    setActiveTab('monitor')
    setRefreshJobs(prev => prev + 1)
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Helios-DS Generation</h1>
        <p>Deterministic Synthetic Enterprise Data Generator</p>
      </header>

      <nav className="app-nav">
        <button
          className={`nav-button ${activeTab === 'submit' ? 'active' : ''}`}
          onClick={() => setActiveTab('submit')}
        >
          Submit Job
        </button>
        <button
          className={`nav-button ${activeTab === 'monitor' ? 'active' : ''}`}
          onClick={() => setActiveTab('monitor')}
        >
          Monitor Jobs
        </button>
        <button
          className={`nav-button ${activeTab === 'datasets' ? 'active' : ''}`}
          onClick={() => setActiveTab('datasets')}
        >
          Datasets &amp; Manifests
        </button>
      </nav>

      <main className="app-content">
        {activeTab === 'submit' && <JobSubmission onJobSubmitted={handleJobSubmitted} />}
        {activeTab === 'monitor' && (
          <JobMonitor refreshTrigger={refreshJobs} onViewManifest={viewManifest} />
        )}
        {activeTab === 'datasets' && (
          <DatasetBrowser selectedDatasetId={selectedDatasetId} onSelect={setSelectedDatasetId} />
        )}
      </main>

      <footer className="app-footer">
        <p>Helios-DS-Generation v0.1.0</p>
      </footer>
    </div>
  )
}

export default App
