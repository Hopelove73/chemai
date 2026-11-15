/* 我的工件（能力升级批次 A4）——老师跨会话的工件库列表页。
 *
 * 一个表 + 类型筛选 + 行内预览：工件真源在工作区盘上（A1），这里只消费
 * GET /api/artifacts 索引；预览取数与面板同款 blob 管道（img/iframe 子资源
 * 带不上 Authorization header）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Film, Presentation, FileText, Image as ImageIcon, Table2, Sparkles,
  RefreshCw, X,
} from 'lucide-react'
import { api } from '../../lib/api'

const KIND_META: Record<string, { label: string; icon: typeof Film }> = {
  animation: { label: '教学动画', icon: Film },
  slides: { label: '课件', icon: Presentation },
  doc: { label: '文档', icon: FileText },
  structure: { label: '结构图', icon: ImageIcon },
  table: { label: '数据表', icon: Table2 },
}

interface ArtifactRow {
  artifact_id: string
  kind: string
  title: string
  current_version: number
  updated_at: string | null
}

export function ArtifactsPage() {
  const [rows, setRows] = useState<ArtifactRow[]>([])
  const [kindFilter, setKindFilter] = useState<string>('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [previewing, setPreviewing] = useState<ArtifactRow | null>(null)
  const [frameSrc, setFrameSrc] = useState<string | null>(null)

  const load = useCallback(async (kind: string) => {
    setLoading(true)
    setError('')
    try {
      const d = await api.get<{ items: ArtifactRow[] }>(
        `/api/artifacts${kind ? `?kind=${encodeURIComponent(kind)}` : ''}`)
      setRows(d.items || [])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load(kindFilter) }, [kindFilter, load])

  /* 导出 .pptx（批次 A5 二等能力）：fetch 带 token 取二进制 → 下载。
   * 后端是大纲 deck（HTML 文字结构 → 标题页 + 要点页），交互/动画不在其中。 */
  const exportPptx = useCallback(async (row: ArtifactRow) => {
    const token = localStorage.getItem('token') || ''
    const resp = await fetch(`/api/artifacts/${row.artifact_id}/export`,
      { headers: { Authorization: `Bearer ${token}` } })
    if (!resp.ok) return
    const blob = await resp.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${row.title || row.artifact_id}.pptx`
    a.click()
    URL.revokeObjectURL(url)
  }, [])

  /* 预览取数：blob 管道（同 ArtifactPanel，P0 修复的同款口径） */
  useEffect(() => {
    if (!previewing) { setFrameSrc(null); return }
    const path = `/api/artifacts/${previewing.artifact_id}/v/${previewing.current_version}`
    const token = localStorage.getItem('token') || ''
    let revoke: string | null = null
    fetch(path, { headers: { Authorization: `Bearer ${token}` } })
      .then((resp) => {
        if (!resp.ok) throw new Error(String(resp.status))
        return resp.blob()
      })
      .then((blob) => {
        revoke = URL.createObjectURL(blob)
        setFrameSrc(revoke)
      })
      .catch(() => setFrameSrc(null))
    return () => { if (revoke) URL.revokeObjectURL(revoke) }
  }, [previewing])

  const kinds = useMemo(() => Object.keys(KIND_META), [])

  return (
    <div className="h-full overflow-y-auto p-6 max-w-5xl mx-auto">
      <div className="flex items-center gap-3 mb-5">
        <h1 className="text-xl font-semibold text-ink m-0">我的工件</h1>
        <button
          onClick={() => void load(kindFilter)}
          className="p-1.5 rounded-[var(--radius-sm)] text-ink-muted hover:text-teal hover:bg-surface-muted cursor-pointer"
          title="刷新"
        >
          <RefreshCw size={14} />
        </button>
        <span className="text-xs text-ink-muted ml-auto">
          在工作台对话里生成的动画、课件、文档都会进这里，跨会话可查。
        </span>
      </div>

      {/* 类型筛选 */}
      <div className="flex gap-1.5 mb-4 flex-wrap">
        <FilterChip active={kindFilter === ''} onClick={() => setKindFilter('')}>全部</FilterChip>
        {kinds.map((k) => {
          const Icon = KIND_META[k].icon
          return (
            <FilterChip key={k} active={kindFilter === k} onClick={() => setKindFilter(k)}>
              <Icon size={12} className="inline mr-1 -mt-0.5" />
              {KIND_META[k].label}
            </FilterChip>
          )
        })}
      </div>

      {loading ? (
        <div className="text-center text-sm text-ink-muted py-16">加载中…</div>
      ) : error ? (
        <div className="text-center text-sm text-error py-16">加载失败：{error}</div>
      ) : rows.length === 0 ? (
        <div className="text-center text-sm text-ink-muted py-16 leading-relaxed">
          还没有工件。去 Agent 工作台说一句「给盐类水解做个教学动画」，
          产物会自动进这里。
        </div>
      ) : (
        <div className="space-y-2" data-testid="artifact-rows">
          {rows.map((r) => {
            const meta = KIND_META[r.kind] || { label: '工件', icon: Sparkles }
            const Icon = meta.icon
            return (
              <button
                key={r.artifact_id}
                data-testid="artifact-row"
                onClick={() => setPreviewing(r)}
                className="w-full flex items-center gap-3 px-4 py-3 bg-white/80 border border-border
                           rounded-[var(--radius-md)] text-left cursor-pointer hover:border-teal
                           hover:shadow-[var(--shadow-sm)] transition-all"
              >
                <Icon size={16} className="text-teal shrink-0" />
                <span className="text-[14px] font-medium text-ink truncate flex-1">
                  {r.title || meta.label}
                </span>
                <span className="text-xs text-ink-muted shrink-0">{meta.label} · v{r.current_version}</span>
                <span className="text-xs text-ink-muted shrink-0 chem-mono">
                  {r.updated_at ? new Date(r.updated_at).toLocaleDateString() : ''}
                </span>
                {(r.kind === 'slides' || r.kind === 'doc') && (
                  <span
                    role="button"
                    data-testid={`export-${r.artifact_id}`}
                    onClick={(e) => { e.stopPropagation(); void exportPptx(r) }}
                    className="text-xs text-teal hover:underline shrink-0 px-2 py-1 cursor-pointer"
                    title="导出为 .pptx（大纲版：标题与文字要点）"
                  >
                    导出 PPT
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {/* 行内预览抽屉 */}
      {previewing && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8"
             onClick={() => setPreviewing(null)}>
          <div className="bg-white rounded-[var(--radius-lg)] w-full max-w-4xl h-full max-h-[90vh] flex flex-col overflow-hidden"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
              <span className="text-sm font-medium text-ink truncate">
                {previewing.title}（v{previewing.current_version}）
              </span>
              <button
                onClick={() => setPreviewing(null)}
                aria-label="关闭预览"
                className="ml-auto p-1.5 rounded-[var(--radius-sm)] text-ink-secondary hover:bg-surface-muted cursor-pointer"
              >
                <X size={16} />
              </button>
            </div>
            {frameSrc ? (
              <iframe
                data-testid="library-frame"
                src={frameSrc}
                sandbox="allow-scripts"
                title={`工件预览：${previewing.title}`}
                className="flex-1 w-full border-0"
              />
            ) : (
              <div className="flex-1 flex items-center justify-center text-sm text-ink-muted">预览加载中…</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function FilterChip({ active, onClick, children }: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded-full text-xs cursor-pointer border transition-colors ${
        active
          ? 'bg-primary text-white border-primary'
          : 'bg-white/70 text-ink-secondary border-border hover:border-teal hover:text-teal'
      }`}
    >
      {children}
    </button>
  )
}
