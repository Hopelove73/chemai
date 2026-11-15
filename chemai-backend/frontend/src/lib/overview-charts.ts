/* overview-charts.ts — 管理概览八图的数据映射与**小样本降级**（admin-shell-v2）。
 *
 * 这些规则来自调研结论（K-State / Salesforce 教育看板实践）：
 *   n≥30 给百分比；10≤n<30 点 + 中位数；4≤n<9 只给人数；n<4 只给名单；n=0 不画零值。
 * 纯函数 —— 渲染层改（echarts 配置怎么调）不影响这些断言。
 */

export type SampleTier = 'none' | 'tiny' | 'small' | 'medium' | 'ok'

export function sampleTier(n: number): SampleTier {
  if (!n || n <= 0) return 'none'
  if (n < 4) return 'tiny'
  if (n < 10) return 'small'
  if (n < 30) return 'medium'
  return 'ok'
}

/** 百分比展示：样本不足一律「—」，避免小样本给出虚假确定性。 */
export function formatPercent(accuracy: number | null | undefined, n: number): string {
  if (accuracy == null) return '—'
  const tier = sampleTier(n)
  if (tier === 'none' || tier === 'tiny' || tier === 'small') return '—'
  return `${Math.round(accuracy * 100)}%`
}

export interface HeatmapPayload {
  classes: Array<{ class_id: string; class_name: string; students: number }>
  kps: Array<{ kp_id: string; error_rate: number }>
  cells: Array<{ class_index: number; kp_index: number; error_rate: number | null; answers: number }>
}

/**
 * 热力矩阵 → `[classIndex][kpIndex] = [errorRate, answers] | null`。
 * **无样本 = null（灰色"未测"），绝不是 0** —— 0 会被读成"全对"。
 * 缺失的格子也补 null，保证矩阵结构对齐。
 */
export function heatmapCells(payload: HeatmapPayload): Array<Array<[number, number] | null>> {
  const byKey = new Map<string, { error_rate: number | null; answers: number }>()
  for (const c of payload.cells) byKey.set(`${c.class_index}:${c.kp_index}`, c)
  return payload.classes.map((_, ci) =>
    payload.kps.map((__, ki) => {
      const cell = byKey.get(`${ci}:${ki}`)
      if (!cell || cell.error_rate == null) return null
      return [cell.error_rate, cell.answers] as [number, number]
    }),
  )
}

export interface ScatterStudent {
  student_id: string
  name: string
  class_name: string
  answers: number
  accuracy: number | null
}

export interface ScatterPoint {
  name: string
  class_name: string
  value: [number, number]   // [作答量, 正确率]
  size: number
  tier: SampleTier
}

/** 风险学生散点：x=作答量，y=正确率，点大小随作答量；样本不足的点标记出来。 */
export function scatterPoints(students: ScatterStudent[], maxSize = 18): ScatterPoint[] {
  const maxAnswers = Math.max(1, ...students.map((s) => s.answers))
  return students
    .filter((s) => s.accuracy != null)
    .map((s) => ({
      name: s.name,
      class_name: s.class_name,
      value: [s.answers, s.accuracy as number] as [number, number],
      size: Math.max(6, Math.round((s.answers / maxAnswers) * maxSize)),
      tier: sampleTier(s.answers),
    }))
}

/** 趋势序列拆成两条（作答量柱 + 正确率线），正确率在样本不足的月份留空。 */
export function trendSeries(
  points: Array<{ month: string; answers: number; accuracy: number | null }>,
): { months: string[]; answers: number[]; accuracy: Array<number | null> } {
  return {
    months: points.map((p) => p.month.slice(2)),
    answers: points.map((p) => p.answers),
    accuracy: points.map((p) => (sampleTier(p.answers) === 'ok' || sampleTier(p.answers) === 'medium'
      ? p.accuracy
      : null)),
  }
}

/* ── 班级对比的四档分流（R-ADM-1）────────────────────────────
 * n≥30        均值百分比，正常点
 * 10≤n<30     中位数百分比，标注"样本较少"
 * 4≤n<9       只给人数（n），不给百分比
 * n<4         不画点，进「样本不足未展示」名单
 */

export interface ClassCompareRow {
  class_id: string
  class_name: string
  answers: number
  accuracy: number | null
  median?: number | null
  students?: number
}

export interface ClassPoint {
  class_id: string
  class_name: string
  answers: number
  tier: SampleTier
  value: number | null
  /** 图中/提示里展示的百分比文本（small/tiny 为 '—'） */
  label: string
  /** 提示的补充说明（样本档位与人数） */
  note: string
}

export function classPoints(rows: ClassCompareRow[]): {
  drawn: ClassPoint[]
  hidden: Array<{ class_name: string; answers: number }>
} {
  const drawn: ClassPoint[] = []
  const hidden: Array<{ class_name: string; answers: number }> = []
  for (const r of rows) {
    const tier = sampleTier(r.answers)
    if (tier === 'none' || tier === 'tiny') {
      hidden.push({ class_name: r.class_name, answers: r.answers })
      continue
    }
    if (tier === 'small') {
      drawn.push({
        class_id: r.class_id, class_name: r.class_name, answers: r.answers, tier,
        value: r.accuracy, label: '—',
        note: `样本过少（n=${r.answers}，${r.students ?? '?'} 人），只给人数不给正确率`,
      })
      continue
    }
    if (tier === 'medium') {
      const v = r.median ?? r.accuracy
      drawn.push({
        class_id: r.class_id, class_name: r.class_name, answers: r.answers, tier,
        value: v, label: v == null ? '—' : `${Math.round(v * 100)}%`,
        note: `样本较少（n=${r.answers}），显示中位数`,
      })
      continue
    }
    drawn.push({
      class_id: r.class_id, class_name: r.class_name, answers: r.answers, tier,
      value: r.accuracy,
      label: r.accuracy == null ? '—' : `${Math.round(r.accuracy * 100)}%`,
      note: `n=${r.answers}`,
    })
  }
  return { drawn, hidden }
}
