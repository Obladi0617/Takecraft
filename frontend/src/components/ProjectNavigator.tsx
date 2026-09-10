import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createScene, fetchScenes, fetchShots } from '../api/client'
import { useAppStore } from '../stores/app'

export default function ProjectNavigator() {
  const projectId = useAppStore((s) => s.projectId)!
  const shotId = useAppStore((s) => s.shotId)
  const setShot = useAppStore((s) => s.setShot)
  const queryClient = useQueryClient()
  const [sceneTitle, setSceneTitle] = useState('')

  const { data: scenes } = useQuery({
    queryKey: ['scenes', projectId],
    queryFn: () => fetchScenes(projectId),
  })
  const { data: shots } = useQuery({
    queryKey: ['shots', projectId],
    queryFn: () => fetchShots(projectId),
  })

  const addScene = useMutation({
    mutationFn: () => createScene(projectId, { title: sceneTitle }),
    onSuccess: () => {
      setSceneTitle('')
      queryClient.invalidateQueries({ queryKey: ['scenes', projectId] })
    },
  })

  const shotsByScene = (sceneId: string | null) =>
    (shots ?? []).filter((s) => s.scene_id === sceneId)

  return (
    <div className="navigator">
      <div className="navigator-section">
        <div className="navigator-heading">场景 / 镜头</div>
        {scenes?.length === 0 && (
          <p className="muted">还没有场景，先创建一个。</p>
        )}
        {scenes?.map((scene) => (
          <div key={scene.id} className="scene-group">
            <div className="scene-title">
              {String(scene.index).padStart(2, '0')} {scene.title}
            </div>
            <button
              className={`shot-item scene-only ${shotsByScene(scene.id).some((shot) => shot.id === shotId) ? 'active' : ''}`}
              onClick={() => {
                const firstShot = shotsByScene(scene.id)[0]
                if (firstShot) setShot(firstShot.id)
              }}
            >
              <span>{shotsByScene(scene.id).length} 个镜头 · 剧本控制时长</span>
              <span className="shot-status">
                {shotsByScene(scene.id).length > 0 && shotsByScene(scene.id).every((shot) => shot.selected_take_id)
                  ? '场景已选'
                  : '等待场景视频'}
              </span>
            </button>
          </div>
        ))}
        <div className="scene-create">
          <input
            placeholder="新场景标题"
            value={sceneTitle}
            onChange={(e) => setSceneTitle(e.target.value)}
          />
          <button
            disabled={!sceneTitle.trim() || addScene.isPending}
            onClick={() => addScene.mutate()}
          >
            新建场景
          </button>
        </div>
      </div>
    </div>
  )
}
