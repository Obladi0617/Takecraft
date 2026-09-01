import { useAppStore } from './stores/app'
import ProjectList from './components/ProjectList'
import Toolbar from './components/Toolbar'
import ProjectNavigator from './components/ProjectNavigator'
import PreviewPlayer from './components/PreviewPlayer'
import ShotWorkspace from './components/ShotWorkspace'
import TimelineBar from './components/TimelineBar'
import AgentActivity from './components/AgentActivity'

export default function App() {
  const projectId = useAppStore((s) => s.projectId)

  if (!projectId) return <ProjectList />

  return (
    <div className="app">
      <Toolbar />
      <div className="main">
        <ProjectNavigator />
        <div className="center">
          <PreviewPlayer />
          <ShotWorkspace />
        </div>
        <AgentActivity />
      </div>
      <TimelineBar />
    </div>
  )
}
