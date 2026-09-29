import { useEffect, useState } from 'react'
import {
  api,
  DatasetSummary,
  ManifestOverview,
  ScenarioDetail,
  ScenarioPage,
} from '../services/api'

const SCENARIO_LABELS: Record<string, string> = {
  product_return_damage: 'Damaged product return',
  warehouse_inventory_issue: 'Warehouse low stock',
  promotion_performance: 'Promotion',
  customer_complaint: 'Web return complaint',
}

const PAGE_SIZE = 25

type Tab = 'summary' | 'scenarios' | 'source' | 'templates' | 'config'

export const scenarioLabel = (type: string) => SCENARIO_LABELS[type] ?? type

export const short = (value: string | null | undefined, n = 12) =>
  value ? (value.length > n ? `${value.slice(0, n)}…` : value) : '—'

export const stateClass = (state: string | null) => {
  switch (state) {
    case 'READY':
      return 'status-completed'
    case 'IN_REVIEW':
    case 'VALIDATING':
    case 'CREATING':
      return 'status-running'
    case 'FAILED':
    case 'REJECTED':
      return 'status-failed'
    default:
      return 'status-pending'
  }
}

const formatBytes = (bytes: number) =>
  bytes > 1_000_000 ? `${(bytes / 1_000_000).toFixed(2)} MB` : `${(bytes / 1000).toFixed(1)} KB`

const display = (value: unknown) =>
  value === null || value === undefined ? '—' : typeof value === 'object' ? JSON.stringify(value) : String(value)

interface ManifestViewerProps {
  datasetId: string
}

export function ManifestViewer({ datasetId }: ManifestViewerProps) {
  const [tab, setTab] = useState<Tab>('summary')
  const [dataset, setDataset] = useState<DatasetSummary | null>(null)
  const [manifest, setManifest] = useState<ManifestOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    Promise.all([api.getDataset(datasetId), api.getManifest(datasetId)])
      .then(([d, m]) => {
        if (!cancelled) {
          setDataset(d)
          setManifest(m)
        }
      })
      .catch(err => {
        if (!cancelled) setError(err.response?.data?.detail ?? err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [datasetId])

  if (loading) return <div className="loading">Loading manifest…</div>
  if (error) return <div className="alert alert-error">{String(error)}</div>
  if (!dataset || !manifest) return null

  return (
    <div className="manifest-viewer">
      <div className="manifest-header">
        <div>
          <h3 className="mono">{dataset.dataset_id}</h3>
          <span className={`status-badge ${stateClass(dataset.state)}`}>{dataset.state ?? 'UNKNOWN'}</span>
          <span className="muted">
            {dataset.created_at && ` created ${new Date(dataset.created_at).toLocaleString()}`}
            {` · generator ${dataset.generator_version}`}
          </span>
        </div>
        <a className="btn btn-primary btn-small" href={api.rawManifestUrl(datasetId)} download>
          Download manifest ({formatBytes(manifest.size_bytes)})
        </a>
      </div>

      <div className="tabs">
        {(['summary', 'scenarios', 'source', 'templates', 'config'] as Tab[]).map(t => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t === 'scenarios' ? `Scenarios (${dataset.scenario_count})` : t[0].toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === 'summary' && <SummaryTab dataset={dataset} manifest={manifest} />}
      {tab === 'scenarios' && <ScenariosTab datasetId={datasetId} manifest={manifest} />}
      {tab === 'source' && <SourceTab manifest={manifest} />}
      {tab === 'templates' && <TemplatesTab manifest={manifest} />}
      {tab === 'config' && <pre className="json">{JSON.stringify(manifest.config, null, 2)}</pre>}
    </div>
  )
}

function SummaryTab({ dataset, manifest }: { dataset: DatasetSummary; manifest: ManifestOverview }) {
  return (
    <div className="tab-body">
      <div className="stat-cards">
        <div className="stat-card">
          <span className="stat-number">{dataset.scenario_count}</span>
          <span className="stat-caption">scenarios</span>
        </div>
        <div className="stat-card">
          <span className="stat-number">{dataset.planned_artifact_count}</span>
          <span className="stat-caption">planned artifacts</span>
        </div>
        <div className="stat-card">
          <span className="stat-number">0</span>
          <span className="stat-caption">rendered artifacts (phase 3)</span>
        </div>
      </div>

      <h4>Artifacts: target vs planned</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>Type</th>
            <th className="num">Target</th>
            <th className="num">Planned</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {Object.entries(manifest.artifact_counts).map(([type, c]) => (
            <tr key={type}>
              <td>{type}</td>
              <td className="num">{c.target}</td>
              <td className={`num ${c.planned < c.target ? 'short' : ''}`}>{c.planned}</td>
              <td className="bar-cell">
                <div className="progress-bar">
                  <div
                    className="progress-fill"
                    style={{ width: `${c.target ? (100 * c.planned) / c.target : 0}%` }}
                  />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Scenarios chosen from TPC-DS</h4>
      <table className="data-table">
        <thead>
          <tr>
            <th>Scenario</th>
            <th className="num">Eligible records</th>
            <th className="num">Requested</th>
            <th className="num">Planned</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(manifest.scenario_counts).map(([type, c]) => (
            <tr key={type}>
              <td>{scenarioLabel(type)}</td>
              <td className="num">{c.eligible.toLocaleString()}</td>
              <td className="num">{c.requested}</td>
              <td className={`num ${c.planned < c.requested ? 'short' : ''}`}>{c.planned}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h4>Lifecycle</h4>
      <ol className="timeline">
        {dataset.lifecycle.map(e => (
          <li key={e.event_seq}>
            <span className={`status-badge ${stateClass(e.state)}`}>{e.state}</span>
            <span className="muted">
              {new Date(e.occurred_at).toLocaleString()} · {e.actor}
            </span>
            {e.reason && <span className="reason">{e.reason}</span>}
          </li>
        ))}
      </ol>

      <details className="technical">
        <summary>Identity (why the dataset has this ID)</summary>
        <table className="data-table">
          <tbody>
            {Object.entries(manifest.identity).map(([k, v]) => (
              <tr key={k}>
                <td>{k}</td>
                <td className="mono">{v}</td>
              </tr>
            ))}
            <tr>
              <td>manifest_sha256</td>
              <td className="mono">{manifest.manifest_sha256}</td>
            </tr>
            <tr>
              <td>manifest location</td>
              <td className="mono">{dataset.manifest_uri}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </div>
  )
}

function ScenariosTab({ datasetId, manifest }: { datasetId: string; manifest: ManifestOverview }) {
  const [scenarioType, setScenarioType] = useState('')
  const [query, setQuery] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<ScenarioPage | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setError(null)
    api
      .listScenarios(datasetId, { scenarioType, q: search, offset, limit: PAGE_SIZE })
      .then(setPage)
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [datasetId, scenarioType, search, offset])

  const applyFilter = (next: () => void) => {
    next()
    setOffset(0)
    setSelected(null)
  }

  return (
    <div className="tab-body">
      <form
        className="scenario-filters"
        onSubmit={e => {
          e.preventDefault()
          applyFilter(() => setSearch(query))
        }}
      >
        <select value={scenarioType} onChange={e => applyFilter(() => setScenarioType(e.target.value))}>
          <option value="">All scenario types</option>
          {Object.keys(manifest.scenario_counts).map(t => (
            <option key={t} value={t}>
              {scenarioLabel(t)}
            </option>
          ))}
        </select>
        <input
          type="search"
          placeholder="Search names, products, stores, keys…"
          value={query}
          onChange={e => setQuery(e.target.value)}
        />
        <button type="submit" className="btn btn-primary btn-small">
          Search
        </button>
      </form>

      {error && <div className="alert alert-error">{String(error)}</div>}
      {!page ? (
        <div className="loading">Loading scenarios…</div>
      ) : (
        <>
          <table className="data-table scenario-table">
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Story</th>
                <th>Planned assets</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map(s => (
                <tr
                  key={s.scenario_id}
                  className={`clickable ${selected === s.scenario_id ? 'selected' : ''}`}
                  onClick={() => setSelected(selected === s.scenario_id ? null : s.scenario_id)}
                >
                  <td className="nowrap">{scenarioLabel(s.scenario_type)}</td>
                  <td>{s.headline}</td>
                  <td>
                    {s.artifact_types.map(t => (
                      <span key={t} className="chip">
                        {t}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
              {page.items.length === 0 && (
                <tr>
                  <td colSpan={3} className="empty-state">
                    No scenarios match.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <div className="pager">
            <button
              className="btn btn-small"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
            >
              ← Previous
            </button>
            <span className="muted">
              {page.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}
            </span>
            <button
              className="btn btn-small"
              disabled={offset + PAGE_SIZE >= page.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next →
            </button>
          </div>
        </>
      )}

      {selected && <ScenarioDetailPanel datasetId={datasetId} scenarioId={selected} />}
    </div>
  )
}

function ScenarioDetailPanel({ datasetId, scenarioId }: { datasetId: string; scenarioId: string }) {
  const [detail, setDetail] = useState<ScenarioDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setDetail(null)
    setError(null)
    api
      .getScenario(datasetId, scenarioId)
      .then(setDetail)
      .catch(err => setError(err.response?.data?.detail ?? err.message))
  }, [datasetId, scenarioId])

  if (error) return <div className="alert alert-error">{String(error)}</div>
  if (!detail) return <div className="loading">Loading scenario…</div>

  return (
    <div className="scenario-detail">
      <h4>{detail.headline}</h4>
      <p className="muted">{scenarioLabel(detail.scenario_type)}</p>

      <div className="detail-grid">
        <div>
          <h5>Planned assets</h5>
          <table className="data-table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Template</th>
              </tr>
            </thead>
            <tbody>
              {detail.artifacts.map(a => (
                <tr key={a.artifact_id}>
                  <td>{a.artifact_type}</td>
                  <td>
                    {a.template_id} <span className="muted">v{a.template_version}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <h5>TPC-DS source rows</h5>
          <ul className="source-refs">
            {detail.source_refs.map((ref, i) => (
              <li key={i}>
                <strong>{ref.table}</strong>{' '}
                <span className="mono">
                  {Object.entries(ref.key)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(', ')}
                </span>
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h5>Facts from TPC-DS</h5>
          <table className="data-table facts">
            <tbody>
              {Object.entries(detail.facts).map(([k, v]) => (
                <tr key={k}>
                  <td className="mono">{k}</td>
                  <td>{display(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <details className="technical">
        <summary>Technical details</summary>
        <table className="data-table">
          <tbody>
            <tr>
              <td>scenario_id</td>
              <td className="mono">{detail.scenario_id}</td>
            </tr>
            <tr>
              <td>business_key</td>
              <td className="mono">{detail.business_key}</td>
            </tr>
            <tr>
              <td>scenario_seed</td>
              <td className="mono">{detail.scenario_seed}</td>
            </tr>
            <tr>
              <td>rank_score</td>
              <td className="mono">{detail.rank_score}</td>
            </tr>
            {detail.artifacts.map(a => (
              <tr key={a.artifact_id}>
                <td>{a.artifact_type} artifact</td>
                <td className="mono">
                  id {a.artifact_id}
                  <br />
                  seed {a.artifact_seed}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </div>
  )
}

function SourceTab({ manifest }: { manifest: ManifestOverview }) {
  const { backend, tables } = manifest.source_fingerprint
  return (
    <div className="tab-body">
      <p className="muted">
        TPC-DS source read through <strong>{backend}</strong>. The dataset ID changes if any of these tables
        changes.
      </p>
      <table className="data-table">
        <thead>
          <tr>
            <th>Table</th>
            <th className="num">Rows</th>
            <th>{backend === 'impala' ? 'Iceberg snapshot' : 'Content hash'}</th>
            <th>Schema hash</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(tables).map(([name, t]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{t.rows?.toLocaleString() ?? '—'}</td>
              <td className="mono">{t.snapshot_id ?? short(t.content_sha256, 16)}</td>
              <td className="mono">{short(t.schema_sha256, 16)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TemplatesTab({ manifest }: { manifest: ManifestOverview }) {
  return (
    <div className="tab-body">
      <table className="data-table">
        <thead>
          <tr>
            <th>Template</th>
            <th>Type</th>
            <th>Version</th>
            <th>Content hash</th>
          </tr>
        </thead>
        <tbody>
          {manifest.templates.map(t => (
            <tr key={t.template_id}>
              <td>{t.template_id}</td>
              <td>{t.artifact_type}</td>
              <td>{t.template_version}</td>
              <td className="mono">{short(t.content_hash, 16)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
