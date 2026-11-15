/* overview-charts.test.ts — admin-shell-v2 任务 3.4：小样本降级与图表数据映射。
 *
 * 规则来自调研结论：n≥30 百分比；10≤n<30 点+中位数；4≤n<9 只给人数；
 * n<4 只给名单；n=0 不画零值。这里只测纯函数，不测 ECharts 渲染。
 */
import { describe, expect, it } from 'vitest'
import { classPoints, formatPercent, heatmapCells, sampleTier, scatterPoints } from '../src/lib/overview-charts'

describe('sampleTier', () => {
  it('分档边界', () => {
    expect(sampleTier(0)).toBe('none')
    expect(sampleTier(1)).toBe('tiny')
    expect(sampleTier(3)).toBe('tiny')
    expect(sampleTier(4)).toBe('small')
    expect(sampleTier(9)).toBe('small')
    expect(sampleTier(10)).toBe('medium')
    expect(sampleTier(29)).toBe('medium')
    expect(sampleTier(30)).toBe('ok')
  })
})

describe('formatPercent', () => {
  it('样本不足时不给百分比', () => {
    expect(formatPercent(0.5, 3)).toBe('—')
    expect(formatPercent(0.5, 9)).toBe('—')
  })
  it('样本达标时给百分比', () => {
    expect(formatPercent(0.5, 10)).toBe('50%')
    expect(formatPercent(1, 30)).toBe('100%')
  })
  it('accuracy 为 null 时也给 —', () => {
    expect(formatPercent(null, 100)).toBe('—')
  })
})

describe('heatmapCells', () => {
  const payload = {
    classes: [{ class_id: 'c1', class_name: '一班', students: 40 }],
    kps: [{ kp_id: 'KP1', error_rate: 0.4 }],
    cells: [
      { class_index: 0, kp_index: 0, error_rate: 0.4, answers: 12 },
    ],
  }

  it('无样本的格子输出 null（灰色未测），不填 0', () => {
    const cells = heatmapCells({
      ...payload,
      cells: [{ class_index: 0, kp_index: 0, error_rate: null, answers: 0 }],
    })
    expect(cells).toEqual([[null]])   // 1 班 × 1 知识点
  })

  it('有样本时输出 [错误率, 作答量]', () => {
    expect(heatmapCells(payload)).toEqual([[[0.4, 12]]])
  })

  it('缺失格子补 null（结构对齐 class × kp）', () => {
    const cells = heatmapCells({
      classes: payload.classes,
      kps: [{ kp_id: 'KP1', error_rate: 0.4 }, { kp_id: 'KP2', error_rate: 0.2 }],
      cells: [{ class_index: 0, kp_index: 0, error_rate: 0.4, answers: 5 }],
    })
    expect(cells).toEqual([[[0.4, 5], null]])   // 第二个知识点缺格 → null
  })
})

describe('scatterPoints', () => {
  it('点大小随作答量、样本不足标注', () => {
    const pts = scatterPoints([
      { student_id: 'S1', name: '甲', class_name: '一班', answers: 40, accuracy: 0.75 },
      { student_id: 'S2', name: '乙', class_name: '一班', answers: 3, accuracy: 0.66 },
    ])
    expect(pts[0].name).toBe('甲')
    expect(pts[0].tier).toBe('ok')
    expect(pts[1].tier).toBe('tiny')
    expect(pts[0].size).toBeGreaterThan(pts[1].size)
  })
})


describe('classPoints（R-ADM-1 四档）', () => {
  const rows = [
    { class_id: 'a', class_name: 'A班', answers: 40, accuracy: 0.8, median: 0.79, students: 20 },
    { class_id: 'b', class_name: 'B班', answers: 20, accuracy: 0.6, median: 0.62, students: 10 },
    { class_id: 'c', class_name: 'C班', answers: 6, accuracy: 0.5, median: 0.5, students: 4 },
    { class_id: 'd', class_name: 'D班', answers: 2, accuracy: 0.5, median: 0.5, students: 2 },
  ]

  it('n≥30 用均值百分比', () => {
    const { drawn } = classPoints(rows)
    const a = drawn.find((p) => p.class_name === 'A班')!
    expect(a.tier).toBe('ok')
    expect(a.label).toBe('80%')
  })

  it('10≤n<30 用中位数并标注样本较少', () => {
    const { drawn } = classPoints(rows)
    const b = drawn.find((p) => p.class_name === 'B班')!
    expect(b.tier).toBe('medium')
    expect(b.label).toBe('62%')
    expect(b.note).toContain('中位数')
    expect(b.note).toContain('20')
  })

  it('4≤n<9 只给人数、不给百分比', () => {
    const { drawn } = classPoints(rows)
    const c = drawn.find((p) => p.class_name === 'C班')!
    expect(c.tier).toBe('small')
    expect(c.label).toBe('—')
    expect(c.note).toContain('6')
  })

  it('n<4 不画点，只进"未展示"名单', () => {
    const { drawn, hidden } = classPoints(rows)
    expect(drawn.map((p) => p.class_name)).not.toContain('D班')
    expect(hidden).toEqual([{ class_name: 'D班', answers: 2 }])
  })
})
