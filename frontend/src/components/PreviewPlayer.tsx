import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchTimeline } from '../api/client'
import { useAppStore } from '../stores/app'

const API_BASE =
  (import.meta.env.VITE_API_BASE as string | undefined) ??
  'http://127.0.0.1:8765'

function clipDuration(c: { source_in: number; source_out: number | null }) {
  return Math.max((c.source_out ?? 0) - c.source_in, 0)
}

export default function PreviewPlayer() {
  const projectId = useAppStore((s) => s.projectId)
  const { data: timeline } = useQuery({
    queryKey: ['timeline', projectId],
    queryFn: () => fetchTimeline(projectId!),
    enabled: projectId !== null,
  })
  const clips = timeline?.clips ?? []
  const total = timeline?.duration ?? 0

  const [clipIndex, setClipIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [globalTime, setGlobalTime] = useState(0)
  const videoRef = useRef<HTMLVideoElement>(null)
  const wantPlay = useRef(false)
  const pendingSeek = useRef<number | null>(null)

  useEffect(() => {
    setClipIndex(0)
    setGlobalTime(0)
    setPlaying(false)
    wantPlay.current = false
    pendingSeek.current = null
  }, [projectId])

  const clip = clips[clipIndex]

  // 新片段元数据就绪后：定位到 source_in（或跨片段 seek 的偏移），按需续播。
  // 必须挂在 loadedmetadata 事件上——src 切换瞬间 readyState 仍是旧片段的，
  // 过早设置 currentTime 会被新源加载重置。
  function handleLoadedMetadata() {
    const video = videoRef.current
    if (!video || !clip) return
    video.currentTime = pendingSeek.current ?? clip.source_in
    pendingSeek.current = null
    if (wantPlay.current) video.play().catch(() => {})
  }

  function advance() {
    if (clipIndex + 1 < clips.length) {
      setClipIndex(clipIndex + 1)
      return
    }
    wantPlay.current = false
    setPlaying(false)
    setGlobalTime(total)
    videoRef.current?.pause()
  }

  function handleTimeUpdate() {
    const video = videoRef.current
    if (!video || !clip) return
    setGlobalTime(
      clip.timeline_start + Math.max(video.currentTime - clip.source_in, 0),
    )
    const end = clip.source_out ?? video.duration
    if (end && video.currentTime >= end - 0.05) advance()
  }

  function togglePlay() {
    const video = videoRef.current
    if (!video) return
    if (playing) {
      wantPlay.current = false
      setPlaying(false)
      video.pause()
      return
    }
    if (clipIndex >= clips.length - 1 && globalTime >= total - 0.1) {
      setClipIndex(0)
      setGlobalTime(0)
    }
    wantPlay.current = true
    setPlaying(true)
    video.play().catch(() => {})
  }

  function seekTo(target: number) {
    if (clips.length === 0) return
    const t = Math.min(Math.max(target, 0), Math.max(total - 0.05, 0))
    let idx = clips.findIndex((c) => t < c.timeline_start + clipDuration(c))
    if (idx < 0) idx = clips.length - 1
    const c = clips[idx]
    const offset = Math.min(
      Math.max(t - c.timeline_start, 0),
      Math.max(clipDuration(c) - 0.05, 0),
    )
    setGlobalTime(t)
    if (idx === clipIndex) {
      const video = videoRef.current
      if (video) video.currentTime = c.source_in + offset
    } else {
      pendingSeek.current = c.source_in + offset
      setClipIndex(idx)
    }
  }

  function handleBarClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect()
    seekTo(((e.clientX - rect.left) / rect.width) * total)
  }

  const progress = total > 0 ? Math.min(globalTime / total, 1) : 0

  return (
    <div className="preview">
      <div className="preview-head">
        <span>实时预览（Proxy）</span>
        {timeline && (
          <span className="muted">
            片段 {clips.length > 0 ? clipIndex + 1 : 0}/{clips.length} ·{' '}
            {globalTime.toFixed(1)}s / {total.toFixed(1)}s
          </span>
        )}
      </div>
      <div className="preview-stage">
        {clip?.media_url ? (
          <video
            ref={videoRef}
            src={`${API_BASE}${clip.media_url}`}
            onLoadedMetadata={handleLoadedMetadata}
            onTimeUpdate={handleTimeUpdate}
            onEnded={advance}
            onPlay={() => setPlaying(true)}
            onPause={() => {
              if (!wantPlay.current) setPlaying(false)
            }}
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
      {clips.length > 0 && (
        <div className="preview-transport">
          <button className="ghost" onClick={togglePlay}>
            {playing ? '暂停' : '播放'}
          </button>
          <div className="preview-bar" onClick={handleBarClick}>
            <div className="preview-bar-fill" style={{ width: `${progress * 100}%` }} />
            {clips.map((c) => (
              <span
                key={c.id}
                className="preview-bar-tick"
                style={{ left: `${(c.timeline_start / total) * 100}%` }}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
