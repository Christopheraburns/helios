import { useState } from 'react'
import { JobSubmission } from './pages/JobSubmission'
import { JobMonitor } from './pages/JobMonitor'
import { ResultsBrowser } from './pages/ResultsBrowser'
import './styles/App.css'

type Tab = 'submit' | 'monitor' | 'results'

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('submit')
  const [refreshJobs, setRefreshJobs] = useState(0)

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
          className={`nav-button ${activeTab === 'results' ? 'active' : ''}`}
          onClick={() => setActiveTab('results')}
        >
          Browse Results
        </button>
      </nav>

      <main className="app-content">
        {activeTab === 'submit' && <JobSubmission onJobSubmitted={handleJobSubmitted} />}
        {activeTab === 'monitor' && <JobMonitor refreshTrigger={refreshJobs} />}
        {activeTab === 'results' && <ResultsBrowser />}
      </main>

      <footer className="app-footer">
        <p>Helios-DS-Generation v0.1.0</p>
      </footer>
    </div>
  )
}

export default App
