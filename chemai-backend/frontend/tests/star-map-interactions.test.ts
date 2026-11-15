/* 星图交互层纯函数（star-map-interactions / REQ-SMI-002/003/004）。 */
import { describe, expect, it } from 'vitest'
import { edgeKey, isGhost, matchStars, prereqClosure } from '../src/lib/starMap'

const E = (from_kp: string, to_kp: string, status = 'active') => ({ from_kp, to_kp, status })

describe('prereqClosure', () => {
  // A → B → C → D，X → C；C → E（后继），disabled: Z → C
  const edges = [E('A', 'B'), E('B', 'C'), E('X', 'C'), E('C', 'D'), E('C', 'E'), E('Z', 'C', 'disabled')]

  it('包含全部上游祖先 + 直接后继', () => {
    const { nodes } = prereqClosure(edges, 'C')
    expect([...nodes].sort()).toEqual(['A', 'B', 'C', 'D', 'E', 'X'])
  })

  it('不含 disabled 边引入的节点', () => {
    expect(prereqClosure(edges, 'C').nodes.has('Z')).toBe(false)
  })

  it('后继只到直接一层（不含后继的后继）', () => {
    const more = [...edges, E('D', 'F')]
    expect(prereqClosure(more, 'C').nodes.has('F')).toBe(false)
  })

  it('高亮边 = 祖先链内部边 + 选中星到直接后继', () => {
    const { edges: keys } = prereqClosure(edges, 'C')
    expect([...keys].sort()).toEqual(
      [edgeKey('A', 'B'), edgeKey('B', 'C'), edgeKey('C', 'D'), edgeKey('C', 'E'), edgeKey('X', 'C')].sort())
  })

  it('有环时不死循环', () => {
    const cyclic = [E('A', 'B'), E('B', 'A'), E('B', 'C')]
    expect([...prereqClosure(cyclic, 'C').nodes].sort()).toEqual(['A', 'B', 'C'])
  })

  it('孤立星只含自身', () => {
    expect([...prereqClosure(edges, 'Q').nodes]).toEqual(['Q'])
  })
})

describe('matchStars', () => {
  const stars = [
    { kp_id: 'M1-T2-KP06', name: '氧化还原反应' },
    { kp_id: 'M1-T2-KP12', name: '氯气的性质' },
    { kp_id: 'M4-T1-KP03', name: '原电池与氧化还原' },
    { kp_id: 'M2-T3-KP01', name: '化学反应速率' },
  ]

  it('名称前缀命中排在子串命中之前', () => {
    expect(matchStars(stars, '氧化').map((s) => s.kp_id)).toEqual(['M1-T2-KP06', 'M4-T1-KP03'])
  })

  it('支持编码匹配（大小写不敏感）', () => {
    expect(matchStars(stars, 'm1-t2-kp12').map((s) => s.name)).toEqual(['氯气的性质'])
  })

  it('空查询返回空；结果受上限约束', () => {
    expect(matchStars(stars, '  ')).toEqual([])
    expect(matchStars(stars, 'KP', 2)).toHaveLength(2)
  })
})

describe('isGhost', () => {
  const none = { hiddenModules: new Set<string>(), minErrorRate: 0 }

  it('无筛选时都不是幽灵（含无样本星）', () => {
    expect(isGhost({ module: '必修一', class_error_rate: null }, none)).toBe(false)
  })

  it('所属册被关闭 → 幽灵', () => {
    const f = { hiddenModules: new Set(['必修一']), minErrorRate: 0 }
    expect(isGhost({ module: '必修一', class_error_rate: 0.5 }, f)).toBe(true)
    expect(isGhost({ module: '必修二', class_error_rate: 0.5 }, f)).toBe(false)
  })

  it('错误率下限：低于下限或无样本 → 幽灵', () => {
    const f = { hiddenModules: new Set<string>(), minErrorRate: 0.4 }
    expect(isGhost({ module: '必修一', class_error_rate: 0.3 }, f)).toBe(true)
    expect(isGhost({ module: '必修一', class_error_rate: 0.4 }, f)).toBe(false)
    expect(isGhost({ module: '必修一', class_error_rate: null }, f)).toBe(true)
  })
})
