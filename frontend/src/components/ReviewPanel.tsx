import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  API_BASE,
  fetchAssets,
  fetchHumanReview,
  submitScriptReview,
  submitAssetReview,
  submitStoryboardReview,
  submitSceneReview,
  selectStoryboard,
} from '../api/client'
import { useAppStore } from '../stores/app'

const LOCATION_REVIEW_VIEWS = new Set(['ESTABLISHING', 'DETAIL'])
const LOCATION_VIEW_LABEL: Record<string, string> = {
  ESTABLISHING: '主角度',
  DETAIL: '细节',
}

export default function ReviewPanel() {
  const projectId = useAppStore((s) => s.projectId)!
  const queryClient = useQueryClient()
  const [scriptFeedback, setScriptFeedback] = useState('')
  const [scriptNotice, setScriptNotice] = useState('')
  const [assetFeedback, setAssetFeedback] = useState('')
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([])
  const [assetNotice, setAssetNotice] = useState('')
  const [storyboardFeedback, setStoryboardFeedback] = useState('')
  const [shotFeedback, setShotFeedback] = useState<Record<string, string>>({})
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null)
  const [expandedShots, setExpandedShots] = useState<string[]>([])
  const [takeFeedback, setTakeFeedback] = useState<Record<string, string>>({})
  const [selectedTakeIds, setSelectedTakeIds] = useState<string[]>([])
  const [submittingTakeIds, setSubmittingTakeIds] = useState<string[]>([])
  const [takeNotice, setTakeNotice] = useState('')
  const [takeReviewError, setTakeReviewError] = useState('')
  const [approvedCharIds, setApprovedCharIds] = useState<Set<string>>(new Set())
  const [approvedLocIds, setApprovedLocIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!previewImage) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPreviewImage(null)
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [previewImage])

  const imageModal = previewImage && (
    <div className="image-modal" role="dialog" aria-modal="true" aria-label={previewImage.alt} onClick={() => setPreviewImage(null)}>
      <button className="image-modal-close" onClick={() => setPreviewImage(null)} aria-label="关闭大图">×</button>
      <img src={previewImage.url} alt={previewImage.alt} onClick={(event) => event.stopPropagation()} />
      <span className="image-modal-hint">按 Esc 或点击黑色背景关闭</span>
    </div>
  )

  const { data: humanReview } = useQuery({
    queryKey: ['human-review', projectId],
    queryFn: () => fetchHumanReview(projectId),
    refetchInterval: 1200,
  })

  const { data: assetBundle } = useQuery({
    queryKey: ['assets', projectId],
    queryFn: () => fetchAssets(projectId),
    enabled: humanReview?.stage === 'ASSET_REVIEW',
    refetchInterval: humanReview?.stage === 'ASSET_REVIEW' ? 1500 : false,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
  })

  const scriptReview = useMutation({
    mutationFn: (decision: 'APPROVE' | 'REVISE') =>
      submitScriptReview(projectId, decision, scriptFeedback),
    onMutate: (decision) => {
      setScriptNotice(
        decision === 'APPROVE'
          ? '已提交，等待系统确认并进入下一阶段…'
          : '修改意见已提交，等待新剧本返回中…',
      )
    },
    onSuccess: (_, decision) => {
      setScriptNotice(
        decision === 'APPROVE'
          ? '审核指令已接收，正在进入下一阶段…'
          : '修改指令已接收，正在重新生成剧本，请等待返回…',
      )
      setScriptFeedback('')
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline', projectId] })
      queryClient.invalidateQueries({ queryKey: ['assets', projectId] })
      queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
    onError: () => {
      setScriptNotice('')
    },
  })

  const assetReview = useMutation({
    mutationFn: (decision: 'APPROVE' | 'REVISE') =>
      submitAssetReview(projectId, decision, assetFeedback, decision === 'REVISE' ? selectedAssetIds : []),
    onSuccess: (_, decision) => {
      if (decision === 'REVISE') {
        setAssetNotice('指令已接收，所选图片正在独立重新生成。你可以继续勾选其他图片并提交新的修改指令。')
      }
      setAssetFeedback('')
      setSelectedAssetIds([])
      setApprovedCharIds(new Set())
      setApprovedLocIds(new Set())
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline', projectId] })
      queryClient.invalidateQueries({ queryKey: ['assets', projectId] })
      queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
    },
  })

  const storyboardReview = useMutation({
    mutationFn: ({ decision, feedback = storyboardFeedback, shotIds = [] }: {
      decision: 'APPROVE' | 'REVISE'; feedback?: string; shotIds?: string[]
    }) => submitStoryboardReview(projectId, decision, feedback, shotIds),
    onSuccess: () => {
      setStoryboardFeedback('')
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline', projectId] })
    },
  })

  const chooseStoryboard = useMutation({
    mutationFn: (storyboardId: string) => selectStoryboard(projectId, storyboardId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
    },
  })

  const refreshTakeReview = () => {
    queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
    queryClient.invalidateQueries({ queryKey: ['pipeline', projectId] })
    queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
  }

  const submitOneScene = async (sceneId: string, decision: 'APPROVE' | 'RETAKE') => {
    setSubmittingTakeIds((ids) => ids.includes(sceneId) ? ids : [...ids, sceneId])
    setTakeReviewError('')
    try {
      const result = await submitSceneReview(projectId, sceneId, decision, takeFeedback[sceneId] ?? '')
      setTakeFeedback((current) => ({ ...current, [sceneId]: '' }))
      setSelectedTakeIds((ids) => ids.filter((id) => id !== sceneId))
      setTakeNotice(decision === 'RETAKE' ? '整场修改指令已提交，DGX 正按剧本总时长重新生成。' : result.auto_edited ? '全部场景已通过，已自动剪辑到时间线。' : '该场景已通过，全部通过后将自动剪辑。')
      queryClient.invalidateQueries({ queryKey: ['timeline', projectId] })
      refreshTakeReview()
    } catch (error) {
      setTakeReviewError(`提交失败：${String(error)}`)
    } finally {
      setSubmittingTakeIds((ids) => ids.filter((id) => id !== sceneId))
    }
  }

  if (!humanReview) {
    return (
      <div className="review-panel empty">
        <p className="muted">等待流水线启动...</p>
      </div>
    )
  }

  const stage = humanReview.stage

  // 剧本审核
  if (stage === 'SCRIPT_REVIEW' && humanReview.screenplay) {
    return (
      <div className="review-panel">
        <h3>剧本审核</h3>
        {scriptNotice && (
          <div className="review-command-notice" role="status" aria-live="polite">
            <span>{scriptReview.isPending ? '⏳' : '✓'} {scriptNotice}</span>
            {!scriptReview.isPending && (
              <button aria-label="关闭提示" onClick={() => setScriptNotice('')}>×</button>
            )}
          </div>
        )}
        <div className="review-content">
          <div className="screenplay-preview">
            <h4>剧本内容</h4>
            <p className="logline"><strong>一句话：</strong>{humanReview.screenplay.logline}</p>
            <div className="scenes-list">
              {(humanReview.screenplay.scenes ?? []).map((scene, sceneIndex) => (
                <div key={`scene-${sceneIndex}`} className="scene-block">
                  <h5>场景 {sceneIndex + 1}: {scene.title}</h5>
                  <p className="muted">{scene.description}</p>
                  <div className="shots-list">
                    {(scene.shots ?? []).map((shot, shotIndex) => (
                      <div key={`shot-${shotIndex}`} className="shot-block">
                        <strong>镜头 {shotIndex + 1}: {shot.title}</strong>
                        <p className="muted">
                          {shot.duration ?? '-'}s · {shot.framing} · {shot.camera_motion}
                        </p>
                        <p>{shot.description}</p>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div className="review-actions">
            <textarea
              placeholder="如需修改，请写明镜头、时长、剧情或节奏问题"
              value={scriptFeedback}
              onChange={(event) => setScriptFeedback(event.target.value)}
            />
            <div className="action-buttons">
              <button
                className="primary"
                disabled={scriptReview.isPending}
                onClick={() => scriptReview.mutate('APPROVE')}
              >
                {scriptReview.isPending && scriptReview.variables === 'APPROVE'
                  ? '已提交，等待返回中…'
                  : '通过剧本并继续'}
              </button>
              <button
                disabled={scriptReview.isPending || !scriptFeedback.trim()}
                onClick={() => scriptReview.mutate('REVISE')}
              >
                {scriptReview.isPending && scriptReview.variables === 'REVISE'
                  ? '已提交，等待返回中…'
                  : '按意见修改剧本'}
              </button>
            </div>
            {scriptReview.isError && (
              <p className="error">提交失败：{String(scriptReview.error)}</p>
            )}
          </div>
        </div>
      </div>
    )
  }

  // 资产审核
  if (stage === 'ASSET_REVIEW' && humanReview.characters) {
    return (
      <div className="review-panel">
        <h3>资产审核</h3>
        {assetNotice && (
          <div className="review-command-notice" role="status">
            <span>✓ {assetNotice}</span>
            <button aria-label="关闭提示" onClick={() => setAssetNotice('')}>×</button>
          </div>
        )}
        <div className="review-content">
          <div className="assets-preview">
            <div className="characters-section">
              <h4>角色 ({humanReview.characters.length})</h4>
              {humanReview.characters.map((char) => (
                <div key={char.id} className="asset-card">
                  <strong>{char.name}</strong>
                  <span className="muted"> · {char.gender} · {char.age_range} · {char.role}</span>
                  <p className="muted">{char.description}</p>
                  <p><strong>外形：</strong>{char.appearance}</p>
                  <p><strong>服装：</strong>{char.costume}</p>
                  {char.visual_anchors?.length > 0 && (
                    <p><strong>视觉锚点：</strong>{char.visual_anchors.join('、')}</p>
                  )}
                  {char.immutable_traits?.length > 0 && (
                    <p><strong>不可变特征：</strong>{char.immutable_traits.join('、')}</p>
                  )}
                  <span className={`badge ${char.status === 'LOCKED' ? 'ok' : ''}`}>{char.status}</span>
                  {approvedCharIds.has(char.id) && <span className="badge ok">已审核通过</span>}
                  <div className="asset-refs review-asset-refs">
                    {(assetBundle?.characters.find((item) => item.id === char.id)?.references ?? []).map((ref) => (
                      <div key={ref.id} className={`asset-view selectable ${selectedAssetIds.includes(ref.id) ? 'selected' : ''}`}>
                        <button className="image-preview-button" onClick={() => setPreviewImage({ url: `${API_BASE}${ref.media_url}`, alt: `${char.name} ${ref.view}` })}>
                          <img src={`${API_BASE}${ref.media_url}`} alt={`${char.name} ${ref.view}`} title="点击查看大图" />
                        </button>
                        <div className="asset-view-foot">
                          <span>{ref.view}</span>
                          <label><input type="checkbox" checked={selectedAssetIds.includes(ref.id)} onChange={() => setSelectedAssetIds((ids) => ids.includes(ref.id) ? ids.filter((id) => id !== ref.id) : [...ids, ref.id])} /> 打回此图</label>
                        </div>
                      </div>
                    ))}
                  </div>
                  {!approvedCharIds.has(char.id) && (
                    <button className="asset-approve-btn" onClick={() => setApprovedCharIds((prev) => new Set(prev).add(char.id))}>通过此角色</button>
                  )}
                </div>
              ))}
            </div>
            {humanReview.locations && humanReview.locations.length > 0 && (
              <div className="locations-section">
                <h4>场景 ({humanReview.locations.length})</h4>
                {humanReview.locations.map((loc) => (
                  <div key={loc.id} className="asset-card">
                    <strong>{loc.name}</strong>
                    <span className="muted"> · {loc.time_of_day_default}</span>
                    <p className="muted">{loc.description}</p>
                    <p><strong>风格：</strong>{loc.visual_style}</p>
                    {loc.colors?.length > 0 && (
                      <p><strong>主色：</strong>{loc.colors.join('、')}</p>
                    )}
                    {loc.materials?.length > 0 && (
                      <p><strong>材质：</strong>{loc.materials.join('、')}</p>
                    )}
                    {loc.visual_cues?.length > 0 && (
                      <p><strong>可视锚点：</strong>{loc.visual_cues.join('、')}</p>
                    )}
                    {loc.immutable_elements?.length > 0 && (
                      <p><strong>固定元素：</strong>{loc.immutable_elements.join('、')}</p>
                    )}
                    <span className={`badge ${loc.status === 'LOCKED' ? 'ok' : ''}`}>{loc.status}</span>
                    {approvedLocIds.has(loc.id) && <span className="badge ok">已审核通过</span>}
                    <div className="asset-refs review-asset-refs">
                      {(assetBundle?.locations.find((item) => item.id === loc.id)?.references ?? [])
                        .filter((ref) => LOCATION_REVIEW_VIEWS.has(ref.view ?? ''))
                        .map((ref) => (
                          <div key={ref.id} className={`asset-view selectable ${selectedAssetIds.includes(ref.id) ? 'selected' : ''}`}>
                            <button className="image-preview-button" onClick={() => setPreviewImage({ url: `${API_BASE}${ref.media_url}`, alt: `${loc.name} ${ref.view}` })}>
                              <img src={`${API_BASE}${ref.media_url}`} alt={`${loc.name} ${ref.view}`} title="点击查看大图" />
                            </button>
                            <div className="asset-view-foot">
                              <span>{LOCATION_VIEW_LABEL[ref.view ?? ''] ?? ref.view}</span>
                              <label><input type="checkbox" checked={selectedAssetIds.includes(ref.id)} onChange={() => setSelectedAssetIds((ids) => ids.includes(ref.id) ? ids.filter((id) => id !== ref.id) : [...ids, ref.id])} /> 打回此图</label>
                            </div>
                          </div>
                        ))}
                    </div>
                    {!approvedLocIds.has(loc.id) && (
                      <button className="asset-approve-btn" onClick={() => setApprovedLocIds((prev) => new Set(prev).add(loc.id))}>通过此场景</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="review-actions">
            <textarea
              placeholder="先勾选要打回的图片，再写明修改方向，例如：面罩改成有棱角的方形，保留深黑与橙色光泽"
              value={assetFeedback}
              onChange={(event) => setAssetFeedback(event.target.value)}
            />
            <div className="action-buttons">
              <button
                className="primary"
                disabled={assetReview.isPending || approvedCharIds.size < humanReview.characters.length || approvedLocIds.size < (humanReview.locations?.length ?? 0)}
                onClick={() => assetReview.mutate('APPROVE')}
              >
                {assetReview.isPending
                  ? assetReview.variables === 'REVISE'
                    ? '已接收打回指令，重新生成中...'
                    : '正在通过资产...'
                  : `通过资产并继续（${approvedCharIds.size}/${humanReview.characters.length} 角色，${approvedLocIds.size}/${humanReview.locations?.length ?? 0} 场景已审核）`}
              </button>
              <button
                disabled={assetReview.isPending || !assetFeedback.trim() || selectedAssetIds.length === 0}
                onClick={() => assetReview.mutate('REVISE')}
              >
                打回所选图片（{selectedAssetIds.length}）
              </button>
            </div>
            {assetReview.isError && (
              <p className="error">提交失败：{String(assetReview.error)}</p>
            )}
          </div>
          {imageModal}
        </div>
      </div>
    )
  }

  // 分镜审核
  if (stage === 'STORYBOARD_REVIEW' && humanReview.storyboards) {
    return (
      <div className="review-panel">
        <h3>分镜审核</h3>
        <p className="review-explainer">
          每个镜头可以有多个历史版本。蓝色“已选用于视频”表示通过审核后将使用该图作为视频首帧；
          “已锁定”只表示它曾经是确认版本，你仍可改选其他版本。
        </p>
        <div className="review-content">
          <div className="storyboards-preview">
            <div className="storyboard-grid">
              {humanReview.storyboards.map((sb) => (
                <div key={sb.id} className={`storyboard-card ${sb.is_locked ? 'locked' : ''}`}>
                  <button className="image-preview-button" onClick={() => setPreviewImage({ url: `${API_BASE}${sb.media_url}`, alt: `镜头 ${sb.shot_id}` })}>
                    <img src={`${API_BASE}${sb.media_url}`} alt={sb.id} title="点击查看大图" />
                  </button>
                  <div className="storyboard-meta">
                    <strong>镜头 {sb.shot_id}</strong>
                    <span className="muted">{sb.id}</span>
                    {sb.is_locked && <span className="badge ok">已锁定</span>}
                    {sb.is_selected && <span className="badge">已选用于视频</span>}
                  </div>
                  <button
                    className={sb.is_selected ? 'primary storyboard-choice' : 'storyboard-choice'}
                    disabled={chooseStoryboard.isPending || sb.is_selected}
                    onClick={() => chooseStoryboard.mutate(sb.id)}
                  >
                    {sb.is_selected ? '✓ 当前图片版本' : '回退并选用此图片版本'}
                  </button>
                  <button className="shot-detail-toggle" onClick={() => setExpandedShots((ids) => ids.includes(sb.id) ? ids.filter((id) => id !== sb.id) : [...ids, sb.id])}>
                    {expandedShots.includes(sb.id) ? '收起镜头文字' : '查看镜头文字'}
                  </button>
                  {expandedShots.includes(sb.id) && (
                    <div className="shot-text-detail">
                      <p><strong>标题：</strong>{sb.shot_title || '未命名镜头'}</p>
                      <p><strong>时长：</strong>{sb.duration ?? '-'} 秒　<strong>景别：</strong>{sb.framing || '-'}　<strong>运镜：</strong>{sb.camera_motion || '-'}</p>
                      <p><strong>画面内容：</strong>{sb.shot_description || '暂无描述'}</p>
                      <p><strong>生图提示词：</strong>{sb.prompt}</p>
                    </div>
                  )}
                  <textarea
                    placeholder="填写这个镜头需要怎样修改，例如构图、角色、动作或光线"
                    value={shotFeedback[sb.shot_id] ?? ''}
                    onChange={(event) => setShotFeedback((current) => ({
                      ...current, [sb.shot_id]: event.target.value,
                    }))}
                  />
                  <button
                    disabled={storyboardReview.isPending || !(shotFeedback[sb.shot_id] ?? '').trim()}
                    onClick={() => storyboardReview.mutate({
                      decision: 'REVISE',
                      feedback: shotFeedback[sb.shot_id],
                      shotIds: [sb.shot_id],
                    })}
                  >打回并重做这个镜头</button>
                </div>
              ))}
            </div>
          </div>
          <div className="review-actions">
            <textarea
              placeholder="如需修改，请写明分镜的问题，如构图、风格、角色表现等"
              value={storyboardFeedback}
              onChange={(event) => setStoryboardFeedback(event.target.value)}
            />
            <div className="action-buttons">
              <button
                className="primary"
                disabled={storyboardReview.isPending}
                onClick={() => storyboardReview.mutate({ decision: 'APPROVE' })}
              >
                通过分镜并继续
              </button>
              <button
                disabled={storyboardReview.isPending || !storyboardFeedback.trim()}
                onClick={() => storyboardReview.mutate({ decision: 'REVISE' })}
              >
                按意见修改分镜
              </button>
            </div>
            {storyboardReview.isError && (
              <p className="error">提交失败：{String(storyboardReview.error)}</p>
            )}
          </div>
          {imageModal}
        </div>
      </div>
    )
  }

  // 场景人工复审：一个大场景就是一个生成、审核和回退版本。
  if (stage === 'HUMAN_TAKE_REVIEW') {
    const scenes = humanReview.scenes_review ?? []
    const selectedScenes = scenes.filter((scene) => selectedTakeIds.includes(scene.id) && scene.status !== 'RUNNING' && scene.status !== 'PENDING')
    const invalidSelected = selectedScenes.filter((scene) => !(takeFeedback[scene.id] ?? '').trim())
    const rejectSelected = async () => {
      if (invalidSelected.length > 0) {
        setTakeReviewError(`请分别填写修改指令：${invalidSelected.map((scene) => scene.title).join('、')}`)
        return
      }
      await Promise.all(selectedScenes.map((scene) => submitOneScene(scene.id, 'RETAKE')))
    }
    return (
      <div className="review-panel">
        <h3>场景视频人工复审</h3>
        {takeNotice && (
          <div className="review-command-notice" role="status">
            <span>✓ {takeNotice}</span>
            <button aria-label="关闭提示" onClick={() => setTakeNotice('')}>×</button>
          </div>
        )}
        <p className="review-explainer">
          01–06 每个大场景只生成和审核一条完整视频。时长由剧本内镜头时长相加，
          小镜头只用于文本 API 编排动作与节奏，不再单独展示或单独提交 DGX。
        </p>
        <div className="take-review-grid">
          {scenes.map((scene) => {
            const generating = scene.status === 'RUNNING' || scene.status === 'PENDING'
            return (
            <div key={scene.id} className={`take-review-card ${scene.decision?.decision === 'APPROVE' ? 'approved' : ''} ${selectedTakeIds.includes(scene.id) ? 'selected' : ''}`}>
              <div className="take-review-heading">
                <strong>{String(scene.index).padStart(2, '0')} {scene.title}</strong>
                <span>{scene.duration.toFixed(1)} 秒 · {scene.shot_count} 个剧本镜头</span>
                {scene.decision?.decision === 'APPROVE' && <span className="badge ok">已人工通过</span>}
                {generating && <span className="badge generating">DGX 整场生成中</span>}
                <label className="take-reject-check">
                  <input
                    type="checkbox"
                    checked={selectedTakeIds.includes(scene.id)}
                    disabled={generating || submittingTakeIds.includes(scene.id)}
                    onChange={() => setSelectedTakeIds((ids) => ids.includes(scene.id) ? ids.filter((id) => id !== scene.id) : [...ids, scene.id])}
                  />
                  勾选打回
                </label>
              </div>
              {scene.media_url
                ? <video src={`${API_BASE}${scene.media_url}`} controls preload="metadata" />
                : <div className="video-placeholder">{generating ? '整场视频生成中…' : '尚无完整场景视频，请重新生成此场景'}</div>}
              <details>
                <summary>查看剧本镜头与整场视频提示词</summary>
                <p><strong>内部镜头：</strong>{scene.shot_titles.join(' → ')}</p>
                <p><strong>视频提示词：</strong>{scene.prompt}</p>
              </details>
              <textarea
                placeholder="填写整场修改指令，文本 API 会结合剧本、人物、场景和镜头要求重写提示词"
                value={takeFeedback[scene.id] ?? ''}
                disabled={generating || submittingTakeIds.includes(scene.id)}
                onChange={(event) => setTakeFeedback((current) => ({ ...current, [scene.id]: event.target.value }))}
              />
              <div className="action-buttons">
                <button
                  className={scene.decision?.decision === 'APPROVE' ? 'primary' : ''}
                  disabled={generating || submittingTakeIds.includes(scene.id) || !scene.media_url || scene.decision?.decision === 'APPROVE'}
                  onClick={() => submitOneScene(scene.id, 'APPROVE')}
                >{submittingTakeIds.includes(scene.id) ? '提交中...' : scene.decision?.decision === 'APPROVE' ? '✓ 已通过' : '通过此场景'}</button>
              </div>
              {scene.decision?.feedback && <p className="muted">上次意见：{scene.decision.feedback}</p>}
            </div>
          )})}
          {scenes.length === 0 && <p className="muted">场景视频正在生成，完成后会自动显示在这里。</p>}
        </div>
        {takeReviewError && <p className="error">{takeReviewError}</p>}
        <div className="take-review-footer">
          <span className="muted">已勾选 {selectedScenes.length} 个完整场景</span>
          <button
            disabled={selectedScenes.length === 0 || selectedScenes.some((scene) => submittingTakeIds.includes(scene.id))}
            onClick={rejectSelected}
          >整场打回（{selectedScenes.length}）</button>
        </div>
      </div>
    )
  }

  // 没有待审核内容
  return (
    <div className="review-panel empty">
      <p className="muted">
        {stage === 'COMPLETE' ? '流水线已完成' : '当前无需人工审核'}
      </p>
    </div>
  )
}
