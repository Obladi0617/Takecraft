import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createScene, createShot, fetchScenes, fetchShots } from '../api/client'
import { useAppStore } from '../stores/app'

export default function ProjectNavigator() {
  const projectId = useAppStore((s) => s.projectId)!
  const shotId = useAppStore((s) => s.shotId)
  const setShot = useAppStore((s) => s.setShot)
  const queryClient = useQueryClient()
  const [sceneTitle, setSceneTitle] = useState('')
  const [shotTitle, setShotTitle] = useState('')

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

  const addShot = useMutation({
    mutationFn: (scene_id: string | null) =>
      createShot(projectId, { scene_id, title: shotTitle }),
    onSuccess: () => {
      setShotTitle('')
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
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
            {shotsByScene(scene.id).map((shot) => (
              <button
                key={shot.id}
                className={`shot-item ${shot.id === shotId ? 'active' : ''}`}
                onClick={() => setShot(shot.id)}
              >
                <span>{shot.title || shot.id}</span>
                <span className="shot-status">
                  {shot.selected_take_id
                    ? `已选 ${shot.selected_take_id}`
                    : '未选 Take'}
                </span>
              </button>
            ))}
            <button
              className="shot-item add"
              onClick={() => addShot.mutate(scene.id)}
              disabled={!shotTitle.trim() || addShot.isPending}
            >
              + 新建镜头（输入标题后点击）
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
          <input
            placeholder="新镜头标题"
            value={shotTitle}
            onChange={(e) => setShotTitle(e.target.value)}
          />
        </div>
      </div>
    </div>
  )
}
