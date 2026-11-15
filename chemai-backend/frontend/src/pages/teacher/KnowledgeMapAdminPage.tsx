/* KnowledgeMapAdminPage — 知识地图（S6 / REQ-KMV-005 + star-map-teacher）。
 * 星图视图（默认）：react-force-graph-3d 深底星空 —— 亮度=班级掌握度、
 * 大小=作答量、无可信样本=灰星，6 册聚为星系团；点星下钻（档案/薄弱学生/
 * 先修上下文）→ 面板内出变式题（跳工作台走确认门，星图零写操作）。
 * 列表视图：先修边管理（启停/采纳，KB-T12 提议队列）。
 * 星图数据源 = 班级聚合 /class/{id}/graph（口径同全局 kb_error_rate）。 */

import { useCallback, useEffect, useState } from 'react'
import { Map as MapIcon, RefreshCw } from 'lucide-react'
import { api } from '../../lib/api'
import { Button, Card, EmptyState, Tabs, useToast } from '../../components/ui'
import { StarMap } from '../../components/teacher/StarMap'
import type { StarGraphEdge, StarGraphNode } from '../../components/teacher/StarMap'
import { StarDrilldownPanel } from '../../components/teacher/StarDrilldownPanel'

interface Edge {
  id: number
  from_kp: string
  to_kp: string
  status: string
  reason: string | null
}

interface ClassInfo { class_id?: string; name?: string }

export function KnowledgeMapAdminPage() {
  const [view, setView] = useState<'graph' | 'list'>('graph')
  const [edges, setEdges] = useState<Edge[] | null>(null)
  const [busy, setBusy] = useState<Record<number, boolean>>({})
  const [rejected, setRejected] = useState<Record<number, string>>({})
  const toast = useToast()

  const [classes, setClasses] = useState<ClassInfo[]>([])
  const [classId, setClassId] = useState<string>('')
  const [star, setStar] = useState<{ nodes: StarGraphNode[]; edges: StarGraphEdge[]; class_name: string } | null>(null)
  const [selectedKp, setSelectedKp] = useState<string | null>(null)
  /* 全校共享星图坐标（REQ-SMI-006）：null = 加载中，加载完才挂星图（先固定再布局） */
  const [layout, setLayout] = useState<Record<string, [number, number, number]> | null>(null)
  useEffect(() => {
    api.get<{ layout?: Record<string, [number, number, number]> }>('/api/knowledge-map/layout')
      .then((d) => setLayout(d.layout || {}))
      .catch(() => setLayout({}))
  }, [])
  const saveLayout = useCallback((points: { kp_id: string; x: number; y: number; z: number }[]) => {
    api.post('/api/knowledge-map/layout', points).catch(() => { /* 存失败不影响本次显示 */ })
  }, [])

  const load = useCallback(() => {
    api.get<{ edges?: Edge[] }>('/api/knowledge-map/teacher/edges')
      .then((d) => setEdges(d.edges || []))
      .catch(() => setEdges([]))
  }, [])

  const loadClasses = useCallback(() => {
    api.get<{ classes?: ClassInfo[] }>('/api/classes')
      .then((d) => {
        const list = (d.classes || []).filter((c) => c.class_id)
        setClasses(list)
        setClassId((prev) => prev || list[0]?.class_id || '')
      })
      .catch(() => setClasses([]))
  }, [])

  const loadStar = useCallback(() => {
    if (!classId) {
      setStar({ nodes: [], edges: [], class_name: '' })
      return
    }
    api.get<{ nodes?: StarGraphNode[]; edges?: StarGraphEdge[]; class_name?: string }>(
      `/api/knowledge-map/class/${classId}/graph`,
    )
      .then((d) => setStar({
        nodes: d.nodes || [], edges: d.edges || [], class_name: d.class_name || '',
      }))
      .catch(() => setStar({ nodes: [], edges: [], class_name: '' }))
  }, [classId])

  useEffect(() => {
    document.title = '知识星图'
    load()
    loadClasses()
  }, [load, loadClasses])

  useEffect(() => {
    if (view === 'graph') loadStar()
  }, [view, classId, loadStar])

  const refreshAll = () => {
    load()
    if (view === 'graph') loadStar()
  }

  const toggle = async (edge: Edge) => {
    setBusy((prev) => ({ ...prev, [edge.id]: true }))
    try {
      await api.put(`/api/knowledge-map/edges/${edge.id}/status`, {
        status: edge.status === 'active' ? 'disabled' : 'active',
      })
      toast.success(edge.status === 'active' ? '已禁用，地图即时生效' : '已启用')
      refreshAll()
    } catch {
      toast.error('操作失败')
    } finally {
      setBusy((prev) => ({ ...prev, [edge.id]: false }))
    }
  }

  /* KB-T12：采纳提议（proposed → active）。成环后端 409 带环路径，
     现场显示在边上——老师能看到"这条为什么不能收"。 */
  const accept = async (edge: Edge) => {
    setBusy((prev) => ({ ...prev, [edge.id]: true }))
    try {
      await api.put(`/api/knowledge-map/edges/${edge.id}/accept`, {})
      toast.success('已采纳，先修链即时生效')
      setRejected((prev) => ({ ...prev, [edge.id]: '' }))
      refreshAll()
    } catch (err) {
      const detail = (err as { detail?: string; message?: string })?.detail
        || (err as Error)?.message || '采纳失败'
      setRejected((prev) => ({ ...prev, [edge.id]: detail }))
      toast.error(detail)
    } finally {
      setBusy((prev) => ({ ...prev, [edge.id]: false }))
    }
  }

  const active = (edges || []).filter((e) => e.status === 'active')
  const proposed = (edges || []).filter((e) => e.status === 'proposed')
  const disabled = (edges || []).filter((e) => e.status !== 'active' && e.status !== 'proposed')
  const selectedNode = star?.nodes.find((n) => n.kp_id === selectedKp) || null

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-4">
      <div className="flex items-center gap-3">
        <span className="w-9 h-9 rounded-[var(--radius-md)] bg-teal-soft flex items-center justify-center">
          <MapIcon size={18} className="text-teal" />
        </span>
        <div className="flex-1">
          <h1 className="text-lg font-bold text-ink">知识星图</h1>
          <p className="text-sm text-ink-secondary mt-0.5">
            点星看档案；先修边的采纳与启停在列表视图
          </p>
        </div>
        <Button size="sm" variant="secondary" onClick={refreshAll}>
          <RefreshCw size={13} />刷新
        </Button>
      </div>

      <Tabs
        variant="segmented"
        items={[
          { key: 'graph', label: '图谱视图' },
          { key: 'list', label: `列表管理${proposed.length > 0 ? `（待采纳 ${proposed.length}）` : ''}` },
        ]}
        active={view}
        onChange={(k) => setView(k as 'graph' | 'list')}
      />

      {view === 'graph' ? (
        star === null || layout === null ? (
          <Card><p className="text-sm text-ink-muted">星图加载中…</p></Card>
        ) : star.nodes.length === 0 ? (
          <EmptyState
            illustration={<MapIcon size={32} />}
            title={classes.length === 0 ? '暂无任教班级' : '图谱为空'}
            description={classes.length === 0
              ? '星图按班级聚合学情；请联系管理员确认班级与任教关系'
              : '先在词典页同步知识点，再摄取文档建立图谱'}
          />
        ) : (
          <div className="space-y-2.5">
            <div className="flex items-center gap-3">
              {classes.length > 1 ? (
                <select
                  value={classId}
                  onChange={(e) => { setSelectedKp(null); setClassId(e.target.value) }}
                  className="px-3 py-1.5 rounded-[var(--radius-md)] border border-border bg-surface text-sm text-ink cursor-pointer"
                  aria-label="切换班级"
                >
                  {classes.map((c) => (
                    <option key={c.class_id} value={c.class_id}>{c.name}</option>
                  ))}
                </select>
              ) : (
                <span className="text-sm text-ink-secondary">{star.class_name || classes[0]?.name}</span>
              )}
              <span className="text-xs text-ink-muted">
                亮度 = 班级掌握度 · 星越大作答越多 · 点暗星下钻，面板内直接出变式题
              </span>
            </div>
            {/* 下钻面板作为 StarMap 的叠加层传入：全屏时随画布一起进入全屏层 */}
            <StarMap nodes={star.nodes} edges={star.edges} onSelect={setSelectedKp} selectedKp={selectedKp} layout={layout} onLayoutSettled={saveLayout} onReset={() => setSelectedKp(null)}>
              {selectedNode && (
                <StarDrilldownPanel
                  node={selectedNode}
                  nodes={star.nodes}
                  edges={star.edges}
                  classId={classId}
                  onClose={() => setSelectedKp(null)}
                />
              )}
            </StarMap>
          </div>
        )
      ) : edges === null ? (
        <Card><p className="text-sm text-ink-muted">加载中…</p></Card>
      ) : edges.length === 0 ? (
        <EmptyState
          illustration={<MapIcon size={32} />}
          title="还没有先修关系"
          description="知识库文档审校后提议边会出现在这里；也可以去 Agent 工作台说「帮我提议知识点先修关系」"
        />
      ) : (
        <>
          {proposed.length > 0 && (
            <Card>
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm font-semibold text-ink">待采纳 · {proposed.length}</h2>
                <span className="text-xs text-ink-muted">采纳后先修链即时生效（成环的会被拒绝并说明）</span>
              </div>
              <div className="space-y-2">
                {proposed.map((e) => (
                  <div key={e.id}>
                    <div className="flex items-center gap-3 p-3 rounded-lg border border-warning/40 bg-warning-soft">
                      <div className="flex-1 min-w-0 text-sm">
                        <span className="font-medium text-ink">{e.from_kp}</span>
                        <span className="text-ink-muted mx-1.5">→</span>
                        <span className="font-medium text-ink">{e.to_kp}</span>
                        {e.reason && (
                          <p className="text-xs text-ink-muted mt-0.5 truncate">依据：{e.reason}</p>
                        )}
                        {rejected[e.id] && (
                          <p className="text-xs text-error mt-0.5">⛔ {rejected[e.id]}</p>
                        )}
                      </div>
                      <Button
                        size="sm" variant="primary"
                        loading={busy[e.id]}
                        onClick={() => accept(e)}
                      >
                        采纳
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}
          <Card>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-ink">生效中 · {active.length}</h2>
              <span className="text-xs text-ink-muted">禁用即时退出学生地图的就绪过滤</span>
            </div>
            {active.length === 0 ? (
              <p className="text-sm text-ink-muted">暂无生效边</p>
            ) : (
              <div className="space-y-2">
                {active.map((e) => (
                  <EdgeRow key={e.id} edge={e} busy={busy[e.id]} onToggle={toggle} />
                ))}
              </div>
            )}
          </Card>
          {disabled.length > 0 && (
            <Card>
              <h2 className="text-sm font-semibold text-ink mb-3">已禁用 · {disabled.length}</h2>
              <div className="space-y-2">
                {disabled.map((e) => (
                  <EdgeRow key={e.id} edge={e} busy={busy[e.id]} onToggle={toggle} />
                ))}
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  )
}

/* ── 列表视图的行 ─────────────────────────────────────────────── */

function EdgeRow({ edge, busy, onToggle }: {
  edge: Edge
  busy?: boolean
  onToggle: (edge: Edge) => void
}) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg border border-border/70 bg-surface">
      <div className="flex-1 min-w-0 text-sm">
        <span className="font-medium text-ink">{edge.from_kp}</span>
        <span className="text-ink-muted mx-1.5">→</span>
        <span className="font-medium text-ink">{edge.to_kp}</span>
        {edge.reason && (
          <p className="text-xs text-ink-muted mt-0.5 truncate">依据：{edge.reason}</p>
        )}
      </div>
      <Button
        size="sm"
        variant={edge.status === 'active' ? 'secondary' : 'primary'}
        loading={busy}
        onClick={() => onToggle(edge)}
      >
        {edge.status === 'active' ? '禁用' : '启用'}
      </Button>
    </div>
  )
}
