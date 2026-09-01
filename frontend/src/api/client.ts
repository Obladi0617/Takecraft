import type {
  GenerationJob,
  Project,
  Scene,
  Shot,
  Storyboard,
  Take,
  Timeline,
} from './types'

export const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  'http://127.0.0.1:8765'

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> =
    init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`HTTP ${res.status}: ${text}`)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export function mediaUrl(projectId: string, relPath: string): string {
  return `${API_BASE}/media/${projectId}/${relPath}`
}

export function takeMediaUrl(take: Take): string {
  return mediaUrl(take.project_id, take.proxy_path ?? take.original_path)
}

export const fetchProjects = () => api<Project[]>('/api/v1/projects')

export const fetchProject = (pid: string) =>
  api<Project>(`/api/v1/projects/${pid}`)

export const createProject = (body: { name: string; idea?: string }) =>
  api<Project>('/api/v1/projects', { method: 'POST', body: JSON.stringify(body) })

export const deleteProject = (pid: string) =>
  api<void>(`/api/v1/projects/${pid}`, { method: 'DELETE' })

export const fetchScenes = (pid: string) =>
  api<Scene[]>(`/api/v1/projects/${pid}/scenes`)

export const createScene = (
  pid: string,
  body: { title: string; description?: string },
) =>
  api<Scene>(`/api/v1/projects/${pid}/scenes`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const fetchShots = (pid: string) =>
  api<Shot[]>(`/api/v1/projects/${pid}/shots`)

export const createShot = (
  pid: string,
  body: {
    scene_id?: string | null
    title?: string
    description?: string
    duration_target?: number
  },
) =>
  api<Shot>(`/api/v1/projects/${pid}/shots`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const fetchTakes = (pid: string, shotId: string) =>
  api<Take[]>(`/api/v1/projects/${pid}/shots/${shotId}/takes`)

export function uploadTake(
  pid: string,
  shotId: string,
  file: File,
  prompt: string,
): Promise<Take> {
  const form = new FormData()
  form.append('file', file)
  form.append('prompt', prompt)
  return api(`/api/v1/projects/${pid}/shots/${shotId}/takes`, {
    method: 'POST',
    body: form,
  })
}

export const selectTake = (pid: string, shotId: string, takeId: string) =>
  api<Shot>(`/api/v1/projects/${pid}/shots/${shotId}/takes/${takeId}/select`, {
    method: 'POST',
  })

export const fetchTimeline = (pid: string) =>
  api<Timeline>(`/api/v1/projects/${pid}/timeline`)

export const autoEdit = (pid: string) =>
  api<Timeline>(`/api/v1/projects/${pid}/timeline/auto-edit`, {
    method: 'POST',
  })

export interface RenderResult {
  id: string
  path: string
  duration: number
  url: string
}

export const renderProject = (pid: string) =>
  api<RenderResult>(`/api/v1/projects/${pid}/render`, { method: 'POST' })

export const fetchStoryboards = (pid: string, shotId: string) =>
  api<Storyboard[]>(`/api/v1/projects/${pid}/shots/${shotId}/storyboards`)

export const generateStoryboards = (
  pid: string,
  shotId: string,
  body: { prompt?: string; count?: number },
) =>
  api<{ job: GenerationJob }>(
    `/api/v1/projects/${pid}/shots/${shotId}/storyboards/generate`,
    { method: 'POST', body: JSON.stringify(body) },
  )

export const selectStoryboard = (pid: string, sbId: string) =>
  api<{ ok: boolean }>(`/api/v1/projects/${pid}/storyboards/${sbId}/select`, {
    method: 'POST',
  })

export const lockStoryboard = (pid: string, sbId: string) =>
  api<{ ok: boolean }>(`/api/v1/projects/${pid}/storyboards/${sbId}/lock`, {
    method: 'POST',
  })

export const generateTakes = (
  pid: string,
  shotId: string,
  body: { count?: number; prompt?: string; force?: boolean },
) =>
  api<{ count: number; jobs: GenerationJob[] }>(
    `/api/v1/projects/${pid}/shots/${shotId}/takes/generate`,
    { method: 'POST', body: JSON.stringify(body) },
  )

export const fetchGenerationJobs = (pid: string, shotId?: string) =>
  api<GenerationJob[]>(
    `/api/v1/projects/${pid}/generation-jobs${shotId ? `?shot_id=${shotId}` : ''}`,
  )
