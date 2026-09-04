import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAppStore } from './stores/app'
import { fetchHumanReview } from './api/client'
import ProjectList from './components/ProjectList'
import Toolbar from './components/Toolbar'
import ProjectNavigator from './components/ProjectNavigator'
import PreviewPlayer from './components/PreviewPlayer'
import ShotWorkspace from './components/ShotWorkspace'
import AssetPanel from './components/AssetPanel'
import TimelineBar from './components/TimelineBar'
import AgentActivity from './components/AgentActivity'
import ReviewPanel from './components/ReviewPanel'

export default function App() {
  const projectId = useAppStore((s) => s.projectId)
  const [tab, setTab] = useState<'shot' | 'assets' | 'review'>('shot')
  const previousStage = useRef<string | undefined>(undefined)

  const { data: humanReview } = useQuery({
    queryKey: ['human-review', projectId],
    queryFn: () => fetchHumanReview(projectId!),
    enabled: !!projectId,
    refetchInterval: 1200,
  })

  const needsReview = humanReview && ['SCRIPT_REVIEW', 'ASSET_REVIEW', 'STORYBOARD_REVIEW', 'HUMAN_TAKE_REVIEW'].includes(humanReview.stage)
  useEffect(() => {
    const stage = humanReview?.stage
    if (stage !== previousStage.current) {
      if (needsReview) setTab('review')
      else setTab((current) => current === 'review' ? 'shot' : current)
      previousStage.current = stage
    }
  }, [humanReview?.stage, needsReview])

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
            {needsReview && (
              <button
                className={tab === 'review' ? 'primary' : ''}
                onClick={() => setTab('review')}
              >
                {humanReview.stage === 'SCRIPT_REVIEW' ? '剧本审核' :
                 humanReview.stage === 'ASSET_REVIEW' ? '资产审核' :
                 humanReview.stage === 'STORYBOARD_REVIEW' ? '分镜审核' : '人工审核'}
              </button>
            )}
          </div>
          {tab === 'shot' ? (
            <>
              <PreviewPlayer />
              <ShotWorkspace />
            </>
          ) : tab === 'assets' ? (
            <AssetPanel />
          ) : (
            <ReviewPanel />
          )}
        </div>
        <AgentActivity />
      </div>
      <TimelineBar />
    </div>
  )
}
