import { useQuery } from '@tanstack/react-query'
import { fetchProject } from '../api/client'
import { useAppStore } from '../stores/app'

export default function Toolbar() {
  const projectId = useAppStore((s) => s.projectId)
  const setProject = useAppStore((s) => s.setProject)

  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => fetchProject(projectId!),
    enabled: projectId !== null,
    refetchInterval: 1200,
  })

  return (
    <div className="toolbar">
      <button className="ghost" onClick={() => setProject(null)}>
        ← 项目列表
      </button>
      <span className="toolbar-title">{project?.name ?? '…'}</span>
      {project && (
        <span className="badge">
          {project.mode === 'AUTO' ? 'Auto 模式' : 'Director 模式'}
        </span>
      )}
      <span className="badge">{project?.status ?? ''}</span>
      <span className="spacer" />
      <span className="muted">AI Film Agent · 阶段 1（底层生产链）</span>
    </div>
  )
}
