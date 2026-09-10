import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  API_BASE,
  createCharacter,
  createLocation,
  deleteCharacter,
  deleteLocation,
  fetchAssets,
  fetchGenerationJobs,
  fetchScenes,
  generateCharacterRefs,
  generateLocationRefs,
  lockCharacter,
  lockLocation,
  patchCharacter,
  reviseCharacterSetting,
  patchLocation,
  relinkShots,
  reviseLocationSetting,
  restoreAssetVersion,
  uploadCharacterRef,
  uploadLocationRef,
} from '../api/client'
import type { CharacterBody, LocationBody } from '../api/client'
import type { AssetReference, AssetStatus, Character, Location } from '../api/types'
import { useAppStore } from '../stores/app'
import MediaModal, { type MediaPreview } from './MediaModal'

const ACTIVE = new Set(['PENDING', 'RUNNING', 'RETAKE'])

const STATUS_LABEL: Record<AssetStatus, string> = {
  DRAFT: '草稿',
  PENDING_CONFIRM: '待确认',
  LOCKED: '已锁定',
}

const VIEW_LABEL: Record<string, string> = {
  FRONT: '正面',
  SIDE: '侧面',
  BACK: '背面',
  ESTABLISHING: '主角度',
  DETAIL: '细节',
}

const ROLE_LABEL: Record<string, string> = {
  PRIMARY: '主角',
  SUPPORTING: '配角',
}

const SOURCE_OPTIONS = [
  { value: 'AUTO', label: '自动生成' },
  { value: 'IMPORT', label: '导入素材' },
]

const toText = (items: string[]) => items.join('、')
const toList = (text: string) =>
  text
    .split(/[、,，;；\n]/)
    .map((s) => s.trim())
    .filter(Boolean)

function ViewSlot({
  view,
  reference,
  disabled,
  onUpload,
  onPreview,
  onRestore,
}: {
  view: string
  reference: AssetReference | undefined
  disabled: boolean
  onUpload: (file: File) => void
  onPreview: () => void
  onRestore: (versionId: string) => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  return (
    <div className={`asset-view ${reference ? '' : 'empty'}`}>
      {reference ? (
        <button className="image-preview-button" onClick={onPreview} aria-label={`在当前页面查看${VIEW_LABEL[view] ?? view}`}>
          <img src={`${API_BASE}${reference.media_url}`} alt={VIEW_LABEL[view] ?? view} title="点击查看原图" />
        </button>
      ) : (
        <div className="asset-view-placeholder">未生成</div>
      )}
      <div className="asset-view-foot">
        <span>{VIEW_LABEL[view] ?? view}</span>
        <button
          className="ghost"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
        >
          上传
        </button>
      </div>
      {(reference?.versions?.length ?? 0) > 0 && (
        <details className="asset-history">
          <summary>历史版本（{reference!.versions!.length}）</summary>
          <div className="asset-history-list">
            {[...reference!.versions!].reverse().map((version, index) => (
              <div className="asset-history-item" key={version.id}>
                <img src={`${API_BASE}${version.media_url}`} alt={`历史版本 ${index + 1}`} title="历史图片缩略图" />
                <button
                  disabled={disabled}
                  onClick={() => window.confirm('恢复这个图片版本？当前版本也会自动保留，可再次恢复。') && onRestore(version.id)}
                >恢复此版</button>
              </div>
            ))}
          </div>
        </details>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          if (file) onUpload(file)
          e.target.value = ''
        }}
      />
    </div>
  )
}

function RefStrip({
  views,
  references,
  disabled,
  onUpload,
  onPreview,
  onRestore,
}: {
  views: string[]
  references: AssetReference[]
  disabled: boolean
  onUpload: (view: string, file: File) => void
  onPreview: (view: string, reference: AssetReference) => void
  onRestore: (reference: AssetReference, versionId: string) => void
}) {
  const byView = new Map(references.map((r) => [r.view, r]))
  return (
    <div className="asset-refs">
      {views.map((view) => (
        <ViewSlot
          key={view}
          view={view}
          reference={byView.get(view)}
          disabled={disabled}
          onUpload={(file) => onUpload(view, file)}
          onPreview={() => {
            const reference = byView.get(view)
            if (reference) onPreview(view, reference)
          }}
          onRestore={(versionId) => {
            const reference = byView.get(view)
            if (reference) onRestore(reference, versionId)
          }}
        />
      ))}
    </div>
  )
}

function PromptBlock({ block, hash }: { block: string; hash: string }) {
  const [open, setOpen] = useState(false)
  if (!block) return null
  return (
    <div className="asset-prompt">
      <button className="ghost" onClick={() => setOpen(!open)}>
        {open ? '收起锁定提示词' : '查看锁定提示词'}
      </button>
      <span className="muted">#{hash}</span>
      {open && <pre>{block}</pre>}
    </div>
  )
}

function CharacterForm({
  character,
  onSave,
  onRevise,
  pending,
  revising,
}: {
  character: Character
  onSave: (body: CharacterBody) => void
  onRevise: (feedback: string) => void
  pending: boolean
  revising: boolean
}) {
  const [revisionFeedback, setRevisionFeedback] = useState('')
  const [name, setName] = useState(character.name)
  const [role, setRole] = useState(character.role)
  const [gender, setGender] = useState(character.gender ?? '')
  const [ageRange, setAgeRange] = useState(character.age_range ?? '')
  const [appearance, setAppearance] = useState(character.appearance)
  const [costume, setCostume] = useState(character.costume)
  const [personality, setPersonality] = useState(character.personality)
  const [description, setDescription] = useState(character.description)
  const [anchors, setAnchors] = useState(toText(character.visual_anchors))
  const [traits, setTraits] = useState(toText(character.immutable_traits))

  return (
    <div className="asset-form">
      <label className="wide">
        告诉AI角色设定哪里有问题、需要怎么改
        <textarea
          rows={3}
          placeholder="例如：人物太矮胖，改成身高约190cm、精壮敏捷；保留黑色短发和左眼伤疤，其他设定不要改。"
          value={revisionFeedback}
          onChange={(e) => setRevisionFeedback(e.target.value)}
        />
      </label>
      <div className="asset-form-actions wide">
        <button
          className="primary"
          disabled={revising || !revisionFeedback.trim()}
          onClick={() => onRevise(revisionFeedback.trim())}
        >
          {revising ? '已提交，等待新设定返回中…' : '按意见修改角色设定'}
        </button>
        <span className="muted">先按意见修改文字设定，再立即按最新设定重新生成角色参考图。</span>
      </div>
      <label>
        姓名
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        定位
        <select value={role} onChange={(e) => setRole(e.target.value)}>
          {Object.entries(ROLE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <label>
        性别
        <input value={gender} onChange={(e) => setGender(e.target.value)} />
      </label>
      <label>
        年龄段
        <input value={ageRange} onChange={(e) => setAgeRange(e.target.value)} />
      </label>
      <label className="wide">
        外形
        <textarea rows={2} value={appearance} onChange={(e) => setAppearance(e.target.value)} />
      </label>
      <label className="wide">
        服装
        <input value={costume} onChange={(e) => setCostume(e.target.value)} />
      </label>
      <label className="wide">
        性格
        <input value={personality} onChange={(e) => setPersonality(e.target.value)} />
      </label>
      <label className="wide">
        人物小传
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="wide">
        视觉锚点（顿号分隔）
        <input value={anchors} onChange={(e) => setAnchors(e.target.value)} />
      </label>
      <label className="wide">
        不可变特征（顿号分隔，锁定后逐字注入提示词）
        <input value={traits} onChange={(e) => setTraits(e.target.value)} />
      </label>
      <div className="asset-form-actions">
        <button
          className="primary"
          disabled={pending || !name.trim()}
          onClick={() =>
            onSave({
              name: name.trim(),
              role,
              gender: gender.trim(),
              age_range: ageRange.trim(),
              appearance: appearance.trim(),
              costume: costume.trim(),
              personality: personality.trim(),
              description: description.trim(),
              visual_anchors: toList(anchors),
              immutable_traits: toList(traits),
            })
          }
        >
          {pending ? '保存中…' : '保存设定'}
        </button>
      </div>
    </div>
  )
}

function LocationForm({
  location,
  scenes,
  onSave,
  onRevise,
  pending,
  revising,
}: {
  location: Location
  scenes: { id: string; title: string }[]
  onSave: (body: LocationBody) => void
  onRevise: (feedback: string) => void
  pending: boolean
  revising: boolean
}) {
  const [revisionFeedback, setRevisionFeedback] = useState('')
  const [name, setName] = useState(location.name)
  const [sceneId, setSceneId] = useState(location.scene_id ?? '')
  const [description, setDescription] = useState(location.description)
  const [visualStyle, setVisualStyle] = useState(location.visual_style)
  const [timeOfDay, setTimeOfDay] = useState(location.time_of_day_default)
  const [materials, setMaterials] = useState(toText(location.materials))
  const [colors, setColors] = useState(toText(location.colors))
  const [cues, setCues] = useState(toText(location.visual_cues))
  const [elements, setElements] = useState(toText(location.immutable_elements))
  const [lighting, setLighting] = useState(toText(location.lighting_rules))

  return (
    <div className="asset-form">
      <label className="wide">
        告诉AI场景设定哪里有问题、需要怎么改
        <textarea
          rows={3}
          placeholder="例如：场景太现代，改成荷马史诗时期的特洛伊石砌城门；整体更写实、更宏大，保留黄昏和火把照明。"
          value={revisionFeedback}
          onChange={(e) => setRevisionFeedback(e.target.value)}
        />
      </label>
      <div className="asset-form-actions wide">
        <button
          className="primary"
          disabled={revising || !revisionFeedback.trim()}
          onClick={() => onRevise(revisionFeedback.trim())}
        >
          {revising ? '已提交，等待新设定返回中…' : '按意见修改场景设定'}
        </button>
        <span className="muted">文本API先修改设定，再自动重生成主角度和细节图。</span>
      </div>
      <label>
        名称
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        关联场景
        <select value={sceneId} onChange={(e) => setSceneId(e.target.value)}>
          <option value="">未绑定</option>
          {scenes.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title || s.id}
            </option>
          ))}
        </select>
      </label>
      <label>
        默认时段
        <input value={timeOfDay} onChange={(e) => setTimeOfDay(e.target.value)} />
      </label>
      <label className="wide">
        场景描述
        <textarea rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
      </label>
      <label className="wide">
        视觉风格
        <input value={visualStyle} onChange={(e) => setVisualStyle(e.target.value)} />
      </label>
      <label className="wide">
        材质（顿号分隔）
        <input value={materials} onChange={(e) => setMaterials(e.target.value)} />
      </label>
      <label className="wide">
        色彩（顿号分隔）
        <input value={colors} onChange={(e) => setColors(e.target.value)} />
      </label>
      <label className="wide">
        可视锚点（顿号分隔）
        <input value={cues} onChange={(e) => setCues(e.target.value)} />
      </label>
      <label className="wide">
        固定元素（顿号分隔，锁定后逐字注入提示词）
        <input value={elements} onChange={(e) => setElements(e.target.value)} />
      </label>
      <label className="wide">
        光线规则（顿号分隔）
        <input value={lighting} onChange={(e) => setLighting(e.target.value)} />
      </label>
      <div className="asset-form-actions">
        <button
          className="primary"
          disabled={pending || !name.trim()}
          onClick={() =>
            onSave({
              name: name.trim(),
              scene_id: sceneId || null,
              description: description.trim(),
              visual_style: visualStyle.trim(),
              time_of_day_default: timeOfDay.trim(),
              materials: toList(materials),
              colors: toList(colors),
              visual_cues: toList(cues),
              immutable_elements: toList(elements),
              lighting_rules: toList(lighting),
            })
          }
        >
          {pending ? '保存中…' : '保存设定'}
        </button>
      </div>
    </div>
  )
}

export default function AssetPanel() {
  const projectId = useAppStore((s) => s.projectId)!
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'character' | 'location'>('character')
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')
  const [newSource, setNewSource] = useState('AUTO')
  const [newSceneId, setNewSceneId] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [mediaPreview, setMediaPreview] = useState<MediaPreview | null>(null)
  const [flowNotice, setFlowNotice] = useState('')

  const { data: jobs } = useQuery({
    queryKey: ['jobs', projectId],
    queryFn: () => fetchGenerationJobs(projectId),
    refetchInterval: 2000,
  })
  const assetJobsActive = (jobs ?? []).some(
    (j) => (j.job_type === 'CHARACTER' || j.job_type === 'LOCATION') && ACTIVE.has(j.status),
  )

  const { data: assets } = useQuery({
    queryKey: ['assets', projectId],
    queryFn: () => fetchAssets(projectId),
    // 生成任务完成与 jobs 轮询更新存在竞态；持续轻量轮询可保证最后一次
    // 覆盖后的带版本 media_url 自动进入页面，无需人工刷新。
    refetchInterval: 1500,
    refetchIntervalInBackground: true,
    refetchOnWindowFocus: true,
  })

  const { data: scenes } = useQuery({
    queryKey: ['scenes', projectId],
    queryFn: () => fetchScenes(projectId),
    enabled: tab === 'location',
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['assets', projectId] })
    queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
    queryClient.invalidateQueries({ queryKey: ['jobs', projectId] })
  }

  const genCharacter = useMutation({
    mutationFn: (id: string) => generateCharacterRefs(projectId, id),
    onSuccess: invalidate,
  })
  const lockChar = useMutation({
    mutationFn: (id: string) => lockCharacter(projectId, id),
    onSuccess: (result) => {
      if ((result.location_jobs?.length ?? 0) > 0) {
        setFlowNotice(`全部角色已确认，已自动提交 ${result.location_jobs!.length} 个场景生成任务。`)
      }
      invalidate()
    },
  })
  const uploadChar = useMutation({
    mutationFn: ({ id, view, file }: { id: string; view: string; file: File }) =>
      uploadCharacterRef(projectId, id, file, view),
    onSuccess: invalidate,
  })
  const restoreAsset = useMutation({
    mutationFn: ({ assetId, versionId }: { assetId: string; versionId: string }) =>
      restoreAssetVersion(projectId, assetId, versionId),
    onSuccess: () => {
      setFlowNotice('已恢复上一版本；刚才的版本也已保留，可随时切回。')
      invalidate()
    },
  })
  const patchChar = useMutation({
    mutationFn: ({ id, body }: { id: string; body: CharacterBody }) =>
      patchCharacter(projectId, id, body),
    onSuccess: () => {
      setEditingId(null)
      invalidate()
    },
  })
  const reviseChar = useMutation({
    mutationFn: ({ id, feedback }: { id: string; feedback: string }) =>
      reviseCharacterSetting(projectId, id, feedback),
    onSuccess: invalidate,
  })
  const addCharacter = useMutation({
    mutationFn: (body: CharacterBody) => createCharacter(projectId, body),
    onSuccess: invalidate,
  })
  const removeCharacter = useMutation({
    mutationFn: (id: string) => deleteCharacter(projectId, id),
    onSuccess: invalidate,
  })

  const genLocation = useMutation({
    mutationFn: (id: string) => generateLocationRefs(projectId, id),
    onSuccess: invalidate,
  })
  const lockLoc = useMutation({
    mutationFn: (id: string) => lockLocation(projectId, id),
    onSuccess: invalidate,
  })
  const uploadLoc = useMutation({
    mutationFn: ({ id, view, file }: { id: string; view: string; file: File }) =>
      uploadLocationRef(projectId, id, file, view),
    onSuccess: invalidate,
  })
  const patchLoc = useMutation({
    mutationFn: ({ id, body }: { id: string; body: LocationBody }) =>
      patchLocation(projectId, id, body),
    onSuccess: () => {
      setEditingId(null)
      invalidate()
    },
  })
  const reviseLoc = useMutation({
    mutationFn: ({ id, feedback }: { id: string; feedback: string }) =>
      reviseLocationSetting(projectId, id, feedback),
    onSuccess: invalidate,
  })
  const addLocation = useMutation({
    mutationFn: (body: LocationBody) => createLocation(projectId, body),
    onSuccess: (result) => {
      setFlowNotice(result.message || '场景已创建，参考图生成任务已自动提交。')
      setCreating(false)
      setNewName('')
      setNewDesc('')
      setNewSceneId('')
      invalidate()
    },
  })
  const removeLocation = useMutation({
    mutationFn: (id: string) => deleteLocation(projectId, id),
    onSuccess: invalidate,
  })

  const relink = useMutation({
    mutationFn: () => relinkShots(projectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['shots', projectId] })
    },
  })

  const characters = assets?.characters ?? []
  const locations = assets?.locations ?? []
  const busy = assetJobsActive
  const busyOwnerIds = new Set(
    (jobs ?? [])
      .filter((job) => (job.job_type === 'CHARACTER' || job.job_type === 'LOCATION') && ACTIVE.has(job.status))
      .map((job) => String(job.payload?.owner_id ?? '')),
  )
  const actionError =
    tab === 'character'
      ? [genCharacter, lockChar, uploadChar, patchChar, reviseChar, addCharacter, removeCharacter]
      : [genLocation, lockLoc, uploadLoc, patchLoc, reviseLoc, addLocation, removeLocation]
  const failed = actionError.find((m) => m.isError)

  const submitCreate = () => {
    if (!newName.trim()) return
    if (tab === 'character') {
      addCharacter.mutate({
        name: newName.trim(),
        description: newDesc.trim(),
        source: newSource,
      })
    } else {
      addLocation.mutate({
        name: newName.trim(),
        description: newDesc.trim(),
        scene_id: newSceneId || null,
        source: newSource,
      })
    }
    setNewName('')
    setNewDesc('')
    setNewSceneId('')
    setCreating(false)
  }

  return (
    <div className="asset-panel">
      <MediaModal media={mediaPreview} onClose={() => setMediaPreview(null)} />
      {flowNotice && (
        <div className="review-command-notice" role="status" aria-live="polite">
          <span>✓ {flowNotice}</span>
          <button aria-label="关闭提示" onClick={() => setFlowNotice('')}>×</button>
        </div>
      )}
      <div className="asset-toolbar">
        <div className="asset-tabs">
          <button
            className={tab === 'character' ? 'primary' : ''}
            onClick={() => {
              setTab('character')
              setEditingId(null)
            }}
          >
            角色（{characters.length}）
          </button>
          <button
            className={tab === 'location' ? 'primary' : ''}
            onClick={() => {
              setTab('location')
              setEditingId(null)
            }}
          >
            场景（{locations.length}）
          </button>
        </div>
        <span className="spacer" />
        {busy && <span className="badge ok">参考图生成中…</span>}
        <button onClick={() => relink.mutate()} disabled={relink.isPending}>
          重新关联镜头
        </button>
        <button className="primary" onClick={() => setCreating(!creating)}>
          {creating ? '收起' : tab === 'character' ? '新建角色' : '新建场景'}
        </button>
      </div>

      {creating && (
        <div className="asset-create">
          <input
            placeholder={tab === 'character' ? '角色姓名' : '场景名称'}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <input
            placeholder="一句话描述"
            value={newDesc}
            onChange={(e) => setNewDesc(e.target.value)}
          />
          {tab === 'location' && (
            <select value={newSceneId} onChange={(e) => setNewSceneId(e.target.value)}>
              <option value="">未绑定场景</option>
              {(scenes ?? []).map((s) => (
                <option key={s.id} value={s.id}>
                  {s.title || s.id}
                </option>
              ))}
            </select>
          )}
          <select value={newSource} onChange={(e) => setNewSource(e.target.value)}>
            {SOURCE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <button className="primary" disabled={!newName.trim()} onClick={submitCreate}>
            创建
          </button>
        </div>
      )}

      {failed && <p className="error">操作失败：{String(failed.error)}</p>}
      {relink.isError && <p className="error">关联失败：{String(relink.error)}</p>}

      <div className="asset-list">
        {tab === 'character' && characters.length === 0 && (
          <p className="muted">
            还没有角色资产——用工具栏「一句话生成」自动产出，或点「新建角色」手动登记。
          </p>
        )}
        {tab === 'location' && locations.length === 0 && (
          <p className="muted">
            还没有场景资产——锁定后同一场景的所有镜头会自动引用它的固定元素。
          </p>
        )}

        {tab === 'character' &&
          characters.map((c) => {
            const locked = c.status === 'LOCKED'
            const ownerBusy = busyOwnerIds.has(c.id)
            const submittingThisCharacter = genCharacter.isPending && genCharacter.variables === c.id
            return (
              <div key={c.id} className={`asset-card ${locked ? 'locked' : ''}`}>
                <div className="asset-card-head">
                  <strong>{c.name || c.id}</strong>
                  <span className="muted">{c.id}</span>
                  <span className="badge">{ROLE_LABEL[c.role] ?? c.role}</span>
                  <span className={`badge ${locked ? 'ok' : ''}`}>
                    {STATUS_LABEL[c.status]}
                  </span>
                  {c.version > 1 && <span className="badge">v{c.version}</span>}
                  <span className="spacer" />
                  <span className="muted">seed {c.seed ?? '-'}</span>
                </div>
                <p className="asset-desc muted">
                  {c.appearance || c.description || '尚未填写外形描述'}
                </p>
                <RefStrip
                  views={c.views}
                  references={c.references}
                  disabled={ownerBusy}
                  onUpload={(view, file) => uploadChar.mutate({ id: c.id, view, file })}
                  onPreview={(view, reference) => setMediaPreview({
                    kind: 'image',
                    url: `${API_BASE}${reference.media_url}`,
                    alt: `${c.name} ${VIEW_LABEL[view] ?? view}`,
                  })}
                  onRestore={(reference, versionId) => restoreAsset.mutate({ assetId: reference.id, versionId })}
                />
                <div className="asset-actions">
                  <button disabled={ownerBusy || submittingThisCharacter} onClick={() => genCharacter.mutate(c.id)}>
                    {submittingThisCharacter
                      ? '提交中…'
                      : ownerBusy
                        ? '生成中…'
                      : c.references.length
                        ? '重新生成三视图'
                        : '生成三视图'}
                  </button>
                  <button
                    className="primary"
                    disabled={ownerBusy || lockChar.isPending}
                    onClick={() => lockChar.mutate(c.id)}
                  >
                    {locked ? '更新锁定' : '确认并锁定'}
                  </button>
                  <button
                    className="ghost"
                    disabled={false}
                    onClick={() => setEditingId(editingId === c.id ? null : c.id)}
                  >
                    {editingId === c.id ? '收起设定' : '编辑设定'}
                  </button>
                  <button
                    className="danger"
                    disabled={ownerBusy || removeCharacter.isPending}
                    onClick={() => window.confirm(`删除角色“${c.name}”？已有图片文件会保留。`) && removeCharacter.mutate(c.id)}
                  >删除角色</button>
                </div>
                {locked ? (
                  <p className="muted asset-hint">
                    已锁定：不可变特征逐字注入所有关联镜头。要改设定请先「重新生成三视图」，
                    资产会退回待确认。
                  </p>
                ) : (
                  c.references.length > 0 && (
                    <p className="muted asset-hint">
                      参考图就绪，确认无误后锁定；锁定后才会注入镜头提示词。
                    </p>
                  )
                )}
                <PromptBlock block={c.prompt_block} hash={c.prompt_block_hash} />
                {editingId === c.id && (
                  <CharacterForm
                    key={`${c.id}-${c.version}`}
                    character={c}
                    pending={patchChar.isPending}
                    revising={reviseChar.isPending && reviseChar.variables?.id === c.id}
                    onSave={(body) => patchChar.mutate({ id: c.id, body })}
                    onRevise={(feedback) => reviseChar.mutate({ id: c.id, feedback })}
                  />
                )}
              </div>
            )
          })}

        {tab === 'location' &&
          locations.map((loc) => {
            const locked = loc.status === 'LOCKED'
            const ownerBusy = busyOwnerIds.has(loc.id)
            const submittingThisLocation = genLocation.isPending && genLocation.variables === loc.id
            const scene = (scenes ?? []).find((s) => s.id === loc.scene_id)
            return (
              <div key={loc.id} className={`asset-card ${locked ? 'locked' : ''}`}>
                <div className="asset-card-head">
                  <strong>{loc.name || loc.id}</strong>
                  <span className="muted">{loc.id}</span>
                  {scene && <span className="badge">{scene.title}</span>}
                  <span className={`badge ${locked ? 'ok' : ''}`}>
                    {STATUS_LABEL[loc.status]}
                  </span>
                  {loc.version > 1 && <span className="badge">v{loc.version}</span>}
                  <span className="spacer" />
                  <span className="muted">seed {loc.seed ?? '-'}</span>
                </div>
                <p className="asset-desc muted">
                  {loc.description ||
                    [loc.visual_style, loc.time_of_day_default].filter(Boolean).join(' · ') ||
                    '尚未填写场景描述'}
                </p>
                <RefStrip
                  views={loc.views}
                  references={loc.references}
                  disabled={ownerBusy}
                  onUpload={(view, file) => uploadLoc.mutate({ id: loc.id, view, file })}
                  onPreview={(view, reference) => setMediaPreview({
                    kind: 'image',
                    url: `${API_BASE}${reference.media_url}`,
                    alt: `${loc.name} ${VIEW_LABEL[view] ?? view}`,
                  })}
                  onRestore={(reference, versionId) => restoreAsset.mutate({ assetId: reference.id, versionId })}
                />
                <div className="asset-actions">
                  <button disabled={ownerBusy || submittingThisLocation} onClick={() => genLocation.mutate(loc.id)}>
                    {submittingThisLocation
                      ? '提交中…'
                      : ownerBusy
                        ? '生成中…'
                      : loc.references.length
                        ? '重新生成参考图'
                        : '生成参考图'}
                  </button>
                  <button
                    className="primary"
                    disabled={ownerBusy || lockLoc.isPending}
                    onClick={() => lockLoc.mutate(loc.id)}
                  >
                    {locked ? '更新锁定' : '确认并锁定'}
                  </button>
                  <button
                    className="ghost"
                    disabled={false}
                    onClick={() => setEditingId(editingId === loc.id ? null : loc.id)}
                  >
                    {editingId === loc.id ? '收起设定' : '编辑设定'}
                  </button>
                  <button
                    className="danger"
                    disabled={ownerBusy || removeLocation.isPending}
                    onClick={() => window.confirm(`删除场景“${loc.name}”？已有图片文件会保留。`) && removeLocation.mutate(loc.id)}
                  >删除场景</button>
                </div>
                {locked ? (
                  <p className="muted asset-hint">
                    已锁定：同场景所有镜头自动引用固定元素。要改设定请先「重新生成参考图」。
                  </p>
                ) : (
                  loc.references.length > 0 && (
                    <p className="muted asset-hint">
                      参考图就绪，确认无误后锁定；锁定后才会注入镜头提示词。
                    </p>
                  )
                )}
                <PromptBlock block={loc.prompt_block} hash={loc.prompt_block_hash} />
                {editingId === loc.id && (
                  <LocationForm
                    key={`${loc.id}-${loc.version}`}
                    location={loc}
                    scenes={scenes ?? []}
                    pending={patchLoc.isPending}
                    revising={reviseLoc.isPending && reviseLoc.variables?.id === loc.id}
                    onSave={(body) => patchLoc.mutate({ id: loc.id, body })}
                    onRevise={(feedback) => reviseLoc.mutate({ id: loc.id, feedback })}
                  />
                )}
              </div>
            )
          })}
      </div>
    </div>
  )
}
