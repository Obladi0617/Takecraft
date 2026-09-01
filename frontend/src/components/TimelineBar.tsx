import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { API_BASE, autoEdit, fetchTimeline, renderProject } from '../api/client'
import { useAppStore } from '../stores/app'

export default function TimelineBar() {
  const projectId = useAppStore((s) => s.projectId)!
  const queryClient = useQueryClient()
  const [renderUrl, setRenderUrl] = useState<string | null>(null)

  const { data: timeline } = useQuery({
    queryKey: ['timeline', projectId],
    queryFn: () => fetchTimeline(projectId),
  })

  const auto = useMutation({
    mutationFn: () => autoEdit(projectId),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['timeline', projectId] }),
  })

  const render = useMutation({
    mutationFn: () => renderProject(projectId),
    onSuccess: (result) => setRenderUrl(`${API_BASE}${result.url}`),
  })

  const clips = timeline?.clips ?? []
  const total = timeline?.duration ?? 0

  return (
    <div className="timeline-bar">
      <div className="timeline-toolbar">
        <span>Timeline</span>
        <span className="muted">
          {clips.length} 个片段 · 总时长 {total.toFixed(1)}s
        </span>
        <span className="spacer" />
        <button
          className="primary"
          onClick={() => auto.mutate()}
          disabled={auto.isPending}
        >
          {auto.isPending ? '剪辑中…' : '自动剪辑'}
        </button>
        <button
          className="primary"
          onClick={() => render.mutate()}
          disabled={render.isPending || clips.length === 0}
          title={clips.length === 0 ? '时间线为空' : undefined}
        >
          {render.isPending ? '渲染中…' : '渲染成片'}
        </button>
      </div>
      {render.isError && (
        <div className="render-error">渲染失败: {String(render.error)}</div>
      )}
      {renderUrl && (
        <video className="render-preview" src={renderUrl} controls autoPlay muted />
      )}
      <div className="timeline-strip">
        {clips.length === 0 && (
          <span className="muted">
            空时间线——选用各镜头的 Take 后点击「自动剪辑」生成。
          </span>
        )}
        {clips.map((clip, i) => {
          const width = total > 0 ? (clipDuration(clip) / total) * 100 : 0
          return (
            <div
              key={clip.id}
              className="timeline-clip"
              style={{ width: `${Math.max(width, 6)}%` }}
              title={`${clip.shot_title ?? clip.shot_id} · ${clip.take_id}`}
            >
              <span className="clip-index">{i + 1}</span>
              <span className="clip-title">
                {clip.shot_title ?? clip.shot_id ?? '片段'}
              </span>
              <span className="clip-take">{clip.take_id}</span>
              <span className="clip-duration">
                {clipDuration(clip).toFixed(1)}s
              </span>
              {clip.transition_out && (
                <span className="clip-transition">{clip.transition_out}</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function clipDuration(clip: {
  source_in: number
  source_out: number | null
}): number {
  return Math.max((clip.source_out ?? 0) - clip.source_in, 0)
}
