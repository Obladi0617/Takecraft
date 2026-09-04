import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  API_BASE,
  fetchGenerationJobs,
  fetchHumanReview,
  fetchShots,
  fetchStoryboards,
  fetchTakes,
  generateStoryboards,
  generateTakes,
  lockStoryboard,
  selectStoryboard,
  selectTake,
  submitTakeReview,
  takeMediaUrl,
  uploadTake,
} from '../api/client'
import { useAppStore } from '../stores/app'

const ACTIVE = new Set(['PENDING', 'RUNNING', 'RETAKE'])

export default function ShotWorkspace() {
  const projectId = useAppStore((s) => s.projectId)!
  const shotId = useAppStore((s) => s.shotId)
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [prompt, setPrompt] = useState('')
  const [uploading, setUploading] = useState(false)
  const [sbPrompt, setSbPrompt] = useState('')
  const sbCount = 1
  const [forceAsk, setForceAsk] = useState(false)
  const [reviewFeedback, setReviewFeedback] = useState<Record<string, string>>({})

  const { data: shots } = useQuery({
    queryKey: ['shots', projectId],
    queryFn: () => fetchShots(projectId),
  })
  const shot = (shots ?? []).find((s) => s.id === shotId) ?? null

  const { data: jobs } = useQuery({
    queryKey: ['jobs', projectId],
    queryFn: () => fetchGenerationJobs(projectId),
    enabled: shotId !== null,
  })
  const shotJobsActive =
    shotId !== null &&
    (jobs ?? []).some((j) => j.shot_id === shotId && ACTIVE.has(j.status))

  const { data: takes, isLoading } = useQuery({
    queryKey: ['takes', projectId, shotId],
    queryFn: () => fetchTakes(projectId, shotId!),
    enabled: shotId !== null,
    refetchInterval: shotJobsActive ? 1500 : false,
  })

  const { data: storyboards } = useQuery({
    queryKey: ['storyboards', projectId, shotId],
    queryFn: () => fetchStoryboards(projectId, shotId!),
    enabled: shotId !== null,
    refetchInterval: shotJobsActive ? 1500 : false,
  })

  const { data: humanReview } = useQuery({
    queryKey: ['human-review', projectId],
    queryFn: () => fetchHumanReview(projectId),
    refetchInterval: 1200,
  })

  const humanTakeReview = useMutation({
    mutationFn: ({ takeId, decision }: { takeId: string; decision: 'APPROVE' | 'RETAKE' }) =>
      submitTakeReview(projectId, takeId, decision, reviewFeedback[takeId] ?? ''),
    onSuccess: (_, variables) => {
      setReviewFeedback((current) => ({ ...current, [variables.takeId]: '' }))
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
      queryClient.invalidateQueries({ queryKey: ['takes', projectId, shotId] })
    },
  })
  const upload = useMutation({
    mutationFn: (file: File) => uploadTake(projectId, shotId!, file, prompt),
    onSuccess: () => {
      setPrompt('')
      if (fileRef.current) fileRef.current.value = ''
      queryClient.invalidateQueries({ queryKey: ['takes', projectId, shotId] })
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
    },
  })

  const select = useMutation({
    mutationFn: (takeId: string) => selectTake(projectId, shotId!, takeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
      queryClient.invalidateQueries({ queryKey: ['takes', projectId, shotId] })
      queryClient.invalidateQueries({ queryKey: ['timeline', projectId] })
    },
  })

  const genStoryboard = useMutation({
    mutationFn: () =>
      generateStoryboards(projectId, shotId!, {
        prompt: sbPrompt || undefined,
        count: sbCount,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
  })

  const selectSb = useMutation({
    mutationFn: (sbId: string) => selectStoryboard(projectId, sbId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['storyboards', projectId, shotId] })
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
    },
  })

  const lockSb = useMutation({
    mutationFn: (sbId: string) => lockStoryboard(projectId, sbId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['storyboards', projectId, shotId] })
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
    },
  })

  const genTakes = useMutation({
    mutationFn: (force: boolean) =>
      generateTakes(projectId, shotId!, { force }),
    onSuccess: () => {
      setForceAsk(false)
      queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
    onError: (err) => {
      if (String(err).includes('storyboard_not_locked')) {
        setForceAsk(true)
      }
    },
  })

  if (!shot) {
    return (
      <div className="shot-workspace empty">
        <p className="muted">在左侧选择一个镜头，或新建镜头后开始。</p>
      </div>
    )
  }

  const lockedSb = (storyboards ?? []).find((sb) => sb.is_locked)

  return (
    <div className="shot-workspace">
      <div className="shot-header">
        <h3>
          {shot.id.toUpperCase()} {shot.title}
        </h3>
        <span className="muted">
          目标时长 {shot.duration_target}s · 景别 {shot.framing} · 运镜{' '}
          {shot.camera_motion}
        </span>
        <span className={`badge ${shot.selected_take_id ? 'ok' : ''}`}>
          {shot.selected_take_id
            ? `已选 ${shot.selected_take_id}`
            : '未选 Take'}
        </span>
        <span className={`badge ${lockedSb ? 'ok' : ''}`}>
          {lockedSb ? `分镜已锁定 ${lockedSb.id}` : '分镜未锁定'}
        </span>
      </div>

      <div className="storyboard-panel">
        <div className="take-pool-heading">
          <span>Storyboard 分镜候选（{storyboards?.length ?? 0}）</span>
          <div className="storyboard-controls">
            <input
              className="sb-prompt"
              placeholder="分镜 Prompt（默认用镜头描述）"
              value={sbPrompt}
              onChange={(e) => setSbPrompt(e.target.value)}
            />
            <span className="badge">每镜 1 张</span>
            <button
              className="primary"
              onClick={() => genStoryboard.mutate()}
              disabled={genStoryboard.isPending}
            >
              {genStoryboard.isPending ? '生成中…' : '生成候选'}
            </button>
          </div>
        </div>
        {genStoryboard.isError && (
          <p className="error">生成失败：{String(genStoryboard.error)}</p>
        )}
        <div className="storyboard-grid">
          {storyboards?.length === 0 && (
            <p className="muted">
              尚无分镜——生成候选后选定并锁定，即可批量生成 Take。
            </p>
          )}
          {storyboards?.map((sb) => (
            <div
              key={sb.id}
              className={`storyboard-card ${sb.is_selected ? 'selected' : ''}`}
            >
              <a href={`${API_BASE}${sb.media_url}`} target="_blank" rel="noreferrer">
                <img src={`${API_BASE}${sb.media_url}`} alt={sb.id} title="点击查看原图" />
              </a>
              <div className="storyboard-meta">
                <strong>{sb.id}</strong>
                <span className="muted">seed {sb.seed ?? '-'}</span>
                {sb.is_locked && <span className="badge ok">已锁定</span>}
              </div>
              <div className="storyboard-actions">
                <button
                  className={sb.is_selected ? 'primary' : ''}
                  onClick={() => selectSb.mutate(sb.id)}
                  disabled={sb.is_locked || selectSb.isPending}
                >
                  {sb.is_selected ? '当前选定' : '选定'}
                </button>
                <button
                  onClick={() => lockSb.mutate(sb.id)}
                  disabled={!sb.is_selected || sb.is_locked || lockSb.isPending}
                >
                  锁定
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="take-pool">
        <div className="take-pool-heading">
          <span>Take Pool（{takes?.length ?? 0}）</span>
          <div className="take-upload">
            <button
              className="primary"
              onClick={() => genTakes.mutate(false)}
              disabled={genTakes.isPending || shotJobsActive}
              title={`按 Shot 设置抽 ${shot.take_count} 张`}
            >
              {shotJobsActive
                ? '生成中…'
                : `生成 Take ×${shot.take_count}`}
            </button>
            <input
              placeholder="Prompt（可选）"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
            <input
              ref={fileRef}
              type="file"
              accept="video/*"
              disabled={uploading}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) {
                  setUploading(true)
                  upload.mutate(file, {
                    onSettled: () => setUploading(false),
                  })
                }
              }}
            />
            {upload.isPending && <span className="muted">上传中…</span>}
            {upload.isError && (
              <span className="error">上传失败：{String(upload.error)}</span>
            )}
          </div>
        </div>

        {forceAsk && (
          <div className="force-ask">
            <span>分镜未锁定，直接生成 Take 可能导致镜头不一致。</span>
            <button
              className="primary"
              onClick={() => genTakes.mutate(true)}
              disabled={genTakes.isPending}
            >
              仍然生成
            </button>
            <button onClick={() => setForceAsk(false)}>取消</button>
          </div>
        )}
        {genTakes.isError && !forceAsk && (
          <p className="error">生成失败：{String(genTakes.error)}</p>
        )}

        <div className="take-grid">
          {isLoading && <p className="muted">加载中…</p>}
          {takes?.length === 0 && (
            <p className="muted">还没有 Take——锁定分镜后批量生成，或上传素材。</p>
          )}
          {takes?.map((take) => (
            <div
              key={take.id}
              className={`take-card ${shot.selected_take_id === take.id ? 'selected' : ''}`}
            >
              <video src={takeMediaUrl(take)} preload="metadata" controls />
              <div className="take-meta">
                <strong>{take.id}</strong>
                {take.duration != null && <span>{take.duration.toFixed(1)}s</span>}
                <span className="muted">{take.model}</span>
              </div>
              {take.prompt && <p className="take-prompt">{take.prompt}</p>}
              {humanReview && (
                <div className="take-human-review">
                  <textarea
                    placeholder="不合格时填写：哪里不好、希望怎样调整"
                    value={reviewFeedback[take.id] ?? ''}
                    onChange={(event) =>
                      setReviewFeedback((current) => ({
                        ...current,
                        [take.id]: event.target.value,
                      }))
                    }
                  />
                  <div className="human-review-actions">
                    <button
                      className={humanReview.take_decisions[take.id]?.decision === 'APPROVE' ? 'primary' : ''}
                      disabled={humanTakeReview.isPending}
                      onClick={() => humanTakeReview.mutate({ takeId: take.id, decision: 'APPROVE' })}
                    >
                      人工通过
                    </button>
                    <button
                      disabled={humanTakeReview.isPending || !(reviewFeedback[take.id] ?? '').trim()}
                      onClick={() => humanTakeReview.mutate({ takeId: take.id, decision: 'RETAKE' })}
                    >
                      按意见打回重拍
                    </button>
                  </div>
                  {humanReview.take_decisions[take.id]?.feedback && (
                    <p className="muted">上次意见：{humanReview.take_decisions[take.id].feedback}</p>
                  )}
                </div>
              )}
              <button
                className={shot.selected_take_id === take.id ? 'primary' : ''}
                onClick={() => select.mutate(take.id)}
                disabled={select.isPending}
              >
                {shot.selected_take_id === take.id ? '当前选用' : '选用'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
