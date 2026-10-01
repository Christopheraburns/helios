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
  scenarios?: Record<string, number> | null
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
  related_dataset_id?: string | null
}

export interface DatasetSummary {
  dataset_id: string
  state: string | null
  created_at: string | null
  scenario_count: number
  planned_artifact_count: number
  rendered_artifact_count: number
  rendered_by_type: Record<string, number>
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

export interface ArtifactSummary {
  artifact_id: string
  scenario_id: string
  artifact_type: string
  template_id: string
  template_version: string
  mime_type: string
  size_bytes: number
  sha256: string
  semantic_timestamp: string
  content_uri: string
  preview_uri: string
  scenario_type?: string | null
  headline?: string | null
}

export interface ChatMessage {
  message_id: string
  timestamp: string
  sender: string
  sender_name: string
  text: string
}

export interface ChatThread {
  schema: string
  thread_id: string
  channel: string
  participants: { sender: string; name: string; role: string }[]
  messages: ChatMessage[]
}

export interface ArtifactPreviewData {
  artifact: ArtifactSummary
  kind: 'pdf' | 'email' | 'chat' | 'other'
  email?: { headers: Record<string, string>; body: string } | null
  chat?: ChatThread | null
}

export interface WhoAmI {
  user: string | null
  identity_header: string | null
  headers_seen: string[]
}

export interface ReviewItem {
  artifact_id: string
  artifact_type: string
  template_id: string
  scenario_id: string
  headline: string | null
  status: string
  comment_count: number
  last_mark_at: string | null
}

export interface DatasetReview {
  dataset: DatasetSummary
  counts: { total: number; unreviewed: number; accepted: number; flagged: number; comments: number }
  items: ReviewItem[]
  lineage: LineageMember[]
}

export interface LineageMember {
  dataset_id: string
  state: string | null
  created_at: string | null
}

export interface DecisionResult {
  dataset: DatasetSummary
  superseded: string[]
}

export interface GoldenQuestion {
  query_id: string
  kind: string | null
  difficulty: string | null
  question: string
  principal_id: string
  result_type: string | null
  answer: string | null
  result: {
    abstain?: boolean
    claims?: { claim_id: string; claim_type: string; statement: string | null }[]
    evidence?: { evidence_id: string; artifact_id: string; excerpt: string | null }[]
    evidence_mention_tiers?: string[]
    alias?: string
    canonical_name?: string | null
    items?: { item: string; store: string; rma: string }[]
    [key: string]: unknown
  }
  required_structured: { table: string; sql: string; rows: unknown[][]; rows_sha256: string } | null
  required_entities: string[]
  required_artifacts: string[]
  required_claims: string[]
  required_evidence: string[]
}

export interface DeleteResult {
  dataset_id: string
  objects_deleted: number
  tables_purged: string[]
}

export interface AuditEvent {
  occurred_at: string
  kind: 'lifecycle' | 'review'
  action: string
  actor: string
  dataset_id: string
  artifact_id?: string | null
  note?: string | null
  related_dataset_id?: string | null
  event_seq?: number | null
  review_counts?: Record<string, number> | null
}

export interface Locator {
  part?: string
  header?: string
  message_id?: string
  page?: number
  text?: string
  start?: number
  end?: number
}

export interface MentionView {
  mention_id: string
  entity_id: string
  entity_type: string
  canonical_name: string
  surface_form: string
  locator: Locator
  difficulty?: string | null
}

export interface EvidenceView {
  evidence_id: string
  claim_id: string
  claim_type: string
  statement: string | null
  truth_status: string
  excerpt: string | null
  locator: Locator
}

export interface RelationshipView {
  predicate: string
  source: string
  target: string
  from_this_artifact: boolean
}

export interface MarkView {
  mark_id: string
  status: string
  comment: string | null
  reviewer: string
  created_at: string
}

export interface ReviewBundle {
  preview: ArtifactPreviewData
  scenario: {
    scenario_id: string
    scenario_type: string
    headline: string
    facts: Record<string, unknown>
    source_refs: { table: string; key: Record<string, unknown> }[]
  } | null
  mentions: MentionView[]
  evidence: EvidenceView[]
  relationships: RelationshipView[]
  status: string
  marks: MarkView[]
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

  async listDatasets(state?: string): Promise<DatasetSummary[]> {
    const response = await apiClient.get('/datasets', { params: state ? { state } : {}, ...SLOW })
    return response.data
  },

  async whoami(): Promise<WhoAmI> {
    const response = await apiClient.get('/whoami')
    return response.data
  },

  async getDatasetReview(datasetId: string): Promise<DatasetReview> {
    const response = await apiClient.get(`/review/datasets/${datasetId}`, SLOW)
    return response.data
  },

  async approveDataset(datasetId: string, note?: string): Promise<DecisionResult> {
    const response = await apiClient.post(
      `/review/datasets/${datasetId}:approve`,
      { note: note || null },
      SLOW
    )
    return response.data
  },

  async rejectDataset(datasetId: string, reason: string): Promise<DecisionResult> {
    const response = await apiClient.post(`/review/datasets/${datasetId}:reject`, { note: reason }, SLOW)
    return response.data
  },

  async deleteDataset(datasetId: string, reason?: string): Promise<DeleteResult> {
    const response = await apiClient.post(
      `/datasets/${datasetId}:delete`,
      { reason: reason || null },
      { timeout: 120000 }
    )
    return response.data
  },

  async getDatasetHistory(datasetId: string): Promise<AuditEvent[]> {
    const response = await apiClient.get(`/review/datasets/${datasetId}/history`, SLOW)
    return response.data
  },

  async getReviewBundle(artifactId: string): Promise<ReviewBundle> {
    const response = await apiClient.get(`/review/artifacts/${artifactId}`, SLOW)
    return response.data
  },

  async addMark(artifactId: string, status: string, comment?: string): Promise<MarkView> {
    const response = await apiClient.post(
      `/review/artifacts/${artifactId}/marks`,
      { status, comment: comment || null },
      SLOW
    )
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

  async listGoldenQuestions(datasetId: string): Promise<GoldenQuestion[]> {
    const response = await apiClient.get(`/datasets/${datasetId}/golden-questions`, SLOW)
    return response.data
  },

  async listArtifacts(datasetId: string): Promise<ArtifactSummary[]> {
    const response = await apiClient.get(`/datasets/${datasetId}/artifacts`, SLOW)
    return response.data
  },

  async getArtifactPreview(artifactId: string): Promise<ArtifactPreviewData> {
    const response = await apiClient.get(`/artifacts/${artifactId}/preview`, SLOW)
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
