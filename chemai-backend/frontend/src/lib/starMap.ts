/* 星图编码纯函数（star-map-teacher / REQ-CSM-002）。
 *
 * 亮度 = 掌握度（班级错误率反转，带可见下限）；大小 = 作答量（sqrt 归一）；
 * 无可信样本（class_error_rate 为 null：无作答或作答 <MIN_ANSWERS 被整条压制）
 * = 固定灰星。色相 = 册（DESIGN.md R-C9 对 R-C8 的显式豁免），见 StarMap。
 * 不做任何模拟时间衰减（真 FSRS 可提取性属 star-map-student）。 */

/** 暗星阈值：与 kb_error_rate.ERROR_RATE_THRESHOLD 同源（错误率 ≥30% 即薄弱） */
export const DIM_THRESHOLD = 0.3
/** 亮度下限：最暗的星也要可见，不消失 */
export const BRIGHTNESS_FLOOR = 0.18
/** 亮度映射的分母：错误率到这个值就算「最暗」，再差不再更暗 */
export const DIM_SATURATION = 0.6
/** 作答量归一基准：sqrt(answers) 达到此值即为最大尺寸 */
export const SIZE_SATURATION_ANSWERS = 350
/** 灰星亮度（无可信样本） */
export const GRAY_BRIGHTNESS = 0.28

export type StarKind = 'lit' | 'dim' | 'gray'

export function starKind(classErrorRate: number | null | undefined): StarKind {
  if (classErrorRate === null || classErrorRate === undefined) return 'gray'
  return classErrorRate >= DIM_THRESHOLD ? 'dim' : 'lit'
}

/** 错误率 → 亮度 [BRIGHTNESS_FLOOR, 1]；null → 灰星亮度 */
export function starBrightness(classErrorRate: number | null | undefined): number {
  if (classErrorRate === null || classErrorRate === undefined) return GRAY_BRIGHTNESS
  const ratio = 1 - Math.min(Math.max(classErrorRate, 0), DIM_SATURATION) / DIM_SATURATION
  return BRIGHTNESS_FLOOR + ratio * (1 - BRIGHTNESS_FLOOR)
}

/** 作答量 → 尺寸倍率 [0.6, 1.8]（sqrt 归一，规模差异可见但不夸张） */
export function starSize(classAnswers: number): number {
  const a = Math.max(classAnswers, 0)
  const ratio = Math.min(Math.sqrt(a) / Math.sqrt(SIZE_SATURATION_ANSWERS), 1)
  return 0.6 + ratio * 0.7
}

/** 6 册/专题的星系团锚点（3D 空间均匀分布，力导向按锚点聚簇） */
export const MODULE_ANCHORS: Record<string, { x: number; y: number; z: number }> = {
  必修一: { x: -540, y: 180, z: -180 },
  必修二: { x: -180, y: -210, z: 360 },
  选择性必修一: { x: 240, y: 240, z: -420 },
  选择性必修二: { x: 600, y: -120, z: 120 },
  选择性必修三: { x: 120, y: 480, z: 480 },
  专题方法: { x: 420, y: -450, z: -480 },
}
const FALLBACK_ANCHOR = { x: 0, y: 0, z: 0 }

export function moduleAnchor(module: string): { x: number; y: number; z: number } {
  return MODULE_ANCHORS[module] || FALLBACK_ANCHOR
}

/* ── 交互层纯函数（star-map-interactions）──────────────────────── */

export interface StarEdgeLite {
  from_kp: string
  to_kp: string
  status: string
}

/** 边键：高亮集合里的边用 `from->to` 标识 */
export function edgeKey(from: string, to: string): string {
  return `${from}->${to}`
}

/** 选中星的关系闭包（REQ-SMI-002）：全部上游祖先（沿 active 先修边反向
 * BFS，环防御）+ 直接后继。高亮边 = 祖先链内部的边 + 选中星到直接后继的边。 */
export function prereqClosure(
  edges: StarEdgeLite[],
  kpId: string,
): { nodes: Set<string>; edges: Set<string> } {
  const active = edges.filter((e) => e.status === 'active')
  const parents = new Map<string, string[]>()
  for (const e of active) {
    const list = parents.get(e.to_kp) || []
    list.push(e.from_kp)
    parents.set(e.to_kp, list)
  }
  const chain = new Set<string>([kpId])
  const queue = [kpId]
  while (queue.length) {
    const cur = queue.shift() as string
    for (const p of parents.get(cur) || []) {
      if (!chain.has(p)) {
        chain.add(p)
        queue.push(p)
      }
    }
  }
  const nodes = new Set(chain)
  const keys = new Set<string>()
  for (const e of active) {
    if (chain.has(e.from_kp) && chain.has(e.to_kp)) keys.add(edgeKey(e.from_kp, e.to_kp))
    if (e.from_kp === kpId) {
      nodes.add(e.to_kp)
      keys.add(edgeKey(e.from_kp, e.to_kp))
    }
  }
  return { nodes, edges: keys }
}

export interface SearchableStar {
  kp_id: string
  name: string
}

/** 搜索匹配（REQ-SMI-003）：名称子串或编码子串（大小写不敏感）；
 * 名称前缀命中排最前，其次名称子串，最后编码；最多 limit 条。 */
export function matchStars<T extends SearchableStar>(stars: T[], query: string, limit = 8): T[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const scored: { star: T; rank: number }[] = []
  for (const s of stars) {
    const name = s.name.toLowerCase()
    const code = s.kp_id.toLowerCase()
    let rank = -1
    if (name.startsWith(q)) rank = 0
    else if (name.includes(q)) rank = 1
    else if (code.includes(q)) rank = 2
    if (rank >= 0) scored.push({ star: s, rank })
  }
  scored.sort((a, b) => a.rank - b.rank || a.star.name.length - b.star.name.length)
  return scored.slice(0, limit).map((x) => x.star)
}

export interface StarFilter {
  /** 被关闭的册 */
  hiddenModules: Set<string>
  /** 错误率下限 0–1；> 0 时无样本星视为不满足 */
  minErrorRate: number
}

/** 筛选判定（REQ-SMI-004）：不满足条件的星呈幽灵态（不隐藏、不重排）。 */
export function isGhost(
  star: { module: string; class_error_rate: number | null | undefined },
  filter: StarFilter,
): boolean {
  if (filter.hiddenModules.has(star.module)) return true
  if (filter.minErrorRate > 0) {
    const r = star.class_error_rate
    if (r === null || r === undefined) return true
    return r < filter.minErrorRate
  }
  return false
}
