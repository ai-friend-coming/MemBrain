import type { Dataset, FactsPage, MemoryGraph, RunTagInfo, SessionSummariesPage, Task, TaskDetail } from '@/types'
import { API_BASE_URL } from '@/constants'

export class APIError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'APIError'
    this.status = status
  }
}

async function get<T>(endpoint: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${endpoint}`)
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new APIError(body?.detail ?? 'Request failed', res.status)
  }
  return res.json() as Promise<T>
}

export function listDatasets(): Promise<Dataset[]> {
  return get<Dataset[]>('/datasets')
}

export function listTasks(datasetId: number): Promise<Task[]> {
  return get<Task[]>(`/datasets/${datasetId}/tasks`)
}

export function getTask(taskId: number): Promise<TaskDetail> {
  return get<TaskDetail>(`/tasks/${taskId}`)
}

export function getTaskMemory(taskId: number, runTag: string): Promise<MemoryGraph> {
  return get<MemoryGraph>(`/tasks/${taskId}/runs/${encodeURIComponent(runTag)}/memory`)
}

export function getTaskFacts(
  taskId: number,
  runTag: string,
  limit: number,
  offset: number,
  batchIndex?: number | null,
  search?: string | null,
): Promise<FactsPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  if (batchIndex != null)
    params.set('batch_index', String(batchIndex))
  if (search)
    params.set('search', search)
  return get<FactsPage>(`/tasks/${taskId}/runs/${encodeURIComponent(runTag)}/memory/facts?${params}`)
}

export function getTaskSessionSummaries(
  taskId: number,
  runTag: string,
  limit: number,
  offset: number,
  search?: string | null,
): Promise<SessionSummariesPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  })
  if (search)
    params.set('search', search)
  return get<SessionSummariesPage>(`/tasks/${taskId}/runs/${encodeURIComponent(runTag)}/memory/summaries?${params}`)
}

export function listTaskRuns(taskId: number): Promise<RunTagInfo[]> {
  return get<RunTagInfo[]>(`/tasks/${taskId}/runs`)
}
