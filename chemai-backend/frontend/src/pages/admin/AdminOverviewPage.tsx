import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Users } from 'lucide-react'
import { api, can } from '../../lib/api'
import { Button, Card, EmptyState, SkeletonCard, StatCard, Tag } from '../../components/ui'
import { CHART_AXIS_TEXT, CHART_COLORS, EChart } from '../../components/ui/EChart'
import { classPoints, formatPercent, heatmapCells, scatterPoints, trendSeries } from '../../lib/overview-charts'

/* ── 契约（与 GET /api/admin/overview-charts 一致）────────── */

interface ChartsData {
  success: boolean
  data_scope_empty: boolean
  kpi?: {
    students: number; teachers: number; classes: number
    parent_bindings: number; parent_accounts: number
  }
  trend?: { granularity: string; data_until: string | null; points: Array<{ month: string; answers: number; accuracy: number | null }> }
  parent_funnel?: { bound: number; activated: number; active_7d: number | null; active_7d_available: boolean }
  class_compare?: Array<{ class_id: string; class_name: string; answers: number; accuracy: number | null; small_sample: boolean }>
  kp_heatmap?: { classes: Array<{ class_id: string; class_name: string; students: number }>; kps: Array<{ kp_id: string; error_rate: number }>; cells: Array<{ class_index: number; kp_index: number; error_rate: number | null; answers: number }> }
  student_scatter?: Array<{ student_id: string; name: string; class_name: string; answers: number; accuracy: number | null }>
  weak_kps?: Array<{ kp_id: string; error_rate: number; answers: number }>
  score_dist?: { metric: string; bins: Array<{ label: string; count: number }>; n: number }
  warning?: { identified: number; processed: number; closed_loop_available: boolean }
}

function ChartHint({ children }: { children: React.ReactNode }) {
  return <p className="text-caption text-ink-muted m-0 mt-2">{children}</p>
}

/** 图卡头部的下钻入口 —— 每张图都必须能点出名单（调研结论）。 */
function DrillLink({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button variant="ghost" onClick={onClick} className="!text-caption !px-1.5 !py-0.5">
      {label} →
    </Button>
  )
}

/* ── 八图 ───────────────────────────────────────────── */

function TrendChart({ data, onDrill }: { data: NonNullable<ChartsData['trend']>; onDrill: () => void }) {
  const series = trendSeries(data.points)
  const empty = series.answers.every((n) => n === 0)
  return (
    <Card title="作答趋势（近 12 个月）" action={<DrillLink label="班级学情" onClick={onDrill} />}>
      {empty ? (
        <EmptyState title="还没有作答数据" description="数据从系统投入使用起累计；先让学生做一次练习或导入历史成绩。" />
      ) : (
        <EChart height={240} option={{
          tooltip: { trigger: 'axis' },
          legend: { data: ['作答量', '正确率'], top: 0, textStyle: CHART_AXIS_TEXT },
          grid: { left: 44, right: 44, top: 40, bottom: 26 },
          xAxis: { type: 'category', data: series.months, axisLabel: CHART_AXIS_TEXT },
          yAxis: [
            { type: 'value', name: '作答', axisLabel: CHART_AXIS_TEXT },
            { type: 'value', name: '正确率', max: 1, axisLabel: { ...CHART_AXIS_TEXT, formatter: (v: number) => `${Math.round(v * 100)}%` } },
          ],
          series: [
            { name: '作答量', type: 'bar', data: series.answers, itemStyle: { color: CHART_COLORS.primary } },
            { name: '正确率', type: 'line', yAxisIndex: 1, smooth: true, connectNulls: false, data: series.accuracy, itemStyle: { color: CHART_COLORS.teal } },
          ],
        }} />
      )}
      <ChartHint>
        数据截止 {data.data_until ?? '—'}；正确率只在样本充足（≥10 次）的月份显示。
      </ChartHint>
    </Card>
  )
}

function ParentFunnelChart({ data }: { data: NonNullable<ChartsData['parent_funnel']> }) {
  const navigate = useNavigate()
  // 三段可下钻（spec：漏斗每段能点出名单）；第三段没有数据源，点了不跳
  const rows = [
    { label: '已绑定', value: data.bound, target: '/admin/parents' },
    { label: '有账号', value: data.activated, target: '/admin/parents?seg=activated' },
    { label: '近 7 天活跃', value: data.active_7d, unknown: !data.active_7d_available, target: '' },
  ]
  return (
    <Card title="家长激活漏斗">
      <div className="space-y-2">
        {rows.map((r) => {
          const pct = data.bound > 0 ? Math.min(100, Math.round((Number(r.value ?? 0) / data.bound) * 100)) : 0
          return (
            <div
              key={r.label}
              onClick={() => { if (r.target) navigate(r.target) }}
              className={r.target ? 'cursor-pointer rounded-[var(--radius-sm)] -mx-1 px-1 py-0.5 hover:bg-surface-muted transition-colors' : ''}
            >
              <div className="flex items-baseline justify-between text-sub">
                <span className="text-ink-secondary">{r.label}{r.target ? ' →' : ''}</span>
                <span className="font-mono font-semibold text-ink">
                  {r.unknown ? '未采集' : r.value}
                  {!r.unknown && data.bound > 0 && (
                    <span className="text-caption text-ink-muted ml-1">（{pct}%）</span>
                  )}
                </span>
              </div>
              <div className="h-2 rounded-full bg-surface-muted mt-1 overflow-hidden">
                <div className="h-full rounded-full bg-primary" style={{ width: `${r.unknown ? 0 : pct}%` }} />
              </div>
            </div>
          )
        })}
      </div>
      <ChartHint>
        「绑定 → 有账号」之间是最大的流失段：{Math.max(0, data.bound - data.activated)} 位家长从未激活。
        {!data.active_7d_available && ' 近 7 天活跃暂无数据源（登录未记活跃），显示为未采集而非 0。'}
      </ChartHint>
    </Card>
  )
}

function ClassCompareChart({ data, onDrill }: { data: NonNullable<ChartsData['class_compare']>; onDrill: () => void }) {
  // R-ADM-1 四档：n<4 不画点；4–9 只给人数；10–29 用中位数；≥30 用均值
  const { drawn, hidden } = classPoints(data)
  const solid = drawn.filter((p) => p.tier === 'ok' && p.value != null)
  const baseline = solid.length
    ? solid.reduce((acc, p) => acc + (p.value as number) * p.answers, 0) /
      solid.reduce((acc, p) => acc + p.answers, 0)
    : null
  return (
    <Card title="班级对比" action={<DrillLink label="班级学情" onClick={onDrill} />}>
      {drawn.length === 0 ? (
        <EmptyState
          title="还没有可比较的班级数据"
          description={
            hidden.length
              ? `已有 ${hidden.length} 个班有作答，但都少于 4 次（样本不足，不画点）。`
              : '导入一次考试成绩或让学生做练习后，这里会出现各班的点。'
          }
        />
      ) : (
        <EChart height={240} option={{
          tooltip: {
            trigger: 'item',
            formatter: (p: { data: { name: string; label: string; note: string } }) =>
              `${p.data.name}<br/>正确率 ${p.data.label}<br/>${p.data.note}`,
          },
          grid: { left: 44, right: 24, top: 24, bottom: 34 },
          xAxis: { type: 'category', data: drawn.map((p) => p.class_name), axisLabel: CHART_AXIS_TEXT },
          yAxis: { type: 'value', min: 0, max: 1, axisLabel: { ...CHART_AXIS_TEXT, formatter: (v: number) => `${Math.round(v * 100)}%` } },
          series: [{
            type: 'scatter',
            symbolSize: (_val: number[], p: { dataIndex: number }) => {
              const tier = drawn[p.dataIndex].tier
              return tier === 'small' ? 8 : tier === 'medium' ? 11 : 14
            },
            data: drawn.map((p) => ({
              name: p.class_name,
              value: [p.class_name, p.value],
              label: p.label,
              note: p.note,
              itemStyle: {
                color: p.tier === 'small' ? CHART_COLORS.muted : CHART_COLORS.primary,
                opacity: p.tier === 'small' ? 0.6 : 1,
              },
            })),
            markLine: baseline == null ? undefined : {
              silent: true,
              symbol: 'none',
              lineStyle: { type: 'dashed', color: CHART_COLORS.warning },
              label: { formatter: `年级基准 ${Math.round(baseline * 100)}%`, ...CHART_AXIS_TEXT },
              data: [{ yAxis: baseline }],
            },
          }],
        }} />
      )}
      <ChartHint>
        灰点 = 有效作答 4–9 次（只给人数、不给百分比）；10–29 次显示中位数并标注样本较少；
        年级基准只由 ≥30 次的班加权得出。
        {hidden.length > 0 && (
          <span className="block mt-1">
            样本不足未展示（&lt;4 次）：{hidden.map((h) => `${h.class_name}（${h.answers}）`).join('、')}
          </span>
        )}
      </ChartHint>
    </Card>
  )
}

function KpHeatmapChart({ data, onDrill }: { data: NonNullable<ChartsData['kp_heatmap']>; onDrill: () => void }) {
  const matrix = heatmapCells(data)
  const hasAny = matrix.some((row) => row.some((c) => c !== null))
  const rates = data.cells.map((c) => c.error_rate).filter((r): r is number => r != null)
  const lo = rates.length ? Math.floor(Math.min(...rates) * 10) / 10 : 0
  const hi = rates.length ? Math.max(lo + 0.1, Math.ceil(Math.max(...rates) * 10) / 10) : 1
  const heatRange = { min: lo, max: hi }
  return (
    <Card title="知识点掌握热力" action={<DrillLink label="知识地图" onClick={onDrill} />}>
      {!hasAny ? (
        <EmptyState
          title="知识点还没有足够作答样本"
          description="每个知识点至少要 5 次作答才计入（与教师端星图同一口径）。"
        />
      ) : (
        <EChart height={260} option={{
          tooltip: {
            formatter: (p: { value: [number, number, number | null, number] }) => {
              const [ci, ki, rate, n] = p.value
              const cls = data.classes[ci]?.class_name ?? ''
              const kp = data.kps[ki]?.kp_id ?? ''
              return rate == null ? `${cls} · ${kp}<br/>未测` : `${cls} · ${kp}<br/>错误率 ${formatPercent(rate, n)}（n=${n}）`
            },
          },
          grid: { left: 70, right: 20, top: 16, bottom: 70 },
          xAxis: { type: 'category', data: data.kps.map((k) => k.kp_id), axisLabel: { ...CHART_AXIS_TEXT, rotate: 45 } },
          yAxis: { type: 'category', data: data.classes.map((c) => c.class_name), axisLabel: CHART_AXIS_TEXT },
          visualMap: {
            // 色阶在**当前展示的这 10 个知识点**内部归一：它们都是最弱的一批（整片 70%+），
            // 用绝对 0–100% 会全部饱和成同一种红，看不出"哪个班更差"。脚注里已说明这一点。
            ...heatRange,
            calculable: false, orient: 'horizontal', left: 'center', bottom: 0,
            inRange: { color: ['#e8eef5', '#b45309', '#b43c28'] }, // @token-allow chart(canvas)
            textStyle: CHART_AXIS_TEXT,
          },
          series: [{
            type: 'heatmap',
            data: data.classes.flatMap((_, ci) =>
              data.kps.map((__, ki) => {
                const cell = matrix[ci]?.[ki]
                // 无样本给「显式灰底」：ECharts 对 null 值默认画成空白，看不出"未测"
                return cell
                  ? [ci, ki, cell[0]]
                  : { value: [ci, ki, null], itemStyle: { color: '#f1f3f5' } } // @token-allow chart(canvas)
              }),
            ),
            itemStyle: { borderColor: '#fff', borderWidth: 1 }, // @token-allow chart(canvas)
            emphasis: { itemStyle: { shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.2)' } }, // @token-allow chart(canvas)
          }],
        }} />
      )}
      <ChartHint>
        灰色格子 = 未测（无样本或不足 5 次），不按 0% 显示；色阶在这 {data.kps.length} 个最弱知识点内部归一，比较的是「同一知识点下哪个班更差」。
      </ChartHint>
    </Card>
  )
}

function StudentScatterChart({ data, onDrill }: { data: NonNullable<ChartsData['student_scatter']>; onDrill: () => void }) {
  const points = scatterPoints(data)
  return (
    <Card title="风险学生散点" action={<DrillLink label="学生管理" onClick={onDrill} />}>
      {points.length === 0 ? (
        <EmptyState title="还没有学生作答数据" description="学生做练习后，每个点代表一名学生：越靠右下越需要关注。" />
      ) : (
        <EChart height={260} option={{
          tooltip: {
            formatter: (p: { data: { name: string; class_name: string; value: [number, number] } }) =>
              `${p.data.name}（${p.data.class_name}）<br/>作答 ${p.data.value[0]} 次<br/>正确率 ${Math.round(p.data.value[1] * 100)}%`,
          },
          grid: { left: 44, right: 24, top: 20, bottom: 30 },
          xAxis: { type: 'value', name: '作答量', axisLabel: CHART_AXIS_TEXT },
          yAxis: { type: 'value', min: 0, max: 1, name: '正确率', axisLabel: { ...CHART_AXIS_TEXT, formatter: (v: number) => `${Math.round(v * 100)}%` } },
          series: [{
            type: 'scatter',
            data: points.map((p) => ({
              name: p.name, class_name: p.class_name, value: p.value,
              symbolSize: p.size,
              itemStyle: { color: p.tier === 'tiny' ? CHART_COLORS.muted : CHART_COLORS.teal, opacity: 0.75 },
            })),
          }],
        }} />
      )}
      <ChartHint>点越大 = 作答越多；灰点 = 作答不足 4 次，结论不可靠。</ChartHint>
    </Card>
  )
}

function WeakKpChart({ data, onDrill }: { data: NonNullable<ChartsData['weak_kps']>; onDrill: () => void }) {
  return (
    <Card title="薄弱知识点 TOP10" action={<DrillLink label="诊断分析" onClick={onDrill} />}>
      {data.length === 0 ? (
        <EmptyState title="暂无足够样本" description="知识点需要至少 5 次作答才会进入统计。" />
      ) : (
        <EChart height={260} option={{
          tooltip: { formatter: (p: { data: { kp: string; n: number; value: number } }) => `${p.data.kp}<br/>错误率 ${Math.round(p.data.value * 100)}%（n=${p.data.n}）` },
          grid: { left: 90, right: 40, top: 10, bottom: 24 },
          xAxis: { type: 'value', max: 1, axisLabel: { ...CHART_AXIS_TEXT, formatter: (v: number) => `${Math.round(v * 100)}%` } },
          yAxis: { type: 'category', data: [...data].reverse().map((k) => k.kp_id), axisLabel: CHART_AXIS_TEXT },
          series: [{
            type: 'bar',
            data: [...data].reverse().map((k) => ({ value: k.error_rate, kp: k.kp_id, n: k.answers, itemStyle: { color: CHART_COLORS.warning } })),
            label: { show: true, position: 'right', ...CHART_AXIS_TEXT,
              formatter: (p: { data: { value: number; n: number } }) => formatPercent(p.data.value, p.data.n) },
          }],
        }} />
      )}
      <ChartHint>按错误率降序；可直接排进下一轮讲评。</ChartHint>
    </Card>
  )
}

function ScoreDistChart({ data }: { data: NonNullable<ChartsData['score_dist']> }) {
  return (
    <Card title="考试得分率分布">
      {data.n === 0 ? (
        <EmptyState title="还没有考试作答" description="导入一次考试成绩后，这里显示每次考试每名学生的得分率分布。" />
      ) : (
        <EChart height={220} option={{
          tooltip: { trigger: 'axis' },
          grid: { left: 44, right: 20, top: 20, bottom: 26 },
          xAxis: { type: 'category', data: data.bins.map((b) => b.label), axisLabel: CHART_AXIS_TEXT },
          yAxis: { type: 'value', name: '人次', axisLabel: CHART_AXIS_TEXT },
          series: [{ type: 'bar', data: data.bins.map((b) => b.count), itemStyle: { color: CHART_COLORS.success } }],
        }} />
      )}
      <ChartHint>口径：每名学生在每场考试的作答正确率分档（共 {data.n} 人次）。</ChartHint>
    </Card>
  )
}

function WarningChart({ data, onDrill }: { data: NonNullable<ChartsData['warning']>; onDrill: () => void }) {
  return (
    <Card title="学业预警" action={<DrillLink label="班级学情" onClick={onDrill} />}>
      <div className="grid grid-cols-2 gap-3">
        <StatCard label="已识别" value={data.identified} tone={data.identified > 0 ? 'warn' : 'ok'} />
        <StatCard label="已处理" value={data.processed}
          hint={data.closed_loop_available ? undefined : '处理流程尚未产生记录'} />
      </div>
      {!data.closed_loop_available && (
        <p className="text-caption text-ink-muted m-0 mt-3">
          闭环数据未接入：当前只有「识别」记录，处理率无法计算 —— 不显示 0% 以免被读成"没处理"。
        </p>
      )}
    </Card>
  )
}

/* ── 页面 ───────────────────────────────────────────── */

export function AdminOverviewPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<ChartsData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setData(await api.get<ChartsData>('/api/admin/overview-charts'))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const kpi = data?.kpi
  const activationPct = useMemo(() => {
    if (!kpi || !kpi.parent_bindings) return null
    const pct = (kpi.parent_accounts / kpi.parent_bindings) * 100
    // 低于 1% 保留一位小数 —— 0.3% 被四舍五入成 "0%" 会被误读为"完全没有"
    return pct < 1 ? Math.round(pct * 10) / 10 : Math.round(pct)
  }, [kpi])

  if (loading) {
    return (
      <div className="space-y-4">
        <div className="grid grid-cols-4 gap-4 max-[1099px]:grid-cols-2">
          <SkeletonCard /><SkeletonCard /><SkeletonCard /><SkeletonCard />
        </div>
        <SkeletonCard />
      </div>
    )
  }
  if (error) {
    return <EmptyState title="概览加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }
  if (!data) return null

  if (data.data_scope_empty) {
    return (
      <EmptyState
        title="暂无可查看的学校数据"
        description="平台超管默认不隶属任何学校，数据范围为空。需要跨校排查时先发起限时提权（break-glass），到期自动收回；日常运营请使用学校管理员账号。"
        action={<Button onClick={() => navigate('/admin/break-glass')}>去发起提权</Button>}
      />
    )
  }

  const emptySchool = kpi && kpi.students === 0 && kpi.teachers === 0

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">管理概览</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">学校运营快照 —— 每张图都能点出名单</p>
      </header>

      {/* KPI：家长激活率是本页最有信息量的一个数（绑定多、账号少） */}
      <div className="grid grid-cols-4 gap-4 max-[1099px]:grid-cols-2">
        <StatCard label="学生" value={kpi?.students ?? 0} suffix="人" icon={<Users size={18} />} />
        <StatCard label="教师" value={kpi?.teachers ?? 0} suffix="人" />
        <StatCard label="班级" value={kpi?.classes ?? 0} suffix="个" />
        <StatCard
          label="家长激活"
          value={`${kpi?.parent_accounts ?? 0}/${kpi?.parent_bindings ?? 0}`}
          tone={activationPct != null && activationPct < 50 ? 'warn' : 'normal'}
          hint={activationPct == null ? '尚无绑定记录' : `激活率 ${activationPct}%`}
        />
      </div>

      {emptySchool && (
        <Card>
          <EmptyState
            title="学校还没有数据"
            description="从「班级与年级」开始建班，再导入学生与教师。"
            action={<Button onClick={() => navigate('/admin/classes')}>去建第一个班级</Button>}
          />
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 max-[1099px]:grid-cols-1">
        {data.trend && <TrendChart data={data.trend} onDrill={() => navigate('/class')} />}
        {data.parent_funnel && <ParentFunnelChart data={data.parent_funnel} />}
        {data.class_compare && <ClassCompareChart data={data.class_compare} onDrill={() => navigate('/class')} />}
        {data.warning && <WarningChart data={data.warning} onDrill={() => navigate('/class')} />}
        {data.kp_heatmap && <KpHeatmapChart data={data.kp_heatmap} onDrill={() => navigate('/kmap')} />}
        {data.student_scatter && <StudentScatterChart data={data.student_scatter} onDrill={() => navigate('/students')} />}
        {data.weak_kps && <WeakKpChart data={data.weak_kps} onDrill={() => navigate('/diagnosis')} />}
        {data.score_dist && <ScoreDistChart data={data.score_dist} />}
      </div>

      {can('knowledge:update') && (
        <div className="flex items-center gap-2">
          <Tag tone="blue">内容与 RAG</Tag>
          <Button variant="ghost" onClick={() => navigate('/admin/kb')}>去 KB 生产 →</Button>
        </div>
      )}
    </div>
  )
}
