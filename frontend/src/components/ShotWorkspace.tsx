import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchShots,
  fetchTakes,
  selectTake,
  takeMediaUrl,
  uploadTake,
} from '../api/client'
import { useAppStore } from '../stores/app'

export default function ShotWorkspace() {
  const projectId = useAppStore((s) => s.projectId)!
  const shotId = useAppStore((s) => s.shotId)
  const queryClient = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const [prompt, setPrompt] = useState('')
  const [uploading, setUploading] = useState(false)

  const { data: shots } = useQuery({
    queryKey: ['shots', projectId],
    queryFn: () => fetchShots(projectId),
  })
  const shot = (shots ?? []).find((s) => s.id === shotId) ?? null

  const { data: takes, isLoading } = useQuery({
    queryKey: ['takes', projectId, shotId],
    queryFn: () => fetchTakes(projectId, shotId!),
    enabled: shotId !== null,
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

  if (!shot) {
    return (
      <div className="shot-workspace empty">
        <p className="muted">在左侧选择一个镜头，或新建镜头后开始。</p>
      </div>
    )
  }

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
      </div>

      <div className="take-pool">
        <div className="take-pool-heading">
          <span>Take Pool（{takes?.length ?? 0}）</span>
          <div className="take-upload">
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

        <div className="take-grid">
          {isLoading && <p className="muted">加载中…</p>}
          {takes?.length === 0 && (
            <p className="muted">还没有 Take，上传一段视频开始抽卡。</p>
          )}
          {takes?.map((take) => (
            <div
              key={take.id}
              className={`take-card ${shot.selected_take_id === take.id ? 'selected' : ''}`}
            >
              <video src={takeMediaUrl(take)} preload="metadata" muted />
              <div className="take-meta">
                <strong>{take.id}</strong>
                {take.duration != null && <span>{take.duration.toFixed(1)}s</span>}
                <span className="muted">{take.model}</span>
              </div>
              {take.prompt && <p className="take-prompt">{take.prompt}</p>}
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
