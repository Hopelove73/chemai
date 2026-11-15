/* admin-nav.test.ts — admin-shell-v2 任务 2.1：管理态分组派生纯函数。
 *
 * 纪律：分组只影响展示；谁能看到哪个 key 由后端 MENUS 决定（caps.menus）。
 * 这里测的是「给定后端下发的 key 列表 → 五组折叠结构」。
 */
import { describe, expect, it } from 'vitest'
import { ADMIN_GROUPS, groupAdminNav, isAdminKey } from '../src/lib/admin-nav'

const REGISTRY: Record<string, { path: string; label: string; symbol: string }> = {
  admin_overview: { path: '/admin', label: '管理概览', symbol: 'Pt' },
  admin_teachers: { path: '/admin/teachers', label: '教师账号', symbol: 'Mn' },
  admin_classes: { path: '/admin/classes', label: '班级与年级', symbol: 'Pb' },
  admin_kb: { path: '/admin/kb', label: 'KB 生产', symbol: 'B' },
  admin_logs: { path: '/admin/logs', label: '操作日志', symbol: 'Rn' },
  admin_settings: { path: '/admin/settings', label: '运行时开关', symbol: 'Tc' },
  admin_school: { path: '/admin/school', label: '学校设置', symbol: 'Sc' },
}

const GROUP_OF: Record<string, string> = {
  admin_overview: 'overview',
  admin_teachers: 'org',
  admin_classes: 'org',
  admin_kb: 'content',
  admin_logs: 'ops',
  admin_settings: 'ops',
  admin_school: 'system',
}

describe('isAdminKey', () => {
  it('识别管理 key', () => {
    expect(isAdminKey('admin_kb')).toBe(true)
    expect(isAdminKey('rag_traces')).toBe(true)
    expect(isAdminKey('break_glass')).toBe(true)
    expect(isAdminKey('chat')).toBe(false)
    expect(isAdminKey('students')).toBe(false)
  })
})

describe('groupAdminNav', () => {
  it('按五组固定顺序聚合，空组不出现', () => {
    const keys = ['admin_school', 'admin_overview', 'admin_logs']  // 乱序输入
    const groups = groupAdminNav(keys, REGISTRY, GROUP_OF)
    expect(groups.map((g) => g.key)).toEqual(['overview', 'ops', 'system'])
    expect(groups[0].items.map((i) => i.key)).toEqual(['admin_overview'])
    expect(groups[1].items.map((i) => i.key)).toEqual(['admin_logs'])
    expect(groups[2].items.map((i) => i.key)).toEqual(['admin_school'])
  })

  it('组内顺序沿用后端下发顺序，而不是注册表顺序', () => {
    const keys = ['admin_classes', 'admin_teachers']   // 后端顺序
    const groups = groupAdminNav(keys, REGISTRY, GROUP_OF)
    expect(groups[0].items.map((i) => i.key)).toEqual(['admin_classes', 'admin_teachers'])
  })

  it('未注册的 key 跳过但不丢整组', () => {
    const groups = groupAdminNav(['admin_overview', 'brand_new_admin'], REGISTRY, GROUP_OF)
    expect(groups).toHaveLength(1)
    expect(groups[0].items.map((i) => i.key)).toEqual(['admin_overview'])
  })

  it('没有管理 key 时返回空数组', () => {
    expect(groupAdminNav(['chat', 'class'], REGISTRY, GROUP_OF)).toEqual([])
  })

  it('五组常量顺序固定', () => {
    expect(ADMIN_GROUPS.map((g) => g.key)).toEqual(['overview', 'org', 'content', 'ops', 'system'])
  })

  it('当前路径所在组：用最长前缀匹配', () => {
    const groups = groupAdminNav(['admin_overview', 'admin_teachers', 'admin_classes'], REGISTRY, GROUP_OF)
    const active = groups.find((g) => g.items.some((i) => i.path === '/admin/classes'))
    expect(active?.key).toBe('org')
  })
})
