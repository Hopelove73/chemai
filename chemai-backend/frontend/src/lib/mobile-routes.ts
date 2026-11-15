/* mobile-routes.ts — 移动端 navigate 白名单（student-daily-loop S4 / REQ-MSC-003）。
 * Gateway 的 navigate 事件目标表是教师页面集合（exam-v2 / diagnosis / students…）；
 * 学生 persona 正常不产生 navigate，防御性过滤：只放行移动路由。 */

/** 允许 agent navigate 跳转的移动路由（精确匹配 + 前缀匹配两类） */
export const MOBILE_NAVIGATE_WHITELIST: string[] = ['/m', '/m/practice', '/m/wrong', '/m/review', '/m/me']

/** 前缀匹配的动态路由（如工件详情 /m/artifact/:artifactId） */
export const MOBILE_NAVIGATE_PREFIXES: string[] = ['/m/artifact/']

/** 教师端 reducer 的 page→path 映射（exam-workbench 特例）后，判断是否可跳。
 * 返回规范化路径；不在白名单返回 null（调用方忽略该事件）。 */
export function allowedMobileNavigate(path: string): string | null {
  const p = String(path || '')
  if (!p.startsWith('/m')) return null
  if (MOBILE_NAVIGATE_WHITELIST.includes(p)) return p
  if (MOBILE_NAVIGATE_PREFIXES.some((pre) => p.startsWith(pre))) return p
  return null
}
