/* 工件面板（能力升级批次 A2，spec：Artifact Panel）——聊天右侧可收缩三栏之一。
 *
 * 三个 tab（CONTEXT.md「Artifact Panel」词条）：
 *   附件  — 老师上传件的原图预览（走带鉴权的 /api/agent/upload/{id}/image）
 *   工件  — AI 生成产物的渲染预览（HTML 类走 sandbox iframe，禁同源禁外链）
 *   版本  — 当前工件的版本历史，点任一版本切预览
 * 一个收缩机制一套布局代码同时承载「回看上传图」与「预览产物」两个需求。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  PanelRightClose, Paperclip, Sparkles, History,
  Film, Presentation, FileText, Image as ImageIcon, Table2,
} from 'lucide-react'
import { api } from '../../lib/api'

export type PanelTab = 'attachments' | 'artifact' | 'versions'

/** 面板焦点：从消息流联动打开（工件卡 / 上传卡点开时由 ChatPage 传入）。 */
export interface PanelFocus {
  tab: PanelTab
  /** 附件：upload_id；工件：artifact_id */
  targetId?: string
}

export interface ArtifactRef {
  artifact_id: string
  kind: string
  title: string
  version_seq: number
  url: string
}

export interface Attachment {
  upload_id: string
  file_name: string
  image_url?: string | null
}

/** 工件类型展示元数据——kind 集合与后端契约测试互钉（test_artifacts_api）。 */
const KIND_META: Record<string, { label: string; icon: typeof Film }> = {
  animation: { label: '教学动画', icon: Film },
  slides: { label: '课件', icon: Presentation },
  doc: { label: '文档', icon: FileText },
  structure: { label: '结构图', icon: ImageIcon },
  table: { label: '数据表', icon: Table2 },
}

interface VersionInfo {
  seq: number
  url: string
  note?: string | null
  created_at?: string | null
}

export function ArtifactPanel({
  open,
  onToggle,
  focus,
  artifacts,
  attachments,
}: {
  open: boolean
  onToggle: () => void
  /** 外部联动（工件卡/上传卡点击）；对象引用变化即重新聚焦 */
  focus: PanelFocus | null
  /** 会话内出现的工件（artifact 事件实时追加，ChatPage 持有） */
  artifacts: ArtifactRef[]
  /** 会话内的图片上传（上传事件实时追加） */
  attachments: Attachment[]
}) {
  const [tab, setTab] = useState<PanelTab>('artifact')
  const [activeArtifact, setActiveArtifact] = useState<ArtifactRef | null>(null)
  const [versions, setVersions] = useState<VersionInfo[]>([])
  const [viewSeq, setViewSeq] = useState<number | null>(null)
  const [activeAttachment, setActiveAttachment] = useState<string | null>(null)
  /** 历史恢复卡片点开时从详情接口拉回的工件（props.artifacts 只含实时事件） */
  const [extraArtifacts, setExtraArtifacts] = useState<ArtifactRef[]>([])
  /** blob URL 缓存：img/iframe 子资源请求带不上 Authorization header（P0 实锤），
   * 统一经 fetchBlob（带 header）转 blob: 再喂给 src。key = 取数路径。 */
  const [blobUrls, setBlobUrls] = useState<Record<string, string>>({})
  /** focus 的序号：每次联动聚焦自增，解决「闭包读到旧 activeArtifact」的竞态
   * （面板关着点旧工件卡 → focus 先选中它 → 自动选中 effect 又覆盖成最新）。 */
  const focusSeq = useRef(0)

  /* 联动聚焦：工件卡点开 → 工件 tab；上传卡点开 → 附件 tab。
   * focusSeq 递增标记「这次聚焦是用户点出来的」，优先级高于自动选中——
   * 否则面板关着点旧工件卡，会被同一 commit 里的自动选中覆盖成最新工件。 */
  useEffect(() => {
    if (!focus) return
    focusSeq.current += 1
    const mySeq = focusSeq.current
    setTab(focus.tab)
    if (focus.tab === 'attachments' && focus.targetId) {
      setActiveAttachment(focus.targetId)
    }
    if (focus.tab === 'artifact' && focus.targetId) {
      /* 数组里同一工件可能有多个版本 ref（每次保存追加一条），取 version_seq
       * 最大的那条——点旧卡也看到它有过的最新版，v2 事件到达后不再回退 v1。 */
      const art = artifacts
        .filter((a) => a.artifact_id === focus.targetId)
        .sort((a, b) => b.version_seq - a.version_seq)[0]
      if (art) {
        selectArtifact(art)
        return
      }
      /* 点的是历史恢复的卡片、面板数组里没有 → 从详情接口拉，落到本地 extra
       * （面板不改 ChatPage 的 state，拉到的工件只进自己的展示列表）（P1-7） */
      api.get<{ artifact_id: string; kind: string; title: string; current_version: number; versions: { seq: number; url: string }[] }>(`/api/artifacts/${focus.targetId}`)
        .then((d) => {
          const cur = d.versions?.find((v) => v.seq === d.current_version)
          const fetched: ArtifactRef = {
            artifact_id: d.artifact_id, kind: d.kind, title: d.title,
            version_seq: d.current_version,
            url: cur?.url || '',
          }
          if (focusSeq.current === mySeq) {
            setExtraArtifacts((prev) =>
              prev.some((a) => a.artifact_id === fetched.artifact_id) ? prev : [...prev, fetched])
            selectArtifact(fetched)
          }
        })
        .catch(() => { /* 详情拿不到（已删/越权）保持空态 */ })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus])

  const selectArtifact = useCallback((art: ArtifactRef) => {
    setActiveArtifact(art)
    setViewSeq(art.version_seq)
    setVersions([{ seq: art.version_seq, url: art.url }])
    /* 版本历史从详情接口补全（实时事件只带当前版本引用） */
    api.get<{ versions: VersionInfo[]; current_version: number }>(`/api/artifacts/${art.artifact_id}`)
      .then((d) => {
        setVersions(d.versions || [])
      })
      .catch(() => { /* 版本列表拿不到不影响预览（当前版本已入列） */ })
  }, [])

  /* 会话内新工件事件到达 → 若正是当前选中的工件出了新版，跟随新版（v2 自动顶掉 v1）；
   * 是别的新工件则只在用户没手动选别的时切过去。均不打断手动点开的旧版本浏览。 */
  useEffect(() => {
    if (!open || tab !== 'artifact' || !artifacts.length) return
    const latest = artifacts[artifacts.length - 1]
    if (!activeArtifact) {
      selectArtifact(latest)
    } else if (latest.artifact_id === activeArtifact.artifact_id
               && latest.version_seq > activeArtifact.version_seq) {
      selectArtifact(latest)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [artifacts.length, open, tab])

  const viewingUrl =
    activeArtifact && viewSeq != null
      ? `/api/artifacts/${activeArtifact.artifact_id}/v/${viewSeq}`
      : null

  /* 取数 → blob URL（P0）：img/iframe 带不上 Authorization header，
   * fetch 能带。请求路径为 key 缓存，同一路径只取一次。 */
  useEffect(() => {
    const paths = [
      ...(activeAttachment
        ? [(`/api/agent/upload/${activeAttachment}/image` as string)]
        : []),
      ...(viewingUrl ? [viewingUrl] : []),
    ]
    for (const p of paths) {
      if (blobUrls[p]) continue
      const token = localStorage.getItem('token') || ''
      fetch(p, { headers: { Authorization: `Bearer ${token}` } })
        .then((resp) => {
          if (!resp.ok) throw new Error(String(resp.status))
          return resp.blob()
        })
        .then((blob) => {
          setBlobUrls((prev) => ({ ...prev, [p]: URL.createObjectURL(blob) }))
        })
        .catch(() => { /* 取数失败保持无 src，不渲染裂图 */ })
    }
  }, [activeAttachment, viewingUrl, blobUrls])

  if (!open) return null

  /* 展示列表 = 实时事件工件 + 历史恢复拉回的工件（按 id 去重，取最大版本号那条） */
  const mergedArtifacts = dedupeArtifacts([...artifacts, ...extraArtifacts])

  return (
    <div className="flex flex-col w-[400px] shrink-0 bg-white/70 backdrop-blur-md border border-white/90 rounded-[var(--radius-lg)] shadow-[var(--shadow-sm)] overflow-hidden">
      {/* 头部：标题 + 收缩 */}
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border">
        <span className="text-sm font-medium text-ink">工件面板</span>
        <button
          onClick={onToggle}
          title="收起面板"
          aria-label="收起工件面板"
          className="ml-auto p-1.5 rounded-[var(--radius-sm)] text-ink-secondary hover:bg-surface-muted cursor-pointer"
        >
          <PanelRightClose size={16} />
        </button>
      </div>

      {/* tab 行 */}
      <div className="flex gap-1 px-3 py-2 border-b border-border">
        {(
          [
            ['attachments', '附件', Paperclip, attachments.length],
            ['artifact', '工件', Sparkles, mergedArtifacts.length],
            ['versions', '版本', History, versions.length],
          ] as const
        ).map(([key, label, Icon, count]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            data-testid={`panel-tab-${key}`}
            className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-sub cursor-pointer transition-colors ${
              tab === key ? 'bg-primary-soft text-primary font-medium' : 'text-ink-secondary hover:bg-surface-muted'
            }`}
          >
            <Icon size={13} />
            {label}
            {count > 0 && (
              <span className="chem-mono text-micro text-ink-muted">{count}</span>
            )}
          </button>
        ))}
      </div>

      {/* 内容区 */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3">
        {tab === 'attachments' && (
          <AttachmentsTab
            attachments={attachments}
            activeId={activeAttachment}
            onSelect={setActiveAttachment}
            blobUrls={blobUrls}
          />
        )}
        {tab === 'artifact' && (
          <ArtifactTab
            artifacts={mergedArtifacts}
            activeId={activeArtifact?.artifact_id ?? null}
            onSelect={selectArtifact}
            frameSrc={viewingUrl ? blobUrls[viewingUrl] ?? null : null}
            kind={activeArtifact?.kind ?? ''}
            activeLabel={activeArtifact?.title || null}
          />
        )}
        {tab === 'versions' && (
          <VersionsTab
            versions={versions}
            viewSeq={viewSeq}
            onView={(seq) => setViewSeq(seq)}
            frameSrc={viewingUrl ? blobUrls[viewingUrl] ?? null : null}
            kind={activeArtifact?.kind ?? ''}
            activeLabel={activeArtifact?.title || null}
          />
        )}
      </div>
    </div>
  )
}

/** 按 artifact_id 去重，保留 version_seq 最大的 ref（多版本事件追加会产生同 id 条目，
 * 直接渲染会撞 React key，且旧 seq 会把 viewSeq 拉回去）。 */
export function dedupeArtifacts(list: ArtifactRef[]): ArtifactRef[] {
  const byId = new Map<string, ArtifactRef>()
  for (const a of list) {
    const cur = byId.get(a.artifact_id)
    if (!cur || a.version_seq > cur.version_seq) byId.set(a.artifact_id, a)
  }
  return [...byId.values()]
}

function AttachmentsTab({
  attachments, activeId, onSelect, blobUrls,
}: {
  attachments: Attachment[]
  activeId: string | null
  onSelect: (id: string) => void
  blobUrls: Record<string, string>
}) {
  if (!attachments.length) {
    return <Empty text="这个会话还没有上传附件。点输入框 + 上传答题卡或试卷图片。" />
  }
  const active = attachments.find((a) => a.upload_id === activeId) ?? attachments[attachments.length - 1]
  const blobSrc = active?.image_url ? blobUrls[active.image_url] : undefined
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {attachments.map((a) => (
          <button
            key={a.upload_id}
            onClick={() => onSelect(a.upload_id)}
            className={`px-2 py-1 rounded-[var(--radius-sm)] text-xs cursor-pointer border transition-colors ${
              a.upload_id === active?.upload_id
                ? 'border-teal text-teal bg-teal-soft'
                : 'border-border text-ink-secondary hover:border-teal'
            }`}
          >
            {a.file_name}
          </button>
        ))}
      </div>
      {active?.image_url ? (
        blobSrc
          ? <img src={blobSrc} alt={`上传原图 ${active.file_name}`} data-testid="attachment-image"
              className="w-full rounded-[var(--radius-md)] border border-border" />
          : <Empty text="原图加载中…" />
      ) : (
        <Empty text="这件附件没有可预览的原图（PDF/Word 请下载查看，这里只显示 OCR 文本卡）。" />
      )}
    </div>
  )
}

function ArtifactTab({
  artifacts, activeId, onSelect, frameSrc, kind, activeLabel,
}: {
  artifacts: ArtifactRef[]
  activeId: string | null
  onSelect: (a: ArtifactRef) => void
  frameSrc: string | null
  kind: string
  activeLabel: string | null
}) {
  if (!artifacts.length) {
    return <Empty text="让 AI 做个动画/课件/文档，产物会出现在这里实时预览。" />
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-1.5">
        {artifacts.map((a) => {
          const km = KIND_META[a.kind] || { label: '工件' }
          return (
            <button
              key={a.artifact_id}
              onClick={() => onSelect(a)}
              className={`px-2 py-1 rounded-[var(--radius-sm)] text-xs cursor-pointer border transition-colors ${
                a.artifact_id === activeId
                  ? 'border-teal text-teal bg-teal-soft'
                  : 'border-border text-ink-secondary hover:border-teal'
              }`}
            >
              {a.title || km.label} · v{a.version_seq}
            </button>
          )
        })}
      </div>
      {frameSrc
        ? <SandboxFrame src={frameSrc} kind={kind} title={activeLabel} />
        : <Empty text="预览加载中…" />}
    </div>
  )
}

function VersionsTab({
  versions, viewSeq, onView, frameSrc, kind, activeLabel,
}: {
  versions: VersionInfo[]
  viewSeq: number | null
  onView: (seq: number) => void
  frameSrc: string | null
  kind: string
  activeLabel: string | null
}) {
  if (!versions.length) {
    return <Empty text="选中一个工件后，它的版本历史在这里；点任意版本回看快照。" />
  }
  return (
    <div className="space-y-3">
      <div className="space-y-1">
        {versions.map((v) => (
          <button
            key={v.seq}
            onClick={() => onView(v.seq)}
            className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-[var(--radius-sm)] text-left text-sub cursor-pointer transition-colors ${
              v.seq === viewSeq ? 'bg-primary-soft text-primary font-medium' : 'text-ink-secondary hover:bg-surface-muted'
            }`}
          >
            <span className="chem-mono">v{v.seq}</span>
            <span className="flex-1 truncate">{v.note || '修订保存'}</span>
          </button>
        ))}
      </div>
      {frameSrc
        ? <SandboxFrame src={frameSrc} kind={kind} title={activeLabel} />
        : <Empty text="预览加载中…" />}
    </div>
  )
}

/** 沙箱预览框（spec R7）：allow-scripts、禁同源、外链资源由工件自包含规范约束。
 * src 是 blob: URL——鉴权在面板的 fetch 里完成（子资源请求带不上 header）。 */
function SandboxFrame({ src, kind, title }: { src: string; kind: string; title?: string | null }) {
  return (
    <iframe
      data-testid="artifact-frame"
      src={src}
      sandbox="allow-scripts"
      title={`工件预览（${title || KIND_META[kind]?.label || kind}）`}
      className="w-full h-[480px] rounded-[var(--radius-md)] border border-border bg-white"
    />
  )
}

function Empty({ text }: { text: string }) {
  return (
    <div className="text-center text-xs text-ink-muted py-10 px-4 leading-relaxed">{text}</div>
  )
}
