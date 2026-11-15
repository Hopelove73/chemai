/* menu-capabilities.test.ts — 阶段 5.2/5.3：能力清单的存取与菜单派生
 *
 * 这里的断言是「前端渲染菜单/守卫路由」的依据。注意它们是 **UX 一致性**，
 * 不是安全边界 —— 真正的边界在后端那 232 个端点（阶段 4）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  type LoginUser, can, clearAuth, getCaps, getToken, hasMenu, login, setCaps, setToken,
} from '../src/lib/api'
import { ADMIN_GROUP_OF, MENU_REGISTRY, ROUTE_MENU, navItemsFor } from '../src/App'
import { groupAdminNav, isAdminKey } from '../src/lib/admin-nav'

const TEACHER: LoginUser = {
  id: 'teacher_hai', username: 'hai', name: '海老师', role: 'teacher',
  role_label: '任课教师',
  permissions: ['analysis:read', 'question:read', 'question:create'],
  menus: ['chat', 'class', 'students', 'exam', 'diagnosis', 'ocr'],
}
const ADMIN: LoginUser = {
  ...TEACHER, id: 'admin_001', username: 'admin', role: 'admin', role_label: '学校管理员',
  menus: [...(TEACHER.menus || []), 'integration'],
}
const STUDENT: LoginUser = {
  id: '202610201', username: '202610201', name: '李雨萱', role: 'student',
  role_label: '学生', permissions: [], menus: [],
}

beforeEach(() => localStorage.clear())

describe('能力清单的存取', () => {
  it('存进去再读出来是同一份', () => {
    setCaps(TEACHER)
    expect(getCaps()).toMatchObject({ role: 'teacher', role_label: '任课教师' })
    expect(getCaps()?.menus).toContain('chat')
  })

  it('没存过 → null（调用方据此去 /me 水合）', () => {
    expect(getCaps()).toBeNull()
  })

  it('坏数据当作「没有」，而不是让页面崩在 undefined 上', () => {
    localStorage.setItem('chemai_caps', '{不是 JSON')
    expect(getCaps()).toBeNull()

    localStorage.setItem('chemai_caps', JSON.stringify({ role: 'teacher' })) // 缺 menus
    expect(getCaps()).toBeNull()
  })

  it('退出登录要把能力清单一起清掉', () => {
    setToken('t'); setCaps(TEACHER)
    clearAuth()
    expect(getCaps()).toBeNull()
  })

  it('can()/hasMenu() 反映当前角色的能力', () => {
    setCaps(TEACHER)
    expect(can('question:create')).toBe(true)
    expect(can('exam:delete')).toBe(false)
    expect(hasMenu('integration')).toBe(false)

    setCaps(ADMIN)
    expect(hasMenu('integration')).toBe(true)
  })
})

describe('登录失败：后端返回 200 + success:false', () => {
  /** 后端登录失败**不走 `!resp.ok`**（照样 200），此前前端直接读 `data.user.teacher_id`
   * → 任何一次失败登录（密码错 / 账号被禁用 / 账号不存在）都把页面崩成
   * "Cannot read properties of null"，而不是告诉用户原因。 */
  function stubLoginResponse(payload: unknown) {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify(payload),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )))
  }

  it('抛出服务端给的原因，而不是崩在 null 上', async () => {
    stubLoginResponse({ success: false, message: '用户名或密码错误', user: null, token: null })
    await expect(login('hai', 'hai123456')).rejects.toThrow('用户名或密码错误')
  })

  it('失败时不写入任何认证状态', async () => {
    stubLoginResponse({ success: false, message: '用户名或密码错误', user: null, token: null })
    await expect(login('hai', 'hai123456')).rejects.toThrow()
    expect(getToken()).toBe('')
    expect(getCaps()).toBeNull()
  })

  it('响应形状异常（缺 user）也不崩', async () => {
    stubLoginResponse({ success: true, token: 't' })   // 没有 user
    await expect(login('x', 'y')).rejects.toThrow()
  })

  afterEach(() => vi.unstubAllGlobals())
})

describe('菜单由后端 key 列表派生（阶段 5.3）', () => {
  it('顺序沿用后端下发的顺序，不是前端注册表的顺序', () => {
    const out = navItemsFor(['ocr', 'chat'])
    expect(out.map((i) => i.key)).toEqual(['ocr', 'chat'])
  })

  it('认不出的 key 跳过并告警 —— 静默丢弃会表现成「后端加了菜单前端不显示」', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const out = navItemsFor(['chat', 'brand_new_menu'])
    expect(out.map((i) => i.key)).toEqual(['chat'])
    expect(warn).toHaveBeenCalledOnce()
    warn.mockRestore()
  })

  it('每个注册过的菜单都有对应路由，否则点了会掉进兜底跳转', () => {
    const routed = new Set(Object.values(ROUTE_MENU))
    for (const key of Object.keys(MENU_REGISTRY)) {
      expect(routed, `菜单 ${key} 没有对应路由`).toContain(key)
    }
  })

  it('每条路由挂的菜单都真实存在', () => {
    for (const [path, key] of Object.entries(ROUTE_MENU)) {
      expect(Object.keys(MENU_REGISTRY), `路由 ${path} 指向了不存在的菜单 ${key}`).toContain(key)
    }
  })

  it('管理员与普通教师的导航项不同 —— 这正是本阶段要的结果', () => {
    const teacher = navItemsFor(TEACHER.menus || []).map((i) => i.key)
    const admin = navItemsFor(ADMIN.menus || []).map((i) => i.key)
    expect(teacher).not.toContain('integration')
    expect(admin).toContain('integration')
  })

  it('学生/家长没有教师端菜单 → 空导航（页面据此显示无权限页）', () => {
    expect(navItemsFor(STUDENT.menus || [])).toEqual([])
  })
})

describe('管理端菜单（admin-shell-v2）', () => {
  const SCHOOL_ADMIN: LoginUser = {
    ...TEACHER, id: 'admin_001', username: 'admin', role: 'admin', role_label: '学校管理员',
    menus: [...(TEACHER.menus || []), 'integration', 'admin_overview', 'admin_teachers', 'admin_classes',
      'admin_school', 'admin_logs', 'admin_kb', 'admin_settings'],
  }
  const ACADEMIC_ADMIN: LoginUser = {
    ...TEACHER, id: 'aa_001', role: 'academic_admin', role_label: '教务管理员',
    menus: [...(TEACHER.menus || []), 'admin_overview', 'admin_classes'],
  }

  it('admin 的管理键按后端下发顺序出现，且 admin_users 已退役', () => {
    const keys = navItemsFor(SCHOOL_ADMIN.menus || []).map((i) => i.key)
    const adminKeys = keys.filter((k) => k.startsWith('admin_'))
    expect(adminKeys).toEqual([
      'admin_overview', 'admin_teachers', 'admin_classes',
      'admin_school', 'admin_logs', 'admin_kb', 'admin_settings',
    ])
    expect(keys).not.toContain('admin_users')
  })

  it('teacher 不收到任何管理菜单 key', () => {
    const keys = navItemsFor(TEACHER.menus || []).map((i) => i.key)
    for (const key of ['admin_overview', 'admin_teachers', 'admin_classes', 'admin_school', 'admin_logs']) {
      expect(keys).not.toContain(key)
    }
  })

  it('academic_admin 只有概览与班级（无教师审批 / 学校设置 / 日志）', () => {
    const keys = navItemsFor(ACADEMIC_ADMIN.menus || []).map((i) => i.key)
    expect(keys).toContain('admin_overview')
    expect(keys).toContain('admin_classes')
    expect(keys).not.toContain('admin_teachers')
    expect(keys).not.toContain('admin_school')
    expect(keys).not.toContain('admin_logs')
  })

  it('管理菜单都登记了元素符号 tile（R-T-L2 侧栏惯例）', () => {
    for (const key of ['admin_overview', 'admin_teachers', 'admin_classes', 'admin_school', 'admin_logs', 'admin_kb', 'admin_settings']) {
      expect(MENU_REGISTRY[key]?.symbol, `${key} 缺元素符号`).toBeTruthy()
    }
  })

  it('管理路由与守卫菜单 key 对得上（登录落地 /admin 依赖 hasMenu("admin_overview")）', () => {
    expect(ROUTE_MENU['/admin']).toBe('admin_overview')
    expect(ROUTE_MENU['/admin/teachers']).toBe('admin_teachers')
    expect(ROUTE_MENU['/admin/classes']).toBe('admin_classes')
    expect(ROUTE_MENU['/admin/school']).toBe('admin_school')
    expect(ROUTE_MENU['/admin/logs']).toBe('admin_logs')
    // 旧路径不再是一条路由的菜单归属（redirect 到 /admin/teachers）
    expect(ROUTE_MENU['/admin/users']).toBeUndefined()
    setCaps(SCHOOL_ADMIN)
    expect(hasMenu('admin_overview')).toBe(true)
    setCaps(TEACHER)
    expect(hasMenu('admin_overview')).toBe(false)
  })

  it('教学态侧栏不含管理项（15 项平铺的回归守卫）', () => {
    const teachOnly = navItemsFor(SCHOOL_ADMIN.menus || []).filter((i) => !isAdminKey(i.key))
    expect(teachOnly.every((i) => !i.key.startsWith('admin_'))).toBe(true)
    expect(teachOnly.length).toBe((TEACHER.menus || []).length + 1)  // + integration
  })

  it('管理态按五组聚合，空组不出现', () => {
    const groups = groupAdminNav(SCHOOL_ADMIN.menus || [], MENU_REGISTRY, ADMIN_GROUP_OF)
    expect(groups.map((g) => g.key)).toEqual(['overview', 'org', 'content', 'ops', 'system'])
    const academic = groupAdminNav(ACADEMIC_ADMIN.menus || [], MENU_REGISTRY, ADMIN_GROUP_OF)
    expect(academic.map((g) => g.key)).toEqual(['overview', 'org'])
  })
})
