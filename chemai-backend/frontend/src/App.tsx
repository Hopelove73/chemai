import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { AnimatePresence, MotionConfig } from 'framer-motion'
import { pageTransition } from './lib/motion'
import { HashRouter, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, Atom, ChevronDown, ChevronRight, LogOut, Loader2, Settings } from 'lucide-react'
import { clearAuth, getToken, hasMenu } from './lib/api'
import { getMobileUser } from './lib/mobile-auth'
import { useCapabilities } from './lib/use-capabilities'
import { activeAdminGroup, groupAdminNav, isAdminKey } from './lib/admin-nav'
import { ToastProvider } from './components/ui'
import { AntdProvider } from './components/antd/AntdProvider'
import { assets } from './assets'
import { ChatPage } from './pages/teacher/ChatPage'
import { ArtifactsPage } from './pages/teacher/ArtifactsPage'
import { LoginPage } from './pages/teacher/LoginPage'
import { NoAccessPage } from './pages/teacher/NoAccessPage'

/* 路由级代码分割：图表/大页面按需加载（echarts/katex 不进首屏包） */
const ClassPage = lazy(() => import('./pages/teacher/ClassPage').then((m) => ({ default: m.ClassPage })))
const StudentsPage = lazy(() => import('./pages/teacher/StudentsPage').then((m) => ({ default: m.StudentsPage })))
const ExamPage = lazy(() => import('./pages/teacher/ExamPage').then((m) => ({ default: m.ExamPage })))
const DiagnosisPage = lazy(() => import('./pages/teacher/DiagnosisPage').then((m) => ({ default: m.DiagnosisPage })))
const OcrPage = lazy(() => import('./pages/teacher/OcrPage').then((m) => ({ default: m.OcrPage })))
const OcrSheetsPage = lazy(() => import('./pages/teacher/OcrSheetsPage').then((m) => ({ default: m.OcrSheetsPage })))
const IntegrationPage = lazy(() =>
  import('./pages/teacher/IntegrationPage').then((m) => ({ default: m.IntegrationPage })),
)
const PendingGatesPage = lazy(() =>
  import('./pages/teacher/PendingGatesPage').then((m) => ({ default: m.PendingGatesPage })),
)
const KnowledgeMapAdminPage = lazy(() =>
  import('./pages/teacher/KnowledgeMapAdminPage').then((m) => ({ default: m.KnowledgeMapAdminPage })),
)
/* ── 管理端（admin-console-p0）：菜单 gate 在后端 MENUS，元素符号见其 design D8 ── */
const AdminOverviewPage = lazy(() =>
  import('./pages/admin/AdminOverviewPage').then((m) => ({ default: m.AdminOverviewPage })),
)
const RagTracesPage = lazy(() =>
  import('./pages/admin/RagTracesPage').then((m) => ({ default: m.RagTracesPage })),
)
const RagRetrievalPage = lazy(() =>
  import('./pages/admin/RagRetrievalPage').then((m) => ({ default: m.RagRetrievalPage })),
)
const RagEvalsPage = lazy(() =>
  import('./pages/admin/RagEvalsPage').then((m) => ({ default: m.RagEvalsPage })),
)
const AdminRolesPage = lazy(() =>
  import('./pages/admin/AdminRolesPage').then((m) => ({ default: m.AdminRolesPage })),
)
const AdminParentsPage = lazy(() =>
  import('./pages/admin/AdminParentsPage').then((m) => ({ default: m.AdminParentsPage })),
)
const AdminStudentsPage = lazy(() =>
  import('./pages/admin/AdminStudentsPage').then((m) => ({ default: m.AdminStudentsPage })),
)
const AdminTeachersPage = lazy(() =>
  import('./pages/admin/AdminTeachersPage').then((m) => ({ default: m.AdminTeachersPage })),
)
const AdminClassesPage = lazy(() =>
  import('./pages/admin/AdminClassesPage').then((m) => ({ default: m.AdminClassesPage })),
)
const AdminSchoolPage = lazy(() =>
  import('./pages/admin/AdminSchoolPage').then((m) => ({ default: m.AdminSchoolPage })),
)
const AdminLogsPage = lazy(() =>
  import('./pages/admin/AdminLogsPage').then((m) => ({ default: m.AdminLogsPage })),
)
const AdminKbPage = lazy(() =>
  import('./pages/admin/AdminKbPage').then((m) => ({ default: m.AdminKbPage })),
)
const BreakGlassPage = lazy(() =>
  import('./pages/admin/BreakGlassPage').then((m) => ({ default: m.BreakGlassPage })),
)
const AdminSettingsPage = lazy(() =>
  import('./pages/admin/AdminSettingsPage').then((m) => ({ default: m.AdminSettingsPage })),
)

/* ── 移动端（学生/家长）· ADR-0001 并入同一 SPA，主题按端作用域（design/分册） ── */
const StudentLoginPage = lazy(() => import('./pages/mobile/student/StudentLoginPage').then((m) => ({ default: m.StudentLoginPage })))
const StudentChatPage = lazy(() => import('./pages/mobile/student/StudentChatPage').then((m) => ({ default: m.StudentChatPage })))
const PracticePage = lazy(() => import('./pages/mobile/student/PracticePage').then((m) => ({ default: m.PracticePage })))
const WrongBookPage = lazy(() => import('./pages/mobile/student/WrongBookPage').then((m) => ({ default: m.WrongBookPage })))
const ReviewCenterPage = lazy(() => import('./pages/mobile/student/ReviewCenterPage').then((m) => ({ default: m.ReviewCenterPage })))
const ProfilePage = lazy(() => import('./pages/mobile/student/ProfilePage').then((m) => ({ default: m.ProfilePage })))
const KnowledgeMapPage = lazy(() => import('./pages/mobile/student/KnowledgeMapPage').then((m) => ({ default: m.KnowledgeMapPage })))
const StudentArtifactPage = lazy(() => import('./pages/student/StudentArtifactPage').then((m) => ({ default: m.StudentArtifactPage })))
const ParentLoginPage = lazy(() => import('./pages/mobile/parent/ParentLoginPage').then((m) => ({ default: m.ParentLoginPage })))
const ParentHomePage = lazy(() => import('./pages/mobile/parent/ParentHomePage').then((m) => ({ default: m.ParentHomePage })))

/* 菜单注册表：菜单 key（后端下发）→ 展示信息。
   权限单一真源在后端（`app/middleware/auth.py` 的 MENUS），
   展示细节单一真源在这里 —— 元素符号、中文名、路径都是前端的事。
   导航顺序沿用后端下发的顺序（见 navItemsFor）。
   `nav: false` = 这一项有路由、有权限 key，但**不在侧栏占一格**（先例：`/ocr` 挂在
   判卷中心那个 key 下）。待确认队列走的是这一格：它是 Agent 工作台内部的事，
   入口是工作台顶栏那颗带数量的按钮，不是与工作台平级的侧栏项。 */
export const MENU_REGISTRY: Record<
  string,
  { path: string; label: string; symbol: string; nav?: boolean }
> = {
  chat: { path: '/chat', label: 'Agent 工作台', symbol: 'H' },
  pending: { path: '/pending', label: '待确认', symbol: 'Pd', nav: false },
  artifacts: { path: '/artifacts', label: '我的工件', symbol: 'Au', nav: false },
  class: { path: '/class', label: '班级学情', symbol: 'O' },
  students: { path: '/students', label: '学生管理', symbol: 'C' },
  exam: { path: '/exam', label: '出题工作台', symbol: 'Fe' },
  diagnosis: { path: '/diagnosis', label: '诊断分析', symbol: 'Cu' },
  ocr: { path: '/ocr-sheets', label: '判卷中心', symbol: 'Ag' },
  integration: { path: '/integration', label: 'LMS 集成', symbol: 'Zn' },
  kmap: { path: '/kmap', label: '知识星图', symbol: 'Km' },
  /* 管理端（admin-console-p0）：gate 在后端 MENUS（admin_overview=student:create、admin_teachers=teacher:create、
     admin_classes=class:update、admin_school=school:update、admin_logs=log:read），
     只有 admin/academic_admin（及镜像的超管）会收到这些 key。 */
  admin_overview: { path: '/admin', label: '管理概览', symbol: 'Pt' },
  admin_students: { path: '/admin/students', label: '学生账号', symbol: 'Sr' },
  admin_parents: { path: '/admin/parents', label: '家长与绑定', symbol: 'Pa' },
  admin_teachers: { path: '/admin/teachers', label: '教师账号', symbol: 'Mn' },
  admin_classes: { path: '/admin/classes', label: '班级与年级', symbol: 'Pb' },
  admin_school: { path: '/admin/school', label: '学校设置', symbol: 'Sc' },
  admin_roles: { path: '/admin/roles', label: '角色与权限', symbol: 'Rl' },
  admin_logs: { path: '/admin/logs', label: '操作日志', symbol: 'Rn' },
  admin_kb: { path: '/admin/kb', label: 'KB 生产', symbol: 'B' },
  rag_traces: { path: '/admin/rag/traces', label: '问答日志', symbol: 'Qt' },
  rag_retrieval: { path: '/admin/rag/retrieval', label: '召回测试', symbol: 'Rt' },
  rag_evals: { path: '/admin/rag/evals', label: '评测回归', symbol: 'Ev' },
  break_glass: { path: '/admin/break-glass', label: '平台提权', symbol: 'Fr' },
  admin_settings: { path: '/admin/settings', label: '运行时开关', symbol: 'Tc' },
}

/** 管理菜单 → 分组（admin-shell-v2）。分组是展示细节，与 MENUS 的 group 字段保持同名同义；
 *  写在前端是为了让组序/组名可独立演进，权限真源仍在后端。 */
export const ADMIN_GROUP_OF: Record<string, string> = {
  admin_overview: 'overview',
  admin_students: 'org',
  admin_parents: 'org',
  admin_teachers: 'org',
  admin_classes: 'org',
  admin_kb: 'content',
  rag_traces: 'content',
  rag_retrieval: 'content',
  rag_evals: 'content',
  admin_logs: 'ops',
  break_glass: 'ops',
  admin_settings: 'ops',
  admin_school: 'system',
  admin_roles: 'system',
}

/** 侧栏没有自己那一格的页面 → 它算在谁头上（点亮哪个侧栏项）。键是精确路径。 */
export const NAV_OWNER: Record<string, string> = {
  '/pending': '/chat',
  '/artifacts': '/chat',
  '/ocr-sheets': '/ocr',
}

/** 路由 → 它归哪个菜单管。守卫按这个判断「这个角色能不能进这一页」。 */
export const ROUTE_MENU: Record<string, string> = {
  '/chat': 'chat',
  '/pending': 'pending',
  '/artifacts': 'artifacts',
  '/class': 'class',
  '/students': 'students',
  '/exam': 'exam',
  '/diagnosis': 'diagnosis',
  '/ocr': 'ocr', // 答题卡识别页没有独立导航入口，与判卷中心同一个菜单 key
  '/ocr-sheets': 'ocr',
  '/integration': 'integration',
  '/kmap': 'kmap',
  '/admin': 'admin_overview',
  '/admin/students': 'admin_students',
  '/admin/parents': 'admin_parents',
  '/admin/teachers': 'admin_teachers',
  '/admin/classes': 'admin_classes',
  '/admin/school': 'admin_school',
  '/admin/roles': 'admin_roles',
  '/admin/logs': 'admin_logs',
  '/admin/kb': 'admin_kb',
  '/admin/rag/traces': 'rag_traces',
  '/admin/rag/retrieval': 'rag_retrieval',
  '/admin/rag/evals': 'rag_evals',
  '/admin/break-glass': 'break_glass',
  '/admin/settings': 'admin_settings',
}

/**
 * 后端下发的菜单 key 列表 → 渲染用的导航项。
 *
 * 顺序沿用后端下发的顺序（菜单排序是权限侧的决定）。
 * `nav: false` 的 key 不在此列（它有路由与权限，只是不占侧栏一格）。
 * 认不出的 key **跳过但告警** —— 前后端版本不一致时不崩，但也不能悄悄少一个菜单：
 * 静默丢弃会表现成「后端加了菜单、前端就是不显示」，很难查。
 */
export function navItemsFor(menuKeys: string[]): NavItem[] {
  return menuKeys
    .map((key) => {
      const meta = MENU_REGISTRY[key]
      if (!meta) {
        console.warn(
          `[menu] 后端下发了前端不认识的菜单 key "${key}" —— 需要在 App.tsx 的 MENU_REGISTRY 补上对应页面`,
        )
        return null
      }
      if (meta.nav === false) return null
      return { key, path: meta.path, label: meta.label, symbol: meta.symbol }
    })
    .filter((item): item is NavItem => item !== null)
}

export type NavItem = { key: string; path: string; label: string; symbol: string }

/** 路由过渡（R-M1 签名①）：AnimatePresence + location key，进出对称 250ms */
function AnimatedOutlet({ className }: { className?: string }) {
  const location = useLocation()
  return (
    <AnimatePresence mode="popLayout" initial={false}>
      {/* Suspense 边界在过渡元素内：懒加载 chunk 未就绪时旧页保持挂载，
          不让整棵 Routes 解绑到全屏 fallback（code review P1-7） */}
      <Suspense fallback={<main className={className}><PageFallback /></main>}>
        <main key={location.pathname} {...pageTransition} className={className}>
          <Outlet />
        </main>
      </Suspense>
    </AnimatePresence>
  )
}

/* 管理态「返回教学」记住的最后教学页（sessionStorage：刷新不丢，关标签即清） */
const LAST_TEACH_PATH_KEY = 'chemai_last_teach_path'
const ADMIN_EXPANDED_KEY = 'chemai_admin_expanded'

/** 单个导航格：教学态与管理态共用同一套皮肤（v2 元素符号 tile 惯例）。 */
function NavButton({
  item, isActive, onClick,
}: { item: NavItem; isActive: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-[var(--radius-md)] text-sm transition-colors cursor-pointer text-left ${
        isActive
          ? 'bg-primary-soft text-primary font-medium'
          : 'text-ink-secondary hover:bg-white/60 hover:text-ink'
      }`}
    >
      <span
        aria-hidden
        className={`w-7 h-7 rounded-[var(--radius-sm)] flex items-center justify-center font-mono text-sub font-semibold shrink-0 transition-colors ${
          isActive ? 'btn-gradient text-white' : 'bg-white/80 text-ink'
        }`}
      >
        {item.symbol}
      </span>
      <span className="max-[1099px]:hidden">{item.label}</span>
    </button>
  )
}

function Shell({ items }: { items: NavItem[] }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { caps } = useCapabilities()

  /* 双模式（admin-shell-v2）：**模式由路由前缀决定，不用额外状态** ——
     深链接、刷新、e2e 都可预测；用开关状态会出现「URL 在 /admin、侧栏却是教学」的不一致。 */
  const isAdminMode = location.pathname.startsWith('/admin')

  /* 教学态侧栏只放教学项：管理 key 只在管理态出现（此前 15 项平铺的根因） */
  const teachItems = useMemo(() => items.filter((i) => !isAdminKey(i.key)), [items])
  const adminGroups = useMemo(
    () => groupAdminNav(
      caps?.menus ?? [],
      MENU_REGISTRY,
      // 后端下发的 key→group 是首选；老版本缓存（或端点未升级）回落到本地常量
      Object.keys(caps?.menu_groups ?? {}).length ? (caps!.menu_groups as Record<string, string>) : ADMIN_GROUP_OF,
    ),
    [caps],
  )

  /* 高亮匹配：精确路径或路径段边界 + **最长路径优先**（/admin/teachers 不点亮 /admin）。 */
  const activePath = NAV_OWNER[location.pathname] ?? location.pathname
  const matches = (p: string) => p === activePath || activePath.startsWith(p + '/')
  const active = [...items]
    .filter((n) => matches(n.path))
    .sort((a, b) => b.path.length - a.path.length)[0]

  // Agent 工作台独占视口高度（消息区内部滚动、输入框钉底），其余页面整页滚动
  const isAgentWorkbench = location.pathname.startsWith('/chat')

  /* 记住最后一个教学页：管理态「返回教学」要回到这里（直接进 /admin 时默认 /chat） */
  useEffect(() => {
    if (!isAdminMode) sessionStorage.setItem(LAST_TEACH_PATH_KEY, location.pathname)
  }, [isAdminMode, location.pathname])

  /* 分组折叠：默认全收起，当前页所在组自动展开；会话内记住用户的展开状态 */
  const [expanded, setExpanded] = useState<string[]>(() => {
    try {
      const raw = sessionStorage.getItem(ADMIN_EXPANDED_KEY)
      return raw ? (JSON.parse(raw) as string[]) : []
    } catch {
      return []
    }
  })
  const activeGroup = activeAdminGroup(adminGroups, location.pathname)
  useEffect(() => {
    if (activeGroup && !expanded.includes(activeGroup)) {
      setExpanded((prev) => (prev.includes(activeGroup) ? prev : [...prev, activeGroup]))
    }
  }, [activeGroup, expanded])
  useEffect(() => {
    sessionStorage.setItem(ADMIN_EXPANDED_KEY, JSON.stringify(expanded))
  }, [expanded])

  function handleLogout() {
    clearAuth()
    navigate('/login', { replace: true })
  }

  function toggleGroup(key: string) {
    setExpanded((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
  }

  function backToTeaching() {
    navigate(sessionStorage.getItem(LAST_TEACH_PATH_KEY) || '/chat')
  }

  /* 页脚当前页名：管理态用「管理中心 · 当前页」，教学态沿用原逻辑 */
  const footerLabel = isAdminMode
    ? `管理中心${active && isAdminKey(active.key) ? ` · ${active.label}` : ''}`
    : (active?.label ?? '教师工作台')

  return (
    <div
      className="h-full flex bg-cover bg-center"
      style={{ backgroundImage: `url(${assets.mainBg})` }}
    >
      {/* 侧边栏（品牌底图 navBg + 玻璃质感，v2 细节保留） */}
      <aside
        className="w-[var(--sidebar-width)] max-[1099px]:w-14 shrink-0 bg-cover bg-center border-r border-border flex flex-col max-[1099px]:items-center transition-[width] duration-[var(--duration-normal)]"
        style={{ backgroundImage: `url(${assets.navBg})` }}
      >
        {/* 管理态：顶部「返回教学」（模式边界，1 次点击可逆） */}
        {isAdminMode && (
          <button
            onClick={backToTeaching}
            className="mx-2.5 mt-2.5 flex items-center gap-2 px-3 py-2 rounded-[var(--radius-md)] text-sm text-ink-secondary hover:bg-white/60 hover:text-ink transition-colors cursor-pointer max-[1099px]:justify-center max-[1099px]:px-1"
            title="返回教学"
          >
            <ArrowLeft size={16} />
            <span className="max-[1099px]:hidden">返回教学</span>
          </button>
        )}

        <div className="px-5 pt-5 pb-4 flex items-center gap-2.5 max-[1099px]:px-2 max-[1099px]:pt-4">
          <div aria-hidden className="w-9 h-9 rounded-[var(--radius-md)] bg-primary text-on-primary flex items-center justify-center shrink-0">
            <Atom size={20} strokeWidth={2.2} />
          </div>
          <div className="max-[1099px]:hidden">
            <div className="text-[15px] font-semibold text-ink leading-tight">智辅化学</div>
            <div className="text-[10px] text-ink-muted tracking-widest mt-0.5">
              {isAdminMode ? 'CHEMAI · 管理中心' : 'CHEMAI · 教师端'}
            </div>
          </div>
        </div>

        <nav className="flex-1 overflow-y-auto px-2.5 py-2 space-y-0.5">
          {isAdminMode ? (
            adminGroups.map((group) => {
              const open = expanded.includes(group.key)
              return (
                <div key={group.key} className="pt-1">
                  <button
                    onClick={() => toggleGroup(group.key)}
                    aria-expanded={open}
                    className="w-full flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium text-ink-muted hover:text-ink-secondary transition-colors cursor-pointer max-[1099px]:hidden"
                  >
                    {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    {group.label}
                  </button>
                  {open && group.items.map((item) => (
                    <NavButton
                      key={item.key}
                      item={item}
                      isActive={active?.path === item.path}
                      onClick={() => navigate(item.path)}
                    />
                  ))}
                </div>
              )
            })
          ) : (
            teachItems.map((item) => (
              <NavButton
                key={item.key}
                item={item}
                isActive={active?.path === item.path}
                onClick={() => navigate(item.path)}
              />
            ))
          )}
        </nav>

        <div className="px-2.5 pb-2 max-[1099px]:hidden">
          {/* 管理中心入口：仅当后端下发了至少一个管理菜单时出现（不判角色名） */}
          {!isAdminMode && adminGroups.length > 0 && (
            <button
              onClick={() => navigate('/admin')}
              className="w-full flex items-center gap-3 px-3 py-2.5 rounded-[var(--radius-md)] text-sm text-ink-secondary hover:bg-white/60 hover:text-ink transition-colors cursor-pointer text-left border border-dashed border-border-strong"
            >
              <span aria-hidden className="w-7 h-7 rounded-[var(--radius-sm)] flex items-center justify-center bg-white/80 text-ink shrink-0">
                <Settings size={14} />
              </span>
              管理中心
              <ChevronRight size={14} className="ml-auto text-ink-muted" />
            </button>
          )}
        </div>

        <div className="px-4 py-3 border-t border-border">
          <div className="flex items-center justify-between">
            <span className="text-xs text-ink-muted max-[1099px]:hidden">{footerLabel}</span>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1 text-xs text-ink-secondary hover:text-error transition-colors cursor-pointer"
              title="退出登录"
            >
              <LogOut size={14} />
              退出
            </button>
          </div>
        </div>
      </aside>

      {/* 主区域 */}
      <main className={`flex-1 min-w-0 ${isAgentWorkbench ? 'overflow-hidden' : 'overflow-y-auto'}`}>
        <AnimatedOutlet
          className={isAgentWorkbench ? 'h-full' : 'max-w-[var(--container-max)] mx-auto p-6 lg:p-8'}
        />
      </main>
    </div>
  )
}

/**
 * 需登录的布局路由。
 *
 * 三种状态要分开处理，别合并：
 *   - 没有 token          → 弹回登录页
 *   - 在拉能力清单        → 转圈（别先渲染再闪走）
 *   - 有身份但没有菜单    → 「无教师端权限」页（学生/家长），**不能**弹回登录页
 */
function ProtectedLayout() {
  const { caps, loading } = useCapabilities()
  if (!getToken()) return <Navigate to="/login" replace />
  if (loading) return <PageFallback />
  if (!caps || caps.menus.length === 0) return <NoAccessPage />
  return <Shell items={navItemsFor(caps.menus)} />
}

/**
 * 路由守卫：该角色没有这一页对应的菜单 → 送到它能进的第一页。
 *
 * ⚠️ 这是 **UX 一致性**，不是安全边界 —— 真正的边界在后端端点（阶段 4 已落地）。
 * 守卫写错只会让人看到点不动的页面，不会漏数据。
 */
function RequireMenu({ menuKey, children }: { menuKey: string; children: ReactNode }) {
  const { caps } = useCapabilities()
  if (!hasMenu(menuKey)) {
    const fallback = navItemsFor(caps?.menus ?? [])[0]?.path
    return fallback ? <Navigate to={fallback} replace /> : <NoAccessPage />
  }
  return <>{children}</>
}

/** 移动端会话守卫（学生/家长各自登录页；用户画像在 sessionStorage，e2e 兼容旧约定） */
function RequireMobile({ login }: { login: string }) {
  if (!getToken() || !sessionStorage.getItem('chemai_user')) return <Navigate to={login} replace />
  return <Outlet />
}

/** 学生端路由守卫：家长（以及教师端角色）误入学生 UI 时送去各自的端。
 *
 *  F4 现场：家长从 `/m/login` 登录后一路落在学生端（AI助教/练习/复习/错题），
 *  学生页又把 `user.id` 当学号调 `/api/review/student/parent_xxx/count` → 403，
 *  家长问「孩子这周学习怎么样」只得到「无权访问该学生」。家长端页面本身早就
 *  正确传 `studentId=active.student_id`，缺的只是这道分流。
 *  与 RequireMenu 同样只是 UX 一致性，真正的边界在后端（ADR-0014）。
 */
function RequireStudentMobile() {
  const user = getMobileUser() as { role?: string } | null
  if (user?.role === 'parent') return <Navigate to="/m/parent" replace />
  return <Outlet />
}

function PageFallback() {
  return (
    <div className="flex items-center justify-center py-24">
      <Loader2 size={22} className="animate-spin text-teal" />
    </div>
  )
}

/** 旧路径 /admin/users → /admin/teachers，**保留查询串**（spec：书签与筛选参数不丢）。 */
function LegacyAdminUsersRedirect() {
  const location = useLocation()
  return <Navigate to={{ pathname: '/admin/teachers', search: location.search }} replace />
}

/** 兜底跳转：落到该角色能进的第一个菜单。 */
function HomeRedirect() {
  const { caps } = useCapabilities()
  const first = navItemsFor(caps?.menus ?? [])[0]?.path
  return first ? <Navigate to={first} replace /> : <NoAccessPage />
}

export function App() {
  return (
    <AntdProvider>
      <ToastProvider>
        <MotionConfig reducedMotion="user">
        <HashRouter>
          <Suspense fallback={<PageFallback />}>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route element={<ProtectedLayout />}>
                <Route path="/chat" element={<RequireMenu menuKey="chat"><ChatPage /></RequireMenu>} />
                <Route path="/pending" element={<RequireMenu menuKey="pending"><PendingGatesPage /></RequireMenu>} />
                <Route path="/artifacts" element={<RequireMenu menuKey="artifacts"><ArtifactsPage /></RequireMenu>} />
                <Route path="/class" element={<RequireMenu menuKey="class"><ClassPage /></RequireMenu>} />
                <Route path="/students" element={<RequireMenu menuKey="students"><StudentsPage /></RequireMenu>} />
                <Route path="/exam" element={<RequireMenu menuKey="exam"><ExamPage /></RequireMenu>} />
                <Route path="/diagnosis" element={<RequireMenu menuKey="diagnosis"><DiagnosisPage /></RequireMenu>} />
                <Route path="/ocr" element={<RequireMenu menuKey="ocr"><OcrPage /></RequireMenu>} />
                <Route path="/ocr-sheets" element={<RequireMenu menuKey="ocr"><OcrSheetsPage /></RequireMenu>} />
                <Route path="/integration" element={<RequireMenu menuKey="integration"><IntegrationPage /></RequireMenu>} />
                <Route path="/kmap" element={<RequireMenu menuKey="kmap"><KnowledgeMapAdminPage /></RequireMenu>} />
                {/* ── 管理端（admin-shell-v2）：守卫与安全边界都在后端矩阵，这里只管 UX 一致性 ── */}
                <Route path="/admin" element={<RequireMenu menuKey="admin_overview"><AdminOverviewPage /></RequireMenu>} />
                <Route path="/admin/students" element={<RequireMenu menuKey="admin_students"><AdminStudentsPage /></RequireMenu>} />
                <Route path="/admin/parents" element={<RequireMenu menuKey="admin_parents"><AdminParentsPage /></RequireMenu>} />
                <Route path="/admin/teachers" element={<RequireMenu menuKey="admin_teachers"><AdminTeachersPage /></RequireMenu>} />
                <Route path="/admin/classes" element={<RequireMenu menuKey="admin_classes"><AdminClassesPage /></RequireMenu>} />
                {/* 旧路径兼容：书签与文档里的 /admin/users 仍可用 */}
                <Route path="/admin/users" element={<LegacyAdminUsersRedirect />} />
                <Route path="/admin/school" element={<RequireMenu menuKey="admin_school"><AdminSchoolPage /></RequireMenu>} />
                <Route path="/admin/roles" element={<RequireMenu menuKey="admin_roles"><AdminRolesPage /></RequireMenu>} />
                <Route path="/admin/logs" element={<RequireMenu menuKey="admin_logs"><AdminLogsPage /></RequireMenu>} />
                <Route path="/admin/kb" element={<RequireMenu menuKey="admin_kb"><AdminKbPage /></RequireMenu>} />
                <Route path="/admin/rag/traces" element={<RequireMenu menuKey="rag_traces"><RagTracesPage /></RequireMenu>} />
                <Route path="/admin/rag/retrieval" element={<RequireMenu menuKey="rag_retrieval"><RagRetrievalPage /></RequireMenu>} />
                <Route path="/admin/rag/evals" element={<RequireMenu menuKey="rag_evals"><RagEvalsPage /></RequireMenu>} />
                <Route path="/admin/break-glass" element={<RequireMenu menuKey="break_glass"><BreakGlassPage /></RequireMenu>} />
                <Route path="/admin/settings" element={<RequireMenu menuKey="admin_settings"><AdminSettingsPage /></RequireMenu>} />
                <Route path="*" element={<HomeRedirect />} />
              </Route>

              {/* ── 移动端 · 学生（深色单彩，design/学生端.md） ── */}
              <Route path="/m/login" element={<StudentLoginPage />} />
              <Route element={<RequireMobile login="/m/login" />}>
                <Route element={<RequireStudentMobile />}>
                  <Route path="/m" element={<StudentChatPage />} />
                  <Route path="/m/practice" element={<PracticePage />} />
                  <Route path="/m/wrong" element={<WrongBookPage />} />
                  <Route path="/m/review" element={<ReviewCenterPage />} />
                  <Route path="/m/map" element={<KnowledgeMapPage />} />
                  <Route path="/m/artifact/:artifactId" element={<StudentArtifactPage />} />
                  <Route path="/m/me" element={<ProfilePage />} />
                </Route>
              </Route>

              {/* ── 移动端 · 家长（鼠尾草浅色，design/家长端.md） ── */}
              <Route path="/m/parent-login" element={<ParentLoginPage />} />
              <Route element={<RequireMobile login="/m/parent-login" />}>
                <Route path="/m/parent" element={<ParentHomePage />} />
              </Route>

              <Route path="*" element={<Navigate to="/chat" replace />} />
            </Routes>
          </Suspense>
        </HashRouter>
        </MotionConfig>
      </ToastProvider>
    </AntdProvider>
  )
}
