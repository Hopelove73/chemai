/* admin-nav.ts — 管理态导航分组（admin-shell-v2）。
 *
 * 纪律（与 MENU_REGISTRY 同一条）：**分组是展示细节，留在前端**；
 * 谁能看到哪个 key 由后端 MENUS 决定（caps.menus 里已经过滤过）。
 * 这里只做两件事：判定哪些 key 属于管理态、按五组固定顺序聚合成可折叠结构。
 *
 * 五组顺序固定为 overview → org → content → ops → system（调研结论：
 * 概览在最前，组织/账号次之，内容与 RAG 第三，运营与安全第四，系统最后）。
 */

export interface AdminGroupDef {
  key: string
  label: string
}

/** 五组固定顺序（组名是产品语言，属展示层）。 */
export const ADMIN_GROUPS: readonly AdminGroupDef[] = [
  { key: 'overview', label: '概览' },
  { key: 'org', label: '组织与账号' },
  { key: 'content', label: '内容与 RAG' },
  { key: 'ops', label: '运营与安全' },
  { key: 'system', label: '系统' },
] as const

export interface AdminNavItem {
  key: string
  path: string
  label: string
  symbol: string
}

export interface AdminNavGroup {
  key: string
  label: string
  items: AdminNavItem[]
}

/** 管理 key 的判定：admin_* / rag_* / break_glass。与后端 MENUS 的命名约定一致。 */
export function isAdminKey(key: string): boolean {
  return key.startsWith('admin_') || key.startsWith('rag_') || key === 'break_glass'
}

const GROUP_RANK: Record<string, number> = Object.fromEntries(
  ADMIN_GROUPS.map((g, i) => [g.key, i]),
)

/**
 * 把后端下发的 key 列表聚合成管理态分组。
 *
 * - 组顺序固定（ADMIN_GROUPS），空组不出现
 * - 组内顺序沿用 **后端下发顺序**（与 navItemsFor 同一条纪律）
 * - 注册表里没有的 key 跳过（后端加了菜单、前端还没发版时不崩）
 * - `groupOf` 未声明分组的 key 归入 ops（兜底，不让菜单凭空消失）
 */
export function groupAdminNav(
  menuKeys: string[],
  registry: Record<string, { path: string; label: string; symbol: string }>,
  groupOf: Record<string, string>,
): AdminNavGroup[] {
  const buckets = new Map<string, AdminNavItem[]>()
  for (const key of menuKeys) {
    if (!isAdminKey(key)) continue
    const meta = registry[key]
    if (!meta) continue
    const group = groupOf[key] ?? 'ops'
    if (!buckets.has(group)) buckets.set(group, [])
    buckets.get(group)!.push({ key, path: meta.path, label: meta.label, symbol: meta.symbol })
  }
  return ADMIN_GROUPS
    .filter((g) => buckets.has(g.key))
    .map((g) => ({ key: g.key, label: g.label, items: buckets.get(g.key)! }))
    .sort((a, b) => (GROUP_RANK[a.key] ?? 99) - (GROUP_RANK[b.key] ?? 99))
}

/** 当前路径所属的分组 key（用于自动展开）；最长的 path 前缀优先。 */
export function activeAdminGroup(
  groups: AdminNavGroup[],
  pathname: string,
): string | null {
  let best: { group: string; len: number } | null = null
  for (const g of groups) {
    for (const item of g.items) {
      const hit = pathname === item.path || pathname.startsWith(item.path + '/')
      if (hit && (!best || item.path.length > best.len)) {
        best = { group: g.key, len: item.path.length }
      }
    }
  }
  return best?.group ?? null
}
