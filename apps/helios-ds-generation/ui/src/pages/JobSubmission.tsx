import React, { useEffect, useState } from 'react'
import { api } from '../services/api'
import '../styles/JobSubmission.css'

interface ConfigTemplate {
  default_tpcds_scale_factors: number[]
  artifact_types: string[]
  scenario_types: string[]
  difficulty_profiles: Array<{
    name: string
    description: string
    weights: Record<string, number>
  }>
}

interface JobConfig {
  tpcds_scale_factor: number
  master_seed: number
  artifact_targets: Record<string, number>
  difficulty_profile: string
}

interface JobSubmissionProps {
  onJobSubmitted: () => void
}

export function JobSubmission({ onJobSubmitted }: JobSubmissionProps) {
  const [template, setTemplate] = useState<ConfigTemplate | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const [config, setConfig] = useState<JobConfig>({
    tpcds_scale_factor: 1,
    master_seed: 42,
    artifact_targets: {},
    difficulty_profile: 'balanced',
  })

  useEffect(() => {
    fetchTemplate()
  }, [])

  const fetchTemplate = async () => {
    try {
      setLoading(true)
      const data = await api.getConfigTemplate()
      setTemplate(data)

      const defaultConfig = await api.getDefaultConfig()
      if (defaultConfig.artifact_targets) {
        setConfig(prev => ({
          ...prev,
          artifact_targets: Object.entries(defaultConfig.artifact_targets).reduce(
            (acc, [key, val]: [string, any]) => {
              acc[key] = val.target_count
              return acc
            },
            {} as Record<string, number>
          ),
        }))
      }
    } catch (err) {
      setError('Failed to load configuration template')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setSuccess(null)

    try {
      setSubmitting(true)
      await api.submitJob(config)
      setSuccess('Job submitted successfully!')
      onJobSubmitted()

      setTimeout(() => setSuccess(null), 3000)
    } catch (err: any) {
      setError(err.message || 'Failed to submit job')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <div className="loading">Loading...</div>

  return (
    <div className="job-submission">
      <h2>Submit Generation Job</h2>

      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      <form onSubmit={handleSubmit} className="submission-form">
        <fieldset>
          <legend>Dataset Configuration</legend>

          <div className="form-group">
            <label htmlFor="tpcds-scale">TPC-DS Scale Factor</label>
            <select
              id="tpcds-scale"
              value={config.tpcds_scale_factor}
              onChange={e =>
                setConfig(prev => ({
                  ...prev,
                  tpcds_scale_factor: parseInt(e.target.value),
                }))
              }
            >
              {template?.default_tpcds_scale_factors.map(sf => (
                <option key={sf} value={sf}>
                  {sf}
                </option>
              ))}
            </select>
            <small>Larger scale factors produce more data and take longer</small>
          </div>

          <div className="form-group">
            <label htmlFor="master-seed">Master Seed</label>
            <input
              id="master-seed"
              type="number"
              value={config.master_seed}
              onChange={e =>
                setConfig(prev => ({
                  ...prev,
                  master_seed: parseInt(e.target.value),
                }))
              }
            />
            <small>Controls deterministic generation; same seed produces identical output</small>
          </div>
        </fieldset>

        <fieldset>
          <legend>Artifact Targets</legend>
          {template?.artifact_types.map(type => (
            <div key={type} className="form-group">
              <label htmlFor={`target-${type}`}>{type}</label>
              <input
                id={`target-${type}`}
                type="number"
                min="0"
                value={config.artifact_targets[type] || 0}
                onChange={e =>
                  setConfig(prev => ({
                    ...prev,
                    artifact_targets: {
                      ...prev.artifact_targets,
                      [type]: parseInt(e.target.value),
                    },
                  }))
                }
              />
            </div>
          ))}
        </fieldset>

        <fieldset>
          <legend>Difficulty Profile</legend>
          {template?.difficulty_profiles.map(profile => (
            <div key={profile.name} className="form-group">
              <label>
                <input
                  type="radio"
                  name="difficulty"
                  value={profile.name}
                  checked={config.difficulty_profile === profile.name}
                  onChange={e =>
                    setConfig(prev => ({
                      ...prev,
                      difficulty_profile: e.target.value,
                    }))
                  }
                />
                <span>
                  <strong>{profile.name}</strong>: {profile.description}
                </span>
              </label>
            </div>
          ))}
        </fieldset>

        <div className="form-actions">
          <button type="submit" disabled={submitting} className="btn btn-primary">
            {submitting ? 'Submitting...' : 'Submit Job'}
          </button>
        </div>
      </form>
    </div>
  )
}
