import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchTimeline } from '../api/client'
import { useAppStore } from '../stores/app'

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  'http://127.0.0.1:8765'

export default function PreviewPlayer() {
  const projectId = useAppStore((s) => s.projectId)
  const { data: timeline } = useQuery({
    queryKey: ['timeline', projectId],
    queryFn: () => fetchTimeline(projectId!),
    enabled: projectId !== null,
  })
  const clips = timeline?.clips ?? []
  const [clipIndex, setClipIndex] = useState(0)
  const videoRef = useRef<HTMLVideoElement>(null)

  useEffect(() => {
    setClipIndex(0)
  }, [projectId])

  const clip = clips[clipIndex]

  useEffect(() => {
    const video = videoRef.current
    if (!video || !clip?.media_url) return
    video.currentTime = clip.source_in
    video.play().catch(() => {})
  }, [clipIndex, clip?.id])

  function advanceIfDue(current: number, end: number) {
    if (current >= end - 0.1) {
      if (clipIndex + 1 < clips.length) {
        setClipIndex(clipIndex + 1)
      } else {
        videoRef.current?.pause()
      }
    }
  }

  function handleTimeUpdate() {
    const video = videoRef.current
    if (!video || !clip) return
    const end = clip.source_out ?? video.duration
    advanceIfDue(video.currentTime, end)
  }

  function handleEnded() {
    if (clipIndex + 1 < clips.length) {
      setClipIndex(clipIndex + 1)
    }
  }

  return (
    <div className="preview">
      <div className="preview-head">
        <span>实时预览（Proxy）</span>
        {timeline && (
          <span className="muted">
            片段 {clips.length > 0 ? clipIndex + 1 : 0}/{clips.length} · 总时长{' '}
            {timeline.duration.toFixed(1)}s
          </span>
        )}
      </div>
      <div className="preview-stage">
        {clip?.media_url ? (
          <video
            key={clip.id}
            ref={videoRef}
            src={`${API_BASE}${clip.media_url}`}
            onTimeUpdate={handleTimeUpdate}
            onEnded={handleEnded}
            controls
            muted
            playsInline
          />
        ) : (
          <p className="muted">
            {clips.length === 0
              ? '时间线为空：为镜头上传 Take、选用后点击「自动剪辑」。'
              : '该片段没有可用媒体。'}
          </p>
        )}
      </div>
    </div>
  )
}
