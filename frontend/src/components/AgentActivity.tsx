import { useQuery } from '@tanstack/react-query'
import { fetchGenerationJobs } from '../api/client'
import { useAppStore } from '../stores/app'
import PipelinePanel from './PipelinePanel'

const STATUS_LABEL: Record<string, string> = {
  PENDING: '排队中',
  RUNNING: '生成中',
  REVIEWING: '待审核',
  RETAKE: '重抽',
  DONE: '完成',
  FAILED: '失败',
  CANCELLED: '已取消',
}

const JOB_TYPE_LABEL: Record<string, string> = {
  STORYBOARD: '分镜',
  VIDEO: 'Take',
  CHARACTER: '角色',
  LOCATION: '场景',
  PIPELINE: '全流程',
}

export default function AgentActivity() {
  const projectId = useAppStore((s) => s.projectId)!

  const { data: jobs } = useQuery({
    queryKey: ['jobs', projectId],
    queryFn: () => fetchGenerationJobs(projectId),
    refetchInterval: 2000,
  })

  const active = (jobs ?? []).filter((j) =>
    ['PENDING', 'RUNNING', 'RETAKE'].includes(j.status),
  )

  return (
    <div className="agent-activity">
      <PipelinePanel />
      <div className="navigator-heading">
        Agent 动态
        {active.length > 0 && (
          <span className="badge ok">{active.length} 个任务进行中</span>
        )}
      </div>
      <div className="job-list">
        {(jobs ?? []).length === 0 && (
          <p className="muted">
            暂无生成任务。生成分镜或 Take 后在此查看队列状态。
          </p>
        )}
        {(jobs ?? []).slice(0, 20).map((job) => (
          <div key={job.id} className="job-row">
            <span className="job-type">
              {JOB_TYPE_LABEL[job.job_type] ?? job.job_type}
            </span>
            <span className="job-id">{job.id}</span>
            <span className={`job-status ${job.status.toLowerCase()}`}>
              {STATUS_LABEL[job.status] ?? job.status}
              {job.retry_count > 0 && ` · 重试${job.retry_count}`}
            </span>
            {job.result?.take_id != null && (
              <span className="muted">{String(job.result.take_id)}</span>
            )}
            {job.error && <span className="error job-error">{job.error}</span>}
          </div>
        ))}
      </div>
      <p className="muted agent-note">
        Producer / Writer / Director / Prompt / Reviewer / Editor 六个 Agent
        已由 LangGraph 主流程编排，可在「一句话生成」中全自动运行。
      </p>
    </div>
  )
}
