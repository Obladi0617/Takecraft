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
