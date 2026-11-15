/* MobileLayout.tsx — 移动端布局壳（ADR-0001）：430 视口容器 + 主题作用域 + 槽位。
 * 主题按端注入 data-theme（mobile-themes.css 覆盖 Tailwind 变量，两端互不渗漏）。
 * R-L2：430×900 视口设计；底部安全区留白。
 * S4（student-daily-loop）：底栏 tabs 单一真源——STUDENT_TABS / PARENT_TABS 在此导出，
 * 页面只引用不定义（此前 5 处各持一份）。 */

import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { BackgroundArt, type BackgroundIntensity } from '../ui/BackgroundArt'
import { Loader2, MessageCircle, PenLine, RotateCcw, BookX, User, BarChart3, BookOpenCheck, Send, Settings, ChevronLeft } from 'lucide-react'

export type MobileTheme = 'student' | 'parent'

/** 底栏 tab 定义（数据与行为分离：页面负责 active/onClick/badge 接线） */
export interface MobileTabDef {
  id: string
  label: string
  /** 学生端为跳转路径；家长端为内部 tab 态（页面自行映射 onClick） */
  path?: string
  icon: ReactNode
}

/** 学生端底栏（含「复习」tab——S1 到期徽章挂点，REQ-RCS-004） */
export const STUDENT_TABS: MobileTabDef[] = [
  { id: 'chat', label: 'AI助教', path: '/m', icon: <MessageCircle size={22} /> },
  { id: 'practice', label: '练习', path: '/m/practice', icon: <PenLine size={22} /> },
  { id: 'review', label: '复习', path: '/m/review', icon: <RotateCcw size={22} /> },
  { id: 'wrong', label: '错题', path: '/m/wrong', icon: <BookX size={22} /> },
  { id: 'me', label: '我的', path: '/m/me', icon: <User size={22} /> },
]

/** 学生端底栏接线工厂：五个页面共用一份样板（S4 单一真源的另一半——
 * 数据在 STUDENT_TABS，接线也只有这一处），dueCount 只落在「复习」tab。 */
export function studentTabs(
  activeId: string,
  navigate: (path: string) => void,
  dueCount = 0,
): MobileTab[] {
  return STUDENT_TABS.map((t) => ({
    id: t.id,
    label: t.label,
    icon: t.icon,
    onClick: () => navigate(t.path || '/m'),
    active: t.id === activeId,
    badge: t.id === 'review' ? dueCount : undefined,
  }))
}

/** 家长端底栏（3-tab 变体，页内切换非路由） */
export const PARENT_TABS: MobileTabDef[] = [
  { id: 'home', label: '概览', icon: <BarChart3 size={20} /> },
  { id: 'report', label: '学习报告', icon: <BookOpenCheck size={20} /> },
  { id: 'notif', label: '消息', icon: <Send size={20} /> },
  /* 第 4 tab：SettingsPane（摘要频率 + 外发订阅）此前无入口，是死 UI（E2E E13 实测） */
  { id: 'settings', label: '设置', icon: <Settings size={20} /> },
]

export function MobileLayout({
  theme,
  header,
  footer,
  children,
  className = '',
  artIntensity,
  wide,
}: {
  theme: MobileTheme
  header?: ReactNode
  footer?: ReactNode
  children: ReactNode
  className?: string
  /** P-21 背景强度；缺省按端：学生沉浸 / 家长浅 */
  artIntensity?: BackgroundIntensity
  /** ≥768 双栏画布档（R-L5）：仅对话页用；壳放宽到 900 */
  wide?: boolean
}) {
  /* 字号偏好（2026-10）：小/中/大三档，存 localStorage 并即时生效。
     用 CSS zoom 缩放整个移动外壳，而不是逐个改 tailwind 字号 class ——
     后者要动几十处、且新增页面会漏。zoom 在移动端 Chrome/Safari 都可用。 */
  const [fontScale, setFontScale] = useState<number>(() => {
    const v = Number(localStorage.getItem('chemai_font_scale') || '1')
    return v === 0.9 || v === 1 || v === 1.12 ? v : 1
  })
  useEffect(() => {
    const onScale = () => {
      const v = Number(localStorage.getItem('chemai_font_scale') || '1')
      setFontScale(v === 0.9 || v === 1 || v === 1.12 ? v : 1)
    }
    window.addEventListener('chemai-font-scale', onScale)
    return () => window.removeEventListener('chemai-font-scale', onScale)
  }, [])

  return (
    <div
      data-theme={theme}
      style={{ zoom: fontScale }}
      className={[
        'relative flex flex-col bg-bg text-ink mx-auto overflow-hidden',
        'w-full max-w-[430px] h-[100dvh]',
        // ≥768：居中设备卡 + 环境留白（R-L5）
        'md:mt-6 md:mb-6 md:h-[min(880px,calc(100dvh-48px))] md:rounded-[28px] md:border md:border-border md:shadow-[var(--shadow-lg)]',
        wide ? 'md:w-[min(900px,calc(100vw-48px))] md:max-w-none' : '',
      ].join(' ')}
    >
      <BackgroundArt theme={theme} intensity={artIntensity ?? (theme === 'student' ? 'immersive' : 'subtle')} />
      <div className="relative">{header}</div>
      <div className={`relative flex-1 min-h-0 overflow-y-auto ${className}`}>{children}</div>
      {footer && (
        <footer className="relative shrink-0 pb-[env(safe-area-inset-bottom)]">{footer}</footer>
      )}
    </div>
  )
}

/* 底部 Tab 导航（P-04：56px，激活项主色；badge>0 时图标右上角计数，REQ-RCS-004） */
export interface MobileTab {
  id: string
  label: string
  icon: ReactNode
  onClick: () => void
  active: boolean
  badge?: number
}

export function MobileTabBar({ tabs }: { tabs: MobileTab[] }) {
  return (
    <nav className="flex bg-surface border-t border-border/60 h-14">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={t.onClick}
          className={`flex-1 flex flex-col items-center justify-center gap-0.5 text-[10px] transition-colors cursor-pointer relative ${
            t.active ? 'text-primary font-medium' : 'text-ink-muted'
          }`}
        >
          {t.active && (
            <span className="absolute top-0 left-1/2 -translate-x-1/2 w-5 h-[2.5px] rounded-b-[3px] bg-primary" />
          )}
          <span className="relative">
            {t.icon}
            {!!t.badge && t.badge > 0 && (
              <span className="absolute -top-1 -right-2 min-w-[15px] h-[15px] px-1 rounded-full bg-error text-white text-[9px] leading-[15px] text-center font-medium">
                {t.badge > 99 ? '99+' : t.badge}
              </span>
            )}
          </span>
          {t.label}
        </button>
      ))}
    </nav>
  )
}

/* 顶部标题栏（P-03 移动端 56px） */
/** 标准返回钮（P-03）：全屏子页头的左槽位；无历史可退时兜底 fallback 路由。 */
export function MobileBackButton({ fallback = '/m' }: { fallback?: string }) {
  const routerNavigate = useNavigate()
  return (
    <button
      onClick={() => (window.history.length > 1 ? routerNavigate(-1) : routerNavigate(fallback))}
      aria-label="返回"
      className="press-target -ml-1 flex h-9 w-9 items-center justify-center rounded-full text-ink-secondary"
    >
      <ChevronLeft size={20} />
    </button>
  )
}

export function MobileHeader({
  title,
  left,
  right,
  className = '',
}: {
  title: ReactNode
  left?: ReactNode
  right?: ReactNode
  className?: string
}) {
  return (
    <div className={`shrink-0 h-14 px-3 flex items-center gap-2 bg-bg border-b border-border/60 ${className}`}>
      {left}
      <div className="flex-1 min-w-0 font-semibold text-[16px] truncate">{title}</div>
      {right}
    </div>
  )
}

/* 全屏滑出面板（学习计划/报告/设置/对话等） */
export function MobileFullPane({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean
  title: ReactNode
  onClose: () => void
  children: ReactNode
}) {
  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex justify-center bg-bg">
      <div className="w-full max-w-[430px] h-[100dvh] flex flex-col bg-bg">
        <MobileHeader
          title={title}
          left={
            <button
              onClick={onClose}
              className="p-1.5 -ml-1.5 rounded-[var(--radius-sm)] hover:bg-surface-muted cursor-pointer"
              aria-label="返回"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m15 18-6-6 6-6" />
              </svg>
            </button>
          }
        />
        <div className="flex-1 min-h-0 overflow-y-auto p-4 pb-[max(16px,env(safe-area-inset-bottom))]">{children}</div>
      </div>
    </div>
  )
}

/* 骨架/加载/空态（P-10 / P-11：三态 MUST，空态给行动引导 R-S2） */
export function MobileLoading({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-10 text-ink-muted text-sm">
      <Loader2 size={16} className="animate-spin" />
      {text}
    </div>
  )
}

export function MobileEmpty({
  icon,
  title,
  hint,
  action,
}: {
  icon?: ReactNode
  title: string
  hint?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="text-center py-14 px-8">
      {icon && <div className="flex justify-center mb-3 text-ink-muted/70">{icon}</div>}
      <h3 className="text-[16px] font-semibold text-ink mb-1.5">{title}</h3>
      {hint && <p className="text-sub text-ink-secondary leading-relaxed">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/* overscroll 背景由 mobile-themes.css 的 body:has(...) 规则处理，无需 JS */
