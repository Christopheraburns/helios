import { useEffect, useState } from 'react'
import { api, Job } from '../services/api'
import '../styles/JobMonitor.css'

interface JobMonitorProps {
  refreshTrigger: number
  onViewManifest: (datasetId: string) => void
}

export function JobMonitor({ refreshTrigger, onViewManifest }: JobMonitorProps) {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<string>('')

  useEffect(() => {
    fetchJobs()
    const interval = setInterval(fetchJobs, 3000)
    return () => clearInterval(interval)
  }, [statusFilter, refreshTrigger])

  const fetchJobs = async () => {
    try {
      setLoading(true)
      const data = await api.listJobs(statusFilter)
      setJobs(data)
      setError(null)
    } catch (err) {
      setError('Failed to fetch jobs')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const handleCancel = async (jobId: string) => {
    try {
      await api.cancelJob(jobId)
      fetchJobs()
    } catch (err) {
      setError('Failed to cancel job')
      console.error(err)
    }
  }

  const getStatusColor = (state: string) => {
    switch (state) {
      case 'RUNNING':
        return 'status-running'
      case 'SUCCEEDED':
        return 'status-completed'
      case 'FAILED':
        return 'status-failed'
      case 'CANCELLED':
        return 'status-cancelled'
      default:
        return 'status-pending'
    }
  }

  if (loading && jobs.length === 0) return <div className="loading">Loading jobs...</div>

  return (
    <div className="job-monitor">
      <h2>Job Monitor</h2>

      {error && <div className="alert alert-error">{error}</div>}

      <div className="monitor-controls">
        <div className="form-group">
          <label htmlFor="status-filter">Filter by Status</label>
          <select
            id="status-filter"
            value={statusFilter}
            onChange={e => setStatusFilter(e.target.value)}
          >
            <option value="">All Statuses</option>
            <option value="QUEUED">Queued</option>
            <option value="RUNNING">Running</option>
            <option value="SUCCEEDED">Succeeded</option>
            <option value="FAILED">Failed</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
      </div>

      {jobs.length === 0 ? (
        <div className="empty-state">
          <p>No jobs found. Submit a new job to get started.</p>
        </div>
      ) : (
        <div className="jobs-list">
          {jobs.map(job => (
            <div key={job.job_id} className="job-card">
              <div className="job-header">
                <div className="job-info">
                  <h3>{job.job_id.substring(0, 8)}...</h3>
                  <span className={`status-badge ${getStatusColor(job.state)}`}>
                    {job.state}
                  </span>
                </div>
                {(job.state === 'QUEUED' || job.state === 'RUNNING') && (
                  <button
                    className="btn btn-small btn-danger"
                    onClick={() => handleCancel(job.job_id)}
                  >
                    Cancel
                  </button>
                )}
              </div>

              <div className="job-content">
                <div className="job-stat">
                  <span className="stat-label">Progress</span>
                  <div className="progress-bar">
                    <div
                      className="progress-fill"
                      style={{ width: `${job.progress_percent}%` }}
                    />
                  </div>
                  <span className="stat-value">{job.progress_percent}%</span>
                </div>

                {job.created_at && (
                  <div className="job-stat">
                    <span className="stat-label">Started</span>
                    <span className="stat-value">
                      {new Date(job.created_at).toLocaleString()}
                    </span>
                  </div>
                )}

                {job.dataset_id && (
                  <div className="job-stat">
                    <span className="stat-label">Dataset</span>
                    <span className="stat-value">{job.dataset_id.substring(0, 8)}…</span>
                    <button
                      className="btn btn-small btn-primary"
                      onClick={() => onViewManifest(job.dataset_id as string)}
                    >
                      View manifest
                    </button>
                  </div>
                )}
              </div>

              {job.message && (
                <p className={job.state === 'FAILED' ? 'job-message error' : 'job-message'}>
                  {job.message}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
