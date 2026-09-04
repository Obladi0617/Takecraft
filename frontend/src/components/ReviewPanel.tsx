import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  API_BASE,
  fetchAssets,
  fetchHumanReview,
  submitScriptReview,
  submitAssetReview,
  submitStoryboardReview,
} from '../api/client'
import { useAppStore } from '../stores/app'

const LOCATION_REVIEW_VIEWS = new Set(['ESTABLISHING', 'KEY_ANGLE_A', 'DETAIL'])
const LOCATION_VIEW_LABEL: Record<string, string> = {
  ESTABLISHING: '主角度',
  KEY_ANGLE_A: '角度 A',
  DETAIL: '细节',
}

export default function ReviewPanel() {
  const projectId = useAppStore((s) => s.projectId)!
  const queryClient = useQueryClient()
  const [scriptFeedback, setScriptFeedback] = useState('')
  const [assetFeedback, setAssetFeedback] = useState('')
  const [selectedAssetIds, setSelectedAssetIds] = useState<string[]>([])
  const [storyboardFeedback, setStoryboardFeedback] = useState('')
  const [shotFeedback, setShotFeedback] = useState<Record<string, string>>({})
  const [previewImage, setPreviewImage] = useState<{ url: string; alt: string } | null>(null)
  const [expandedShots, setExpandedShots] = useState<string[]>([])

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
  })

  const scriptReview = useMutation({
    mutationFn: (decision: 'APPROVE' | 'REVISE') =>
      submitScriptReview(projectId, decision, scriptFeedback),
    onSuccess: () => {
      setScriptFeedback('')
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline', projectId] })
    },
  })

  const assetReview = useMutation({
    mutationFn: (decision: 'APPROVE' | 'REVISE') =>
      submitAssetReview(projectId, decision, assetFeedback, decision === 'REVISE' ? selectedAssetIds : []),
    onSuccess: () => {
      setAssetFeedback('')
      setSelectedAssetIds([])
      queryClient.invalidateQueries({ queryKey: ['human-review', projectId] })
      queryClient.invalidateQueries({ queryKey: ['pipeline', projectId] })
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
                通过剧本并继续
              </button>
              <button
                disabled={scriptReview.isPending || !scriptFeedback.trim()}
                onClick={() => scriptReview.mutate('REVISE')}
              >
                按意见修改剧本
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
                disabled={assetReview.isPending || assetReview.isSuccess}
                onClick={() => assetReview.mutate('APPROVE')}
              >
                {assetReview.isPending
                  ? '正在提交...'
                  : assetReview.isSuccess
                    ? '已通过，正在生成资产参考图...'
                    : '通过资产并继续'}
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
                    {sb.is_selected && <span className="badge">已选定</span>}
                  </div>
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

  // 没有待审核内容
  return (
    <div className="review-panel empty">
      <p className="muted">
        {stage === 'COMPLETE' ? '流水线已完成' : '当前无需人工审核'}
      </p>
    </div>
  )
}
