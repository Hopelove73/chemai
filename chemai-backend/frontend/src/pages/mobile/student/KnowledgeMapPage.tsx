/* KnowledgeMapPage — 知识地图（S6 / REQ-KMV-002..004）。
 * 拓扑分层卡式列表（不用力导向 canvas 图）；三态着色（锁定/就绪/精通）+
 * FSRS 褪色态；节点点击 → 详情面板（前置/绑定工件「只读已分享版本」/
 * 「练习此点」定向智能练习）；冷启动引导态。 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Lock, PlayCircle, RotateCcw, ChevronRight, Sparkles,
} from 'lucide-react'
import { api } from '../../../lib/api'
import { getMobileUser } from '../../../lib/mobile-auth'
import { MobileLayout, MobileHeader, MobileTabBar, MobileLoading, MobileEmpty, MobileFullPane, STUDENT_TABS } from '../../../components/mobile/MobileLayout'
import { useReviewDue } from '../../../lib/use-review-due'

interface MapData {
  states: Record<string, string>
  mastery: Record<string, number>
  layers: string[][]
  edges: { from: string; to: string }[]
  bindings: { kp: string; artifact_id: string }[]
  faded: string[]
  cold_start: boolean
}

const STATE_LABEL: Record<string, string> = {
  mastered: '已精通',
  ready: '当前可学',
  locked: '未解锁',
}

/** 知识点气泡图（2026-10 新增）——
 * 原来的「知识地图」是按状态分组的列表，`edges=0`（老师还没确认先修关系）时根本画不出图。
 * 这里给一个**不依赖边也能成图**的呈现：三条状态带（待复习 / 可学 / 已精通）横排气泡，
 * 半径随掌握度、颜色随状态，点任意气泡看详情。真有了 edges 之后，
 * 上面再叠连线就是完整路径图；现在至少是「图」而不是「表」。 */
function KpBubbleMap({ data, onPick }: { data: MapData; onPick: (kp: string) => void }) {
  const bands: { key: string; label: string; color: string; test: (kp: string) => boolean }[] = [
    { key: 'faded', label: '有遗忘 · 待复习', color: 'var(--color-error)',
      test: (kp) => (data.faded || []).includes(kp) },
    { key: 'ready', label: '当前可学', color: 'var(--color-primary)',
      test: (kp) => data.states[kp] === 'ready' && !(data.faded || []).includes(kp) },
    { key: 'mastered', label: '已精通', color: 'var(--color-success)',
      test: (kp) => data.states[kp] === 'mastered' },
  ]
  const all = Array.from(new Set([
    ...Object.keys(data.states || {}),
    ...Object.keys(data.mastery || {}),
  ]))
  const grouped = bands.map((b) => ({ ...b, items: all.filter(b.test) }))
  const rest = all.filter((kp) => !grouped.some((g) => g.items.includes(kp)))
  if (rest.length) grouped.push({ key: 'other', label: '待解锁', color: 'var(--color-ink-muted)', test: () => false, items: rest })

  const W = 396
  const ROW_W = W - 16
  let y = 8
  const rows: { x: number; y: number; r: number; kp: string; label: string; color: string }[] = []
  grouped.forEach((g) => {
    if (!g.items.length) return
    let x = 12
    let rowH = 0
    g.items.forEach((kp) => {
      const m = Number(data.mastery[kp] || 0)
      const r = 6 + Math.min(10, Math.round(m * 18))
      /* 排布宽度必须按「标签宽度 vs 直径」取大者：只按直径算的话圆不重叠、
         但 81 个知识点的中文标签会互相压在一起（2026-10 实测长这样）。
         标签按 5 字截断，字号 7px，每字约 7px → 估算宽 = 字数*7 + 6。 */
      const label = kp.length > 5 ? kp.slice(0, 5) + '…' : kp
      const labelW = label.length * 7 + 6
      const w = Math.max(r * 2 + 8, labelW) + 6
      if (x + w > 12 + ROW_W) { x = 12; y += rowH + 14; rowH = 0 }
      rows.push({ x: x + Math.max(r, labelW / 2), y: y + r, r, kp, label, color: g.color })
      x += w
      rowH = Math.max(rowH, r * 2 + 10)
    })
    y += rowH + 28
  })
  const H = y + 8
  /* 真连线（2026-10）：老师采纳先修边之后 data.edges 不再是空数组，这里把
     两端都出现在图上的边画出来。只有真实边才画 —— 提议阶段（edges=[]）不连线。 */
  const pos = new Map(rows.map((n) => [n.kp, n]))
  const edgeLines = (data.edges || [])
    .map((e) => ({ a: pos.get(e.from), b: pos.get(e.to) }))
    .filter((p): p is { a: NonNullable<typeof p.a>; b: NonNullable<typeof p.b> } => !!p.a && !!p.b)

  return (
    <div className="rounded-lg bg-surface border border-border/60 p-2.5">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[12.5px] font-semibold text-ink">知识点分布图</span>
        <span className="text-micro text-ink-muted">气泡越大 = 掌握度越高</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto" role="img" aria-label="知识点分布气泡图">
        {edgeLines.map((p, i) => (
          <line key={'e' + i} x1={p.a.x} y1={p.a.y} x2={p.b.x} y2={p.b.y}
                stroke="var(--color-border-strong)" strokeWidth={0.8} strokeOpacity={0.55} />
        ))}
        {rows.map((n) => (
          <g key={n.kp} onClick={() => onPick(n.kp)} style={{ cursor: 'pointer' }}>
            <circle cx={n.x} cy={n.y} r={n.r} fill={n.color} fillOpacity={0.22} stroke={n.color} strokeWidth={1.2} />
            <text x={n.x} y={n.y + n.r + 10} textAnchor="middle" fontSize="7" fill="var(--color-ink-secondary)">
              {n.label}
            </text>
          </g>
        ))}
      </svg>
      <div className="flex flex-wrap gap-3 mt-1">
        {grouped.map((g) => (
          <span key={g.key} className="flex items-center gap-1 text-micro text-ink-secondary">
            <span className="w-2 h-2 rounded-full inline-block" style={{ background: g.color }} />
            {g.label} {g.items.length}
          </span>
        ))}
      </div>
    </div>
  )
}
export function KnowledgeMapPage() {
  const navigate = useNavigate()
  const [user] = useState(() => getMobileUser())
  const dueCount = useReviewDue(user?.id)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [data, setData] = useState<MapData | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  const sid = user?.id || ''

  const load = useCallback(() => {
    if (!sid) return
    setLoading(true)
    api.get<MapData>(`/api/knowledge-map/student/${encodeURIComponent(sid)}`)
      .then((d) => {
        setData(d)
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
  }, [sid])

  useEffect(() => {
    document.title = '知识地图'
    load()
  }, [load])

  /* 「练习此点」只带参转场：开轮由 PracticePage 统一执行
   *（此前本页先 start 再转场，转场后 PracticePage 又 start——双开会话，
   *  旧记录悬挂且全班可见。捉虫 P2-5） */
  const startSmartAt = useCallback((kp: string) => {
    navigate('/m/practice', { state: { smartKp: kp } })
  }, [navigate])

  const bindingOf = (kp: string) => data?.bindings.find((b) => b.kp === kp)
  const prereqsOf = (kp: string) =>
    (data?.edges || []).filter((e) => e.to === kp).map((e) => e.from)

  const tabbar = (
    <MobileTabBar
      tabs={STUDENT_TABS.map((t) => ({
        id: t.id,
        label: t.label,
        icon: t.icon,
        onClick: () => navigate(t.path || '/m'),
        active: false,
        badge: t.id === 'review' ? dueCount : undefined,
      }))}
    />
  )

  const nodeCls = (kp: string) => {
    const st = data?.states[kp] || 'ready'
    const faded = data?.faded.includes(kp)
    if (st === 'mastered') return 'border-primary bg-primary text-on-primary'
    if (st === 'locked') return 'border-border bg-surface-muted text-ink-muted'
    return faded
      ? 'border-dashed border-warning/50 bg-warning-soft text-warning'
      : 'border-primary/50 bg-primary-soft text-primary'
  }

  return (
    <MobileLayout
      theme="student"
      header={<MobileHeader title="知识地图" />}
      footer={tabbar}
    >
      {loading && <MobileLoading />}
      {!loading && error && (
        <MobileEmpty title="加载失败" hint={error} action={
          <button onClick={load} className="px-5 py-2 rounded-full bg-primary text-on-primary text-sub font-bold">重试</button>
        } />
      )}

      {/* 冷启动提示（2026-10 调整）：原来 cold_start=true 会把**整张图**吞掉，只剩
          「老师还没建立知识点关系」——可接口明明返回了 81 个知识点状态、48 个掌握度和
          一整层知识点，图是画得出来的，缺的只是老师确认的先修边（edges=0）。
          现在冷启动只当提示条，图照常渲染：没边就按层内状态分组展示，有边才画路径。 */}
      {!loading && data?.cold_start && (
        <div className="mx-3.5 mt-3 rounded-lg border border-warning/40 bg-warning-soft px-3.5 py-2.5">
          <p className="text-[12.5px] text-ink leading-relaxed">
            先修关系还没由老师确认，下面的知识点按「已掌握 / 可学 / 待解锁」分组展示；
            确认后会自动连成学习路径。
          </p>
        </div>
      )}

      {!loading && data && (
        <div className="p-3.5 space-y-4">
          <KpBubbleMap data={data} onPick={(kp) => setSelected(kp)} />
          {data.layers.map((layer, li) => {
            /* 层内按状态归组：精通在上、可学居中、锁定垫底（卡式列表，430px 友好） */
            const mastered = layer.filter((kp) => data.states[kp] === 'mastered')
            const ready = layer.filter((kp) => data.states[kp] === 'ready')
            const locked = layer.filter((kp) => data.states[kp] === 'locked')
            const groups: [string, string[], typeof Lock | null][] = [
              ['已精通', mastered, null],
              ['当前可学', ready, null],
              ['未解锁', locked, Lock],
            ]
            return (
              <div key={li} className="space-y-2">
                {groups.map(([label, kps, Icon]) => kps.length === 0 ? null : (
                  <div key={label}>
                    <div className="flex items-center gap-1.5 text-micro text-ink-muted mb-1.5">
                      {Icon ? <Icon size={11} /> : <Sparkles size={11} className="text-ink" />}
                      {label} · {kps.length}
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {kps.map((kp) => {
                        const bind = bindingOf(kp)
                        const faded = data.faded.includes(kp)
                        return (
                          <button
                            key={kp}
                            onClick={() => setSelected(kp)}
                            className={`text-left p-3 rounded-xl border text-[12.5px] leading-snug transition-colors ${nodeCls(kp)}`}
                          >
                            <span className="line-clamp-2 break-words">{kp}</span>
                            <span className="block text-[10px] mt-1 opacity-80">
                              {faded ? '有遗忘 · 待复习' : STATE_LABEL[data.states[kp]]}
                              {bind ? ' · 有讲解' : ''}
                            </span>
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            )
          })}
          <p className="text-[10px] text-ink-muted text-center pb-2">
            地图由老师依据你的学情确认先修关系后生成
          </p>
        </div>
      )}

      {/* 节点详情面板 */}
      <MobileFullPane
        open={!!selected}
        title={selected || ''}
        onClose={() => setSelected(null)}
      >
        {selected && data && (() => {
          const st = data.states[selected] || 'ready'
          const m = data.mastery[selected]
          const bind = bindingOf(selected)
          const pres = prereqsOf(selected)
          return (
            <div className="space-y-3 text-sub">
              <div className="p-3.5 surface-card space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className={`text-[12px] px-2 py-0.5 rounded-full ${nodeCls(selected)}`}>
                    {STATE_LABEL[st]}
                  </span>
                  {data.faded.includes(selected) && (
                    <span className="text-micro text-warning flex items-center gap-1">
                      <RotateCcw size={10} />有遗忘
                    </span>
                  )}
                </div>
                <p className="text-ink-secondary">
                  掌握度：{m != null ? `${Math.round(m * 100)}%` : '数据不足（多练几题后出结论）'}
                </p>
                {pres.length > 0 && (
                  <p className="text-ink-secondary text-[12px]">
                    前置：{pres.join('、')}
                  </p>
                )}
              </div>

              {bind && (
                <button
                  onClick={() => navigate(`/m/artifact/${bind.artifact_id}`)}
                  className="w-full p-3.5 rounded-xl bg-surface border border-teal/40 flex items-center gap-2 text-left"
                >
                  <Sparkles size={15} className="text-teal" />
                  <span className="flex-1 text-sub text-ink">老师为本知识点准备的讲解</span>
                  <ChevronRight size={14} className="text-ink-muted" />
                </button>
              )}

              {st !== 'locked' ? (
                <button
                  onClick={() => startSmartAt(selected)}
                  className="w-full py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold flex items-center justify-center gap-2"
                >
                  <PlayCircle size={16} />
                  练习此点（定向智能练习）
                </button>
              ) : (
                <div className="p-3 rounded-xl bg-surface-muted/60 text-[12px] text-ink-muted flex items-start gap-2">
                  <Lock size={13} className="shrink-0 mt-0.5" />
                  先掌握前置知识点：{pres.join('、')}
                </div>
              )}
            </div>
          )
        })()}
      </MobileFullPane>
    </MobileLayout>
  )
}
