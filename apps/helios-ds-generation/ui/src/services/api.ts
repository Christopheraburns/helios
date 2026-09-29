import axios, { AxiosInstance } from 'axios'

const apiClient: AxiosInstance = axios.create({
  baseURL: '/v1',
  timeout: 10000,
})

// Lakehouse reads plus a first-time manifest fetch from object storage can take a few seconds.
const SLOW = { timeout: 30000 }

export interface GenerationRequest {
  source: { catalog: string; database: string; scale_factor: number }
  master_seed: number
  profile: string
  artifact_counts: Record<string, number>
  security_profile: string
  difficulty_profile: string
}

export interface Job {
  job_id: string
  state: string
  config_hash: string
  dataset_id?: string | null
  created_at: string
  updated_at: string
  progress_percent: number
  artifacts_generated: number
  workbench_run_id?: string | null
  message?: string | null
  error?: string | null
}

export interface LifecycleEvent {
  event_seq: number
  state: string
  actor: string
  occurred_at: string
  reason?: string | null
}

export interface DatasetSummary {
  dataset_id: string
  state: string | null
  created_at: string | null
  scenario_count: number
  planned_artifact_count: number
  manifest_sha256: string
  manifest_uri: string | null
  config_hash: string
  template_bundle_hash: string
  source_fingerprint_hash: string
  generator_version: string
  generator_schema_version: string
  lifecycle: LifecycleEvent[]
}

export interface TableIdentity {
  rows: number | null
  schema_sha256: string
  snapshot_id?: number | null
  content_sha256?: string
}

export interface ManifestOverview {
  manifest_schema_version: string
  dataset_id: string
  manifest_sha256: string
  size_bytes: number
  identity: Record<string, string>
  config: Record<string, unknown>
  source_fingerprint: { backend: string; tables: Record<string, TableIdentity> }
  templates: Record<string, string>[]
  artifact_counts: Record<string, { target: number; planned: number }>
  scenario_counts: Record<string, { requested: number; eligible: number; planned: number }>
}

export interface ScenarioSummary {
  scenario_id: string
  scenario_type: string
  business_key: string
  headline: string
  artifact_types: string[]
}

export interface ScenarioPage {
  total: number
  offset: number
  limit: number
  items: ScenarioSummary[]
}

export interface ArtifactPlan {
  artifact_id: string
  artifact_type: string
  ordinal: number
  template_id: string
  template_version: string
  artifact_seed: string
}

export interface ScenarioDetail {
  scenario_id: string
  scenario_type: string
  business_key: string
  headline: string
  rank_score: string
  scenario_seed: string
  source_refs: { table: string; key: Record<string, unknown> }[]
  facts: Record<string, unknown>
  artifacts: ArtifactPlan[]
}

export const api = {
  async getConfigTemplate() {
    const response = await apiClient.get('/config/template')
    return response.data
  },

  async getDefaultRequest(): Promise<GenerationRequest> {
    const response = await apiClient.get('/config/default')
    return response.data
  },

  async createGeneration(request: GenerationRequest) {
    const response = await apiClient.post('/generations', request, SLOW)
    return response.data
  },

  async listJobs(state?: string, limit = 50): Promise<Job[]> {
    const params = new URLSearchParams()
    if (state) params.append('state', state)
    params.append('limit', String(limit))

    const response = await apiClient.get('/jobs', { params, ...SLOW })
    return response.data
  },

  async getJob(jobId: string): Promise<Job> {
    const response = await apiClient.get(`/jobs/${jobId}`)
    return response.data
  },

  async cancelJob(jobId: string): Promise<Job> {
    const response = await apiClient.post(`/jobs/${jobId}:cancel`, undefined, SLOW)
    return response.data
  },

  async listDatasets(): Promise<DatasetSummary[]> {
    const response = await apiClient.get('/datasets', SLOW)
    return response.data
  },

  async getDataset(datasetId: string): Promise<DatasetSummary> {
    const response = await apiClient.get(`/datasets/${datasetId}`, SLOW)
    return response.data
  },

  async getManifest(datasetId: string): Promise<ManifestOverview> {
    const response = await apiClient.get(`/datasets/${datasetId}/manifest`, SLOW)
    return response.data
  },

  async listScenarios(
    datasetId: string,
    options: { scenarioType?: string; q?: string; offset?: number; limit?: number } = {}
  ): Promise<ScenarioPage> {
    const params = new URLSearchParams()
    if (options.scenarioType) params.append('scenario_type', options.scenarioType)
    if (options.q) params.append('q', options.q)
    params.append('offset', String(options.offset ?? 0))
    params.append('limit', String(options.limit ?? 25))
    const response = await apiClient.get(`/datasets/${datasetId}/scenarios`, { params, ...SLOW })
    return response.data
  },

  async getScenario(datasetId: string, scenarioId: string): Promise<ScenarioDetail> {
    const response = await apiClient.get(`/datasets/${datasetId}/scenarios/${scenarioId}`, SLOW)
    return response.data
  },

  rawManifestUrl(datasetId: string): string {
    return `/v1/datasets/${datasetId}/manifest/raw`
  },
}
