import { useEffect } from 'react'

export type MediaPreview = {
  url: string
  alt: string
  kind: 'image' | 'video'
}

export default function MediaModal({ media, onClose }: { media: MediaPreview | null; onClose: () => void }) {
  useEffect(() => {
    if (!media) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [media, onClose])

  if (!media) return null
  return (
    <div className="image-modal" role="dialog" aria-modal="true" aria-label={media.alt} onClick={onClose}>
      <button className="image-modal-close" onClick={onClose} aria-label="关闭预览">×</button>
      {media.kind === 'video' ? (
        <video src={media.url} controls autoPlay playsInline onClick={(event) => event.stopPropagation()} />
      ) : (
        <img src={media.url} alt={media.alt} onClick={(event) => event.stopPropagation()} />
      )}
      <span className="image-modal-hint">按 Esc、右上角 × 或点击黑色背景关闭</span>
    </div>
  )
}
