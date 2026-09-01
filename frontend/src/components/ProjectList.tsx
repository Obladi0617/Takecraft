import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { createProject, fetchProjects } from '../api/client'
import { useAppStore } from '../stores/app'

export default function ProjectList() {
  const setProject = useAppStore((s) => s.setProject)
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [idea, setIdea] = useState('')

  const { data: projects, isLoading } = useQuery({
    queryKey: ['projects'],
    queryFn: fetchProjects,
  })

  const create = useMutation({
    mutationFn: () => createProject({ name, idea: idea || undefined }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      setProject(project.id)
    },
  })

  return (
    <div className="project-list">
      <header className="project-list-header">
        <h1>AI Film Agent</h1>
        <p>本地优先的多智能体 AI 短片生产工作流系统</p>
      </header>

      <section className="project-create card">
        <h2>新建项目</h2>
        <input
          placeholder="项目名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <textarea
          placeholder="一句话创意（Idea）"
          value={idea}
          onChange={(e) => setIdea(e.target.value)}
          rows={2}
        />
        <button
          className="primary"
          disabled={!name.trim() || create.isPending}
          onClick={() => create.mutate()}
        >
          创建
        </button>
        {create.isError && (
          <p className="error">创建失败：{String(create.error)}</p>
        )}
      </section>

      <section className="project-items">
        <h2>我的项目</h2>
        {isLoading && <p className="muted">加载中…</p>}
        {projects?.length === 0 && (
          <p className="muted">还没有项目，从上面创建一个开始。</p>
        )}
        <div className="project-grid">
          {projects?.map((p) => (
            <button
              key={p.id}
              className="project-card"
              onClick={() => setProject(p.id)}
            >
              <strong>{p.name}</strong>
              <span className="muted">{p.idea ?? '（无创意描述）'}</span>
              <span className="muted">
                {p.mode} · {p.status}
              </span>
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}
