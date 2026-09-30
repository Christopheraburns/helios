import React, { useEffect, useState } from 'react'
import { scenarioLabel } from '../components/ManifestViewer'
import { api, GenerationRequest } from '../services/api'
import '../styles/JobSubmission.css'

interface ConfigTemplate {
  scale_factors: number[]
  artifact_types: string[]
  scenario_types: string[]
  difficulty_profiles: Array<{
    name: string
    description: string
    weights: Record<string, number>
  }>
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

  const [config, setConfig] = useState<GenerationRequest | null>(null)

  useEffect(() => {
    fetchTemplate()
  }, [])

  const fetchTemplate = async () => {
    try {
      setLoading(true)
      const data = await api.getConfigTemplate()
      setTemplate(data)

      setConfig(await api.getDefaultRequest())
    } catch (err) {
      setError('Failed to load configuration template')
      console.error(err)
    } finally {
      setLoading(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!config) return
    setError(null)
    setSuccess(null)

    try {
      setSubmitting(true)
      await api.createGeneration(config)
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
  if (!config) return <div className="alert alert-error">{error ?? 'No configuration available'}</div>

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
              value={config.source.scale_factor}
              onChange={e =>
                setConfig({
                  ...config,
                  source: { ...config.source, scale_factor: parseInt(e.target.value) },
                })
              }
            >
              {template?.scale_factors.map(sf => (
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
              onChange={e => setConfig({ ...config, master_seed: parseInt(e.target.value) })}
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
                value={config.artifact_counts[type] || 0}
                onChange={e =>
                  setConfig({
                    ...config,
                    artifact_counts: {
                      ...config.artifact_counts,
                      [type]: parseInt(e.target.value) || 0,
                    },
                  })
                }
              />
            </div>
          ))}
        </fieldset>

        <fieldset>
          <legend>Scenarios</legend>
          <small>
            Relative weights; 0 leaves a story type out. Currently rendered: damaged product
            return PDFs, emails and chats. Other types are planned but not rendered yet.
          </small>
          {template?.scenario_types.map(type => (
            <div key={type} className="form-group">
              <label htmlFor={`scenario-${type}`}>{scenarioLabel(type)}</label>
              <input
                id={`scenario-${type}`}
                type="number"
                min="0"
                max="1"
                step="0.05"
                value={config.scenarios?.[type] ?? 0}
                onChange={e =>
                  setConfig({
                    ...config,
                    scenarios: { ...(config.scenarios ?? {}), [type]: parseFloat(e.target.value) || 0 },
                  })
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
                  onChange={e => setConfig({ ...config, difficulty_profile: e.target.value })}
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
