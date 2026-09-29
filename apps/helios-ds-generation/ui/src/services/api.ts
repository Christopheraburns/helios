import axios, { AxiosInstance } from 'axios'

const apiClient: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

export const api = {
  async getConfigTemplate() {
    const response = await apiClient.get('/config/template')
    return response.data
  },

  async getDefaultConfig() {
    const response = await apiClient.get('/config/default')
    return response.data
  },

  async submitJob(config: any) {
    const response = await apiClient.post('/jobs/submit', config)
    return response.data
  },

  async listJobs(status?: string, limit = 50) {
    const params = new URLSearchParams()
    if (status) params.append('status', status)
    params.append('limit', String(limit))

    const response = await apiClient.get('/jobs', { params })
    return response.data
  },

  async getJobStatus(jobId: string) {
    const response = await apiClient.get(`/jobs/${jobId}`)
    return response.data
  },

  async cancelJob(jobId: string) {
    const response = await apiClient.post(`/jobs/${jobId}/cancel`)
    return response.data
  },

  async getDatasetStats(datasetId: string) {
    const response = await apiClient.get(`/results/datasets/${datasetId}/stats`)
    return response.data
  },

  async listArtifacts(datasetId: string, artifactType?: string) {
    const params = new URLSearchParams()
    if (artifactType) params.append('artifact_type', artifactType)

    const response = await apiClient.get(`/results/datasets/${datasetId}/artifacts`, {
      params,
    })
    return response.data
  },

  async getDatasetSchema(datasetId: string) {
    const response = await apiClient.get(`/results/datasets/${datasetId}/schema`)
    return response.data
  },
}
