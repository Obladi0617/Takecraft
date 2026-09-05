import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { API_BASE, fetchPipeline } from '../api/client'
import { useAppStore } from '../stores/app'

const CANONICAL_STAGES: { key: string; label: string; agent: string }[] = [
  { key: 'SCRIPTING', label: '剧本创作', agent: 'Writer' },
  { key: 'SCRIPT_REVIEW', label: '剧本人工审核', agent: 'Human' },
  { key: 'ASSET_DESIGN', label: '资产设计', agent: 'Director / Producer' },
  { key: 'ASSET_REVIEW', label: '资产人工审核', agent: 'Human' },
  { key: 'STORYBOARDING', label: '分镜生成', agent: 'Prompt / Director' },
  { key: 'STORYBOARD_REVIEW', label: '分镜人工审核', agent: 'Human' },
  { key: 'VIDEO_GENERATION', label: '视频生成', agent: '生成队列' },
  { key: 'REVIEWING', label: '质量审核', agent: 'Reviewer' },
  { key: 'HUMAN_TAKE_REVIEW', label: 'Take 人工复审', agent: 'Human' },
  { key: 'TAKE_SELECTION', label: '选片', agent: 'Reviewer' },
  { key: 'PREVIEW', label: '自动剪辑', agent: 'Editor' },
  { key: 'RENDERING', label: '成片渲染', agent: 'FFmpeg' },
  { key: 'COMPLETE', label: '完成', agent: '—' },
]

const RETAKE_STAGES = new Set(['VIDEO_GENERATION', 'REVIEWING'])

export default function PipelinePanel() {
  const projectId = useAppStore((s) => s.projectId)!
  const queryClient = useQueryClient()
  const [showRender, setShowRender] = useState(false)

  const { data } = useQuery({
    queryKey: ['pipeline', projectId],
    queryFn: () => fetchPipeline(projectId),
    refetchInterval: (query) =>
      query.state.data?.job?.status === 'RUNNING' ? 1200 : false,
  })

  const job = data?.job ?? null
  const stages = data?.stages ?? []
  const status = job?.status

  const prevStatus = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (prevStatus.current === 'RUNNING' && status && status !== 'RUNNING') {
      for (const key of ['project', 'scenes', 'shots', 'timeline', 'jobs', 'projects']) {
        queryClient.invalidateQueries({ queryKey: [key] })
      }
    }
    prevStatus.current = status
  }, [status, queryClient])

  useEffect(() => {
    if (!showRender) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setShowRender(false)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [showRender])

  if (!job) return null

  const seen = new Map(stages.map((s) => [s.stage, s]))
  const lastStage = stages.length > 0 ? stages[stages.length - 1].stage : null
  const retakeCount = stages.filter((s) => s.stage === 'REVIEWING').length - 1
  const blockedReason =
    (job.result?.blocked_reason as string | undefined) || null
  const renderUrl = job.result?.render_url as string | undefined

  return (
    <div className={`pipeline-panel status-${(status ?? '').toLowerCase()}`}>
      <div className="navigator-heading">
        一句话全自动流程
        <span
          className={
            status === 'DONE'
              ? 'badge ok'
              : status === 'FAILED'
                ? 'badge err'
                : 'badge run'
          }
        >
          {status === 'RUNNING'
            ? '运行中'
            : status === 'DONE'
              ? '已完成'
              : status === 'FAILED'
                ? '失败'
                : (status ?? '')}
        </span>
      </div>

      <ol className="stage-list">
        {CANONICAL_STAGES.map((stage) => {
          const reached = seen.has(stage.key)
          const isCurrent =
            lastStage === stage.key && status === 'RUNNING'
          const cls = isCurrent
            ? 'current'
            : reached
              ? 'done'
              : 'pending'
          const note = seen.get(stage.key)?.note
          return (
            <li key={stage.key} className={`stage-item ${cls}`}>
              <span className="stage-label">
                {stage.label}
                <span className="stage-agent">{stage.agent}</span>
              </span>
              {RETAKE_STAGES.has(stage.key) && retakeCount > 0 && (
                <span className="badge retake">重拍 ×{retakeCount}</span>
              )}
              {note && <span className="stage-note">{note}</span>}
            </li>
          )
        })}
      </ol>

      {status === 'RUNNING' && (
        <p className="muted pipeline-hint">
          多 Agent 流水线执行中，可随时在左侧查看分镜与 Take 进展。
        </p>
      )}

      {renderUrl && (
        <button
          className="pipeline-render"
          onClick={() => setShowRender(true)}
        >
          在当前页面查看成片
        </button>
      )}

      {renderUrl && showRender && (
        <div className="render-modal" role="dialog" aria-modal="true" aria-label="最终成片" onClick={() => setShowRender(false)}>
          <div className="render-modal-content" onClick={(event) => event.stopPropagation()}>
            <div className="render-modal-head">
              <strong>最终成片</strong>
              <button onClick={() => setShowRender(false)} aria-label="关闭成片">×</button>
            </div>
            <video src={`${API_BASE}${renderUrl}`} controls autoPlay playsInline />
            <a href={`${API_BASE}${renderUrl}`} download>下载成片</a>
          </div>
        </div>
      )}

      {blockedReason && (
        <p className="muted pipeline-blocked">兜底说明：{blockedReason}</p>
      )}

      {job.error && <p className="error">{job.error}</p>}
    </div>
  )
}
