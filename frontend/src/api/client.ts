import type {
  AssetBundle,
  AssetReference,
  Character,
  GenerationJob,
  HumanReviewState,
  Location,
  PipelineResponse,
  Project,
  Scene,
  Shot,
  Storyboard,
  Take,
  Timeline,
} from './types'

export type { AssetBundle, AssetReference, Character, Location } from './types'

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

export const createProject = (body: {
  name: string
  idea?: string
  mode?: string
  default_take_count?: number
}) =>
  api<Project>('/api/v1/projects', { method: 'POST', body: JSON.stringify(body) })

export const startOneSentence = (pid: string) =>
  api<{ job: { id: string; status: string }; message: string }>(
    `/api/v1/projects/${pid}/one-sentence`,
    { method: 'POST' },
  )

export const fetchPipeline = (pid: string) =>
  api<PipelineResponse>(`/api/v1/projects/${pid}/pipeline`)

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

// ---------- 角色 / 场景资产（规格书第 13、14 节）----------

export interface CharacterBody {
  name: string
  age_range?: string | null
  gender?: string | null
  role?: string
  description?: string
  appearance?: string
  costume?: string
  personality?: string
  visual_anchors?: string[]
  immutable_traits?: string[]
  source?: string
}

export interface LocationBody {
  name: string
  scene_id?: string | null
  description?: string
  visual_style?: string
  time_of_day_default?: string
  materials?: string[]
  colors?: string[]
  visual_cues?: string[]
  immutable_elements?: string[]
  lighting_rules?: string[]
  source?: string
}

export const fetchAssets = (pid: string) =>
  api<AssetBundle>(`/api/v1/projects/${pid}/assets`)

export const createCharacter = (pid: string, body: CharacterBody) =>
  api<Character>(`/api/v1/projects/${pid}/characters`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const patchCharacter = (
  pid: string,
  characterId: string,
  body: Partial<CharacterBody>,
) =>
  api<Character>(`/api/v1/projects/${pid}/characters/${characterId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })

export const deleteCharacter = (pid: string, characterId: string) =>
  api<void>(`/api/v1/projects/${pid}/characters/${characterId}`, { method: 'DELETE' })

export const generateCharacterRefs = (pid: string, characterId: string) =>
  api<{ job: GenerationJob }>(
    `/api/v1/projects/${pid}/characters/${characterId}/references/generate`,
    { method: 'POST' },
  )

export function uploadCharacterRef(
  pid: string,
  characterId: string,
  file: File,
  view: string,
): Promise<AssetReference> {
  const form = new FormData()
  form.append('file', file)
  form.append('view', view)
  return api(`/api/v1/projects/${pid}/characters/${characterId}/references`, {
    method: 'POST',
    body: form,
  })
}

export const lockCharacter = (pid: string, characterId: string) =>
  api<Character>(`/api/v1/projects/${pid}/characters/${characterId}/lock`, {
    method: 'POST',
  })

export const createLocation = (pid: string, body: LocationBody) =>
  api<Location>(`/api/v1/projects/${pid}/locations`, {
    method: 'POST',
    body: JSON.stringify(body),
  })

export const patchLocation = (
  pid: string,
  locationId: string,
  body: Partial<LocationBody>,
) =>
  api<Location>(`/api/v1/projects/${pid}/locations/${locationId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })

export const deleteLocation = (pid: string, locationId: string) =>
  api<void>(`/api/v1/projects/${pid}/locations/${locationId}`, { method: 'DELETE' })

export const generateLocationRefs = (pid: string, locationId: string) =>
  api<{ job: GenerationJob }>(
    `/api/v1/projects/${pid}/locations/${locationId}/references/generate`,
    { method: 'POST' },
  )

export function uploadLocationRef(
  pid: string,
  locationId: string,
  file: File,
  view: string,
): Promise<AssetReference> {
  const form = new FormData()
  form.append('file', file)
  form.append('view', view)
  return api(`/api/v1/projects/${pid}/locations/${locationId}/references`, {
    method: 'POST',
    body: form,
  })
}

export const lockLocation = (pid: string, locationId: string) =>
  api<Location>(`/api/v1/projects/${pid}/locations/${locationId}/lock`, {
    method: 'POST',
  })

export const relinkShots = (pid: string) =>
  api<{ links: Record<string, string[]> }>(
    `/api/v1/projects/${pid}/assets/link-shots`,
    { method: 'POST' },
  )
export const fetchHumanReview = (pid: string) =>
  api<HumanReviewState>(`/api/v1/projects/${pid}/human-review`)

export const submitScriptReview = (
  pid: string,
  decision: 'APPROVE' | 'REVISE',
  feedback = '',
) =>
  api<{ ok: boolean; review_id: string; decision: string }>(
    `/api/v1/projects/${pid}/script-review`,
    { method: 'POST', body: JSON.stringify({ decision, feedback }) },
  )

export const submitAssetReview = (
  pid: string,
  decision: 'APPROVE' | 'REVISE',
  feedback = '',
) =>
  api<{ ok: boolean; review_id: string; decision: string }>(
    `/api/v1/projects/${pid}/asset-review`,
    { method: 'POST', body: JSON.stringify({ decision, feedback }) },
  )

export const submitStoryboardReview = (
  pid: string,
  decision: 'APPROVE' | 'REVISE',
  feedback = '',
  targetShotIds: string[] = [],
) =>
  api<{ ok: boolean; review_id: string; decision: string }>(
    `/api/v1/projects/${pid}/storyboard-review`,
    { method: 'POST', body: JSON.stringify({ decision, feedback, target_shot_ids: targetShotIds }) },
  )

export const submitTakeReview = (
  pid: string,
  takeId: string,
  decision: 'APPROVE' | 'RETAKE',
  feedback = '',
) =>
  api<{
    ok: boolean
    review_id: string
    decision: string
    job?: GenerationJob
    revised_prompt?: string
  }>(`/api/v1/projects/${pid}/takes/${takeId}/human-review`, {
    method: 'POST',
    body: JSON.stringify({ decision, feedback }),
  })
