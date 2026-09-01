export default function AgentActivity() {
  return (
    <div className="agent-activity">
      <div className="navigator-heading">Agent 动态</div>
      <div className="agent-placeholder">
        <p className="muted">
          多 Agent 体系将在阶段 3 接入：
        </p>
        <ul className="muted">
          <li>Producer · 生产规划</li>
          <li>Writer · 剧本</li>
          <li>Director · 导演方案</li>
          <li>Prompt · 提示词工程</li>
          <li>Reviewer · AI Dailies 审核</li>
          <li>Editor · 自动剪辑</li>
        </ul>
        <p className="muted">
          当前为阶段 1：Shot → Take → 选片 → Timeline → Preview 底层生产链。
        </p>
      </div>
    </div>
  )
}
