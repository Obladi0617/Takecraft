export interface Project {
  id: string
  name: string
  idea: string | null
  mode: string
  status: string
  default_take_count: number
  created_at: string
  updated_at: string
}

export interface Scene {
  id: string
  project_id: string
  index: number
  title: string
  description: string
}

export interface Shot {
  id: string
  project_id: string
  scene_id: string | null
  index: number
  title: string
  description: string
  duration_target: number
  framing: string
  camera_motion: string
  character_ids: string[]
  location_id: string | null
  storyboard_id: string | null
  selected_take_id: string | null
  take_count: number
  status: string
  created_at: string
  updated_at: string
}

export interface Take {
  id: string
  project_id: string
  shot_id: string
  index: number
  generation_job_id: string | null
  prompt: string
  negative_prompt: string | null
  model: string
  seed: number | null
  original_path: string
  proxy_path: string | null
  duration: number | null
  created_at: string
}

export interface TimelineClip {
  id: string
  project_id: string
  index: number
  shot_id: string | null
  take_id: string | null
  asset_id: string | null
  timeline_start: number
  source_in: number
  source_out: number | null
  volume: number
  transition_in: string | null
  transition_out: string | null
  shot_title: string | null
  media_url: string | null
}

export interface Timeline {
  clips: TimelineClip[]
  duration: number
}

export interface PipelineJob {
  id: string
  job_type: string
  status: string
  payload: Record<string, unknown>
  result: Record<string, unknown> | null
  error: string | null
  created_at: string
  updated_at: string
}

export interface PipelineStageNote {
  stage: string
  note?: string
}

export interface PipelineResponse {
  job: PipelineJob | null
  stages: PipelineStageNote[]
}

export interface Storyboard {
  id: string
  project_id: string
  shot_id: string
  index: number
  prompt: string
  negative_prompt: string | null
  image_path: string
  width: number
  height: number
  model: string
  seed: number | null
  status: string
  created_at: string
  media_url: string
  is_selected: boolean
  is_locked: boolean
}

export interface GenerationJob {
  id: string
  project_id: string
  shot_id: string | null
  index: number
  job_type: string
  priority: string
  status: string
  retry_count: number
  payload: Record<string, unknown>
  result: Record<string, unknown>
  error: string | null
  created_at: string
  updated_at: string
}

export interface AssetReference {
  id: string
  project_id: string
  type: string
  path: string
  proxy_path: string | null
  source: string
  meta: Record<string, unknown>
  created_at: string
  view: string | null
  media_url: string
}

export type AssetStatus = 'DRAFT' | 'PENDING_CONFIRM' | 'LOCKED'

export interface Character {
  id: string
  project_id: string
  index: number
  name: string
  age_range: string | null
  gender: string | null
  role: string
  description: string
  appearance: string
  costume: string
  personality: string
  visual_anchors: string[]
  immutable_traits: string[]
  identity_model_id: string | null
  identity_model_type: string | null
  prompt_block: string
  prompt_block_hash: string
  seed: number | null
  source: string
  status: AssetStatus
  version: number
  locked_at: string | null
  created_at: string
  updated_at: string
  references: AssetReference[]
  views: string[]
}

export interface Location {
  id: string
  project_id: string
  index: number
  scene_id: string | null
  name: string
  description: string
  visual_style: string
  materials: string[]
  colors: string[]
  visual_cues: string[]
  immutable_elements: string[]
  lighting_rules: string[]
  time_of_day_default: string
  prompt_block: string
  prompt_block_hash: string
  seed: number | null
  source: string
  status: AssetStatus
  version: number
  locked_at: string | null
  created_at: string
  updated_at: string
  references: AssetReference[]
  views: string[]
}

export interface AssetBundle {
  characters: Character[]
  locations: Location[]
}
export interface HumanDecision {
  decision: 'APPROVE' | 'REVISE' | 'RETAKE'
  feedback: string
  shot_id?: string
}

export interface CharacterCard {
  name: string
  gender?: string | null
  age_range?: string | null
  role?: string
  description?: string
  appearance?: string
  costume?: string
  personality?: string
  visual_anchors?: string[]
  immutable_traits?: string[]
}

export interface LocationCard {
  name: string
  scene_title?: string
  description?: string
  visual_style?: string
  time_of_day_default?: string
  materials?: string[]
  colors?: string[]
  visual_cues?: string[]
  immutable_elements?: string[]
  lighting_rules?: string[]
}

export interface CharacterInfo {
  id: string
  name: string
  gender: string | null
  age_range: string | null
  role: string
  description: string
  appearance: string
  costume: string
  personality: string
  visual_anchors: string[]
  immutable_traits: string[]
  status: string
}

export interface LocationInfo {
  id: string
  name: string
  description: string
  visual_style: string
  time_of_day_default: string
  materials: string[]
  colors: string[]
  visual_cues: string[]
  immutable_elements: string[]
  lighting_rules: string[]
  status: string
}

export interface StoryboardInfo {
  id: string
  shot_id: string
  prompt: string
  image_path: string
  media_url: string
  status: string
  is_selected: boolean
  is_locked: boolean
  shot_title: string
  shot_description: string
  duration: number | null
  framing: string
  camera_motion: string
}

export interface HumanReviewState {
  stage: string
  screenplay_id: string | null
  screenplay: {
    logline?: string
    scenes?: Array<{
      title?: string
      description?: string
      shots?: Array<{
        title?: string
        description?: string
        framing?: string
        camera_motion?: string
        duration?: number
      }>
    }>
  } | null
  script_decision: HumanDecision | null
  character_cards: { characters: CharacterCard[] } | null
  location_cards: { locations: LocationCard[] } | null
  asset_decisions: Record<string, HumanDecision>
  characters: CharacterInfo[]
  locations: LocationInfo[]
  storyboard_candidates_id: string | null
  storyboard_decisions: Record<string, HumanDecision>
  storyboards: StoryboardInfo[]
  takes: Array<{
    id: string
    shot_id: string
    shot_title: string
    shot_description: string
    duration: number | null
    prompt: string
    model: string
    media_url: string
    is_latest: boolean
    is_selected: boolean
    decision: HumanDecision | null
  }>
  take_decisions: Record<string, HumanDecision>
}
