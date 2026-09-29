import axios, { AxiosInstance } from 'axios'

const apiClient: AxiosInstance = axios.create({
  baseURL: '/v1',
  timeout: 10000,
})

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
  error?: string | null
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
    const response = await apiClient.post('/generations', request)
    return response.data
  },

  async listJobs(state?: string, limit = 50): Promise<Job[]> {
    const params = new URLSearchParams()
    if (state) params.append('state', state)
    params.append('limit', String(limit))

    const response = await apiClient.get('/jobs', { params })
    return response.data
  },

  async getJob(jobId: string): Promise<Job> {
    const response = await apiClient.get(`/jobs/${jobId}`)
    return response.data
  },

  async cancelJob(jobId: string): Promise<Job> {
    const response = await apiClient.post(`/jobs/${jobId}:cancel`)
    return response.data
  },

  async getDataset(datasetId: string) {
    const response = await apiClient.get(`/datasets/${datasetId}`)
    return response.data
  },

  async listArtifacts(datasetId: string) {
    const response = await apiClient.get(`/datasets/${datasetId}/artifacts`)
    return response.data
  },
}
