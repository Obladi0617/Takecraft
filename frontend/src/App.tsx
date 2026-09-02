import { useState } from 'react'
import { useAppStore } from './stores/app'
import ProjectList from './components/ProjectList'
import Toolbar from './components/Toolbar'
import ProjectNavigator from './components/ProjectNavigator'
import PreviewPlayer from './components/PreviewPlayer'
import ShotWorkspace from './components/ShotWorkspace'
import AssetPanel from './components/AssetPanel'
import TimelineBar from './components/TimelineBar'
import AgentActivity from './components/AgentActivity'

export default function App() {
  const projectId = useAppStore((s) => s.projectId)
  const [tab, setTab] = useState<'shot' | 'assets'>('shot')

  if (!projectId) return <ProjectList />

  return (
    <div className="app">
      <Toolbar />
      <div className="main">
        <ProjectNavigator />
        <div className="center">
          <div className="center-tabs">
            <button
              className={tab === 'shot' ? 'primary' : ''}
              onClick={() => setTab('shot')}
            >
              镜头工作台
            </button>
            <button
              className={tab === 'assets' ? 'primary' : ''}
              onClick={() => setTab('assets')}
            >
              角色 / 场景资产
            </button>
          </div>
          {tab === 'shot' ? (
            <>
              <PreviewPlayer />
              <ShotWorkspace />
            </>
          ) : (
            <AssetPanel />
          )}
        </div>
        <AgentActivity />
      </div>
      <TimelineBar />
    </div>
  )
}
