/* ParentHomePage — 家长主面板（design/家长端.md）：
 * 三 Tab（概览/学习报告/消息）+ 绑定管理 + AI 助手对话覆盖层（persona=parent）。
 * Wise 视觉：白卡 24px 圆角坐鼠尾草底；报告 hero 48px/900（R-P-T1/L1）；
 * 涨跌徽章 positive-pale 深字（R-P-C2）；AI 气泡保持品牌青（R-P-C3）。 */

import { useCallback, useEffect, useState } from 'react'
import {
  User, Link2, Link2Off, Users, TrendingUp, TrendingDown, Minus,
  Lightbulb, Search, X, MessageCirclePlus, MessagesSquare, Trash2,
} from 'lucide-react'
import { api } from '../../../lib/api'
import { getMobileUser, type MobileUser } from '../../../lib/mobile-auth'
import { MobileLayout, MobileHeader, MobileTabBar, MobileFullPane, MobileLoading, MobileEmpty, PARENT_TABS } from '../../../components/mobile/MobileLayout'
import { SummaryPane, FeedPane } from './panes'
import { SettingsPane } from './settings-pane'
import { ChatPanel } from '../../../components/mobile/ChatPanel'

interface Conversation {
  thread_id: string
  title?: string
  preview?: string
  last_at?: string
}

interface Child {
  student_id: string
  student_name?: string
  grade?: string
  class_name?: string
  relation?: string
  binding_id?: string
}

interface ChildReport {
  success?: boolean
  report?: {
    exercises_completed?: number
    last_exercise_at?: string
    barrier_type?: Record<string, number>
  }
}

interface Weekly {
  success?: boolean
  weekly?: {
    practice_count?: number
    practice_completed?: number
    accuracy_rate?: number
    streak_days?: number
    weak_knowledge_points?: string[]
    week_start?: string
    week_end?: string
  }
}

type TabId = 'home' | 'report' | 'notif' | 'settings'

function greeting() {
  const h = new Date().getHours()
  return h < 6 ? '夜深了' : h < 12 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
}


const BARRIER_NAMES: Record<string, string> = { concept: '概念理解', reading: '审题仔细', expression: '答题表述' }
const BARRIER_TIPS: Record<string, string> = {
  concept: '建议帮孩子建立知识框架图，用思维导图梳理化学概念之间的联系。',
  reading: '建议每天陪孩子读一道题，让他把题目条件说出来。这能有效改善审题习惯。',
  expression: '鼓励孩子写完整的解题过程，化学符号和方程式要写规范。',
}

export function ParentHomePage() {
  const [user] = useState<MobileUser | null>(() => getMobileUser() as MobileUser | null)
  const [children, setChildren] = useState<Child[]>([])
  const [active, setActive] = useState<Child | null>(null)
  const [tab, setTab] = useState<TabId>('home')
  const [loaded, setLoaded] = useState(false)
  const [bindOpen, setBindOpen] = useState(false)
  const [manageOpen, setManageOpen] = useState(false)
  const [bindSid, setBindSid] = useState('')
  const [bindCode, setBindCode] = useState('')
  const [chatOpen, setChatOpen] = useState(false)
  const [parentCid, setParentCid] = useState(() => localStorage.getItem('chemai_parent_cid') || `p-${Date.now()}`)

  useEffect(() => {
    document.title = '家长面板'
    localStorage.setItem('chemai_parent_cid', parentCid)
  }, [parentCid])

  /* 历史会话（ADR-0029 批⑦后补齐：与学生端同款，prefix=p- + 归属过滤） */
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [conversations, setConversations] = useState<Conversation[] | null>(null)
  const loadConversations = useCallback(() => {
    api.get<{ conversations?: Conversation[] }>(`/api/agent/chat/conversations?prefix=p-`)
      .then((d) => setConversations((d.conversations || []).sort(
        (a, b) => ((b.last_at || '') > (a.last_at || '') ? 1 : -1))))
      .catch(() => setConversations([]))
  }, [])

  function newConversation() {
    api.post<{ thread_id: string }>('/api/agent/chat/new?prefix=p-')
      .then((d) => {
        setParentCid(d.thread_id)
        setDrawerOpen(false)
        loadConversations()
      })
      .catch(() => {})
  }

  function deleteConversation(tid: string) {
    setConversations((prev) => (prev ? prev.filter((c) => c.thread_id !== tid) : prev))
    api.del(`/api/agent/chat/conversations/${tid}`).catch(() => {})
  }

  /* 打开聊天覆盖层时拉一次历史列表 */
  useEffect(() => {
    if (chatOpen) loadConversations()
  }, [chatOpen, loadConversations])

  const loadChildren = useCallback(() => {
    if (!user) return
    api.get<{ success?: boolean; children?: Child[] }>(
      `/api/parent/children?parent_id=${encodeURIComponent(user.id)}`,
    )
      .then((d) => {
        const list = d.success && d.children ? d.children : []
        setChildren(list)
        setActive(list[0] || null)
        setLoaded(true)
      })
      .catch(() => setLoaded(true))
  }, [user])

  useEffect(() => loadChildren(), [loadChildren])

  function doBind() {
    if (!user || !bindSid.trim() || !bindCode.trim()) return
    api.post<{ success?: boolean; message?: string; detail?: string }>('/api/parent/bind', {
      student_id: bindSid.trim(), bind_code: bindCode.trim(), parent_id: user.id, relation: '家长',
    })
      .then((d) => {
        if (d.success) {
          setBindOpen(false)
          setBindSid('')
          setBindCode('')
          loadChildren()
        } else {
          alert(d.message || d.detail || '绑定失败')
        }
      })
      .catch(() => alert('网络错误，请重试'))
  }

  function doUnbind(bindingId: string, name: string) {
    if (!user || !confirm(`确定解除与 ${name} 的绑定？解除后家长端将不再显示该学生的学习数据。`)) return
    api.del<{ success?: boolean; message?: string }>(
      `/api/parent/bind/${bindingId}?parent_id=${encodeURIComponent(user.id)}`,
    )
      .then((d) => {
        if (d.success) {
          setManageOpen(false)
          loadChildren()
        } else alert(d.message || '解绑失败')
      })
  }

  if (!user) return null

  const tabs = PARENT_TABS.map((t) => ({
    id: t.id,
    label: t.label,
    icon: t.icon,
    active: tab === t.id,
    onClick: () => setTab(t.id as TabId),
  }))

  return (
    <MobileLayout
      theme="parent"
      header={
        <MobileHeader
          title={
            <span>
              <span className="block text-micro text-ink-muted font-normal leading-none">{greeting()}</span>
              <span className="text-[16px]">{user.name || '家长'}</span>
            </span>
          }
          left={
            <div className="w-9 h-9 rounded-full bg-primary-soft flex items-center justify-center">
              <User size={17} className="text-primary" />
            </div>
          }
          right={
            <div className="flex items-center gap-1">
              <button
                onClick={() => (children.length > 0 ? setManageOpen(true) : setBindOpen(true))}
                className="p-2 rounded-full hover:bg-surface-muted cursor-pointer text-ink-secondary"
                title={children.length > 0 ? '管理绑定' : '绑定孩子'}
              >
                {children.length > 0 ? <Link2Off size={17} /> : <Link2 size={17} />}
              </button>

            </div>
          }
        />
      }
      footer={
        children.length > 0 ? (
          <>
            {/* 孩子条 */}
            <div className="flex items-center gap-2.5 px-4 py-2 bg-surface border-t border-border/60">
              <div className="w-8 h-8 rounded-full bg-primary-soft flex items-center justify-center text-[14px] font-black text-primary font-display">
                {(active?.student_name || '?')[0]}
              </div>
              <div>
                <div className="text-sub font-semibold text-ink leading-tight">{active?.student_name}</div>
                <div className="text-micro text-ink-muted">{active?.grade} {active?.class_name}</div>
              </div>
            </div>
            <MobileTabBar tabs={tabs} />
          </>
        ) : undefined
      }
    >
      {!loaded ? (
        <MobileLoading />
      ) : !active ? (
        /* 未绑定空态（R-S2：给行动） */
        <MobileEmpty
          icon={<Users size={34} />}
          title="还没有绑定孩子"
          hint={<>绑定后可以查看孩子的<br />学习报告和成长动态</>}
          action={
            <button
              onClick={() => setBindOpen(true)}
              className="px-9 py-3.5 rounded-[var(--radius-card)] bg-primary text-on-primary text-[15px] font-bold"
            >
              立即绑定
            </button>
          }
        />
      ) : tab === 'home' ? (
        <SummaryPane studentId={active.student_id} />
      ) : tab === 'report' ? (
        <ReportPane child={active} parentId={user.id} />
      ) : tab === 'notif' ? (
        <FeedPane parentId={user.id} />
      ) : (
        <SettingsPane />
      )}

      {/* 问问 AI 助手（悬浮按钮，R-P-C3：入口保留定案） */}
      {active && (
        <button
          onClick={() => setChatOpen(true)}
          className="fixed bottom-24 right-[max(16px,calc(50%-200px))] z-30 flex items-center gap-1.5 h-11 px-5 rounded-full bg-teal text-white text-[14px] font-semibold shadow-[var(--shadow-md)] active:scale-95 transition-transform"
        >
          问问 AI 助手
        </button>
      )}

      {/* AI 对话覆盖层 */}
      {chatOpen && (
        <div className="fixed inset-0 z-50 flex justify-center bg-bg">
          <div data-theme="parent" className="w-full max-w-[430px] h-[100dvh] flex flex-col bg-bg">
            <MobileHeader
              title="AI 家长助手"
              left={
                <button onClick={() => setChatOpen(false)} className="p-1.5 -ml-1.5 hover:bg-surface-muted rounded-full cursor-pointer" aria-label="返回">
                  <X size={20} />
                </button>
              }
              right={
                <div className="flex items-center gap-1">
                  <button onClick={newConversation} className="p-2 rounded-full hover:bg-surface-muted cursor-pointer text-ink-secondary" aria-label="新建对话" title="新建对话">
                    <MessageCirclePlus size={18} />
                  </button>
                  <button onClick={() => { setDrawerOpen(true); loadConversations() }} className="p-2 rounded-full hover:bg-surface-muted cursor-pointer text-ink-secondary" aria-label="历史对话" title="历史对话">
                    <MessagesSquare size={18} />
                  </button>
                </div>
              }
            />
            <ChatPanel
              persona="parent"
              cid={parentCid}
              studentId={active?.student_id || ''}
              placeholder="输入问题…"
              quickPrompts={['孩子这周学习怎么样?', '需要重点关注什么?', '在家怎么帮孩子?']}
              intro={
                <div className="text-center py-16 px-8">
                  <p className="text-[14px] text-ink-secondary leading-[1.8]">
                    我是 ChemAI 家长助手。
                    <br />
                    可以问我孩子的学习情况、
                    <br />
                    成长空间、以及在家怎么配合。
                  </p>
                </div>
              }
            />
          </div>
        </div>
      )}

      {/* 历史会话抽屉（z-60 压过聊天覆盖层） */}
      {drawerOpen && (
        <>
          <div className="fixed inset-0 z-[60] bg-black/40" onClick={() => setDrawerOpen(false)} />
          <div className="fixed top-0 left-0 bottom-0 z-[60] w-[85%] max-w-[360px] bg-surface shadow-[var(--shadow-lg)] flex flex-col rounded-r-2xl" data-theme="parent">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border/60">
              <span className="font-semibold text-[15px] text-ink">历史对话</span>
              <div className="flex items-center gap-1">
                <button
                  onClick={newConversation}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-full border border-primary text-primary text-[12px] bg-transparent cursor-pointer"
                >
                  <MessageCirclePlus size={13} />
                  新建
                </button>
                <button
                  onClick={() => setDrawerOpen(false)}
                  className="p-1.5 rounded-full hover:bg-surface-muted cursor-pointer text-ink-muted"
                  aria-label="关闭"
                >
                  <X size={18} />
                </button>
              </div>
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {conversations === null ? (
                <p className="text-center text-sub text-ink-muted py-8">加载中…</p>
              ) : conversations.length === 0 ? (
                <p className="text-center text-sub text-ink-muted py-8">暂无历史对话</p>
              ) : (
                conversations.map((c) => (
                  <div
                    key={c.thread_id}
                    onClick={() => {
                      setParentCid(c.thread_id)
                      setDrawerOpen(false)
                    }}
                    className={`group flex items-center gap-2 px-3 py-2.5 rounded-lg mb-0.5 cursor-pointer transition-colors ${
                      c.thread_id === parentCid ? 'bg-primary-soft' : 'hover:bg-surface-muted'
                    }`}
                  >
                    <MessagesSquare size={14} className="text-ink-muted shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="text-sub text-ink truncate">{c.title || '家长对话'}</div>
                      {c.preview && <div className="text-caption text-ink-muted truncate">{c.preview}</div>}
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); deleteConversation(c.thread_id) }}
                      className="p-1.5 rounded-full text-ink-muted hover:bg-surface-muted cursor-pointer opacity-0 group-hover:opacity-100"
                      aria-label="删除对话"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))
              )}
            </div>
          </div>
        </>
      )}

      {/* 绑定弹窗 */}
      {bindOpen && (
        <div className="fixed inset-0 z-50 bg-black/30 flex items-center justify-center p-6" onClick={(e) => e.target === e.currentTarget && setBindOpen(false)}>
          <div className="w-full max-w-[390px] bg-surface rounded-[var(--radius-card)] p-6 shadow-[var(--shadow-lg)]">
            <h3 className="text-[17px] font-bold text-ink mb-4">绑定孩子</h3>
            <label className="block text-sub text-ink-secondary mb-1">学生 ID（学号）</label>
            <input value={bindSid} onChange={(e) => setBindSid(e.target.value)} placeholder="输入孩子的学号，如 202610101" className="w-full h-12 px-4 mb-3 rounded-xl border-[1.5px] border-ink bg-surface text-[15px] text-ink placeholder:text-ink-muted/70 outline-none focus:border-primary" />
            <label className="block text-sub text-ink-secondary mb-1">绑定码（由学生在自己客户端获取）</label>
            <input value={bindCode} onChange={(e) => setBindCode(e.target.value)} placeholder="6 位数字" maxLength={6} className="w-full h-12 px-4 rounded-xl border-[1.5px] border-ink bg-surface text-[15px] text-ink placeholder:text-ink-muted/70 outline-none focus:border-primary" />
            <div className="flex gap-2.5 mt-4">
              <button onClick={() => setBindOpen(false)} className="flex-1 h-12 rounded-[var(--radius-card)] bg-surface-muted text-ink-secondary text-[15px] font-semibold">取消</button>
              <button onClick={doBind} className="flex-1 h-12 rounded-[var(--radius-card)] bg-primary text-on-primary text-[15px] font-bold">确认绑定</button>
            </div>
          </div>
        </div>
      )}

      {/* 绑定管理弹窗 */}
      <MobileFullPane open={manageOpen} title="已绑定的孩子" onClose={() => setManageOpen(false)}>
        <div className="space-y-2.5">
          {children.map((c) => (
            <div key={c.binding_id} className="flex items-center justify-between p-4 rounded-[var(--radius-card)] bg-surface">
              <div>
                <div className="font-bold text-[15px] text-ink">{c.student_name}</div>
                <div className="text-[12px] text-ink-muted">{c.grade} {c.class_name} · {c.relation || '家长'}</div>
              </div>
              <button
                onClick={() => doUnbind(c.binding_id || '', c.student_name || '')}
                className="px-3.5 py-1.5 rounded-full border border-error/40 text-error text-[12.5px] bg-surface cursor-pointer"
              >
                解绑
              </button>
            </div>
          ))}
          <button
            onClick={() => {
              setManageOpen(false)
              setBindOpen(true)
            }}
            className="w-full h-12 mt-2 rounded-[var(--radius-card)] bg-surface-muted text-ink-secondary text-[14px] font-semibold"
          >
            + 绑定其他孩子
          </button>
        </div>
      </MobileFullPane>
    </MobileLayout>
  )
}

/* ── 概览 Tab ── */
/* 下钻（2026-10，§11 B3）：报告页此前每块都是"只能看、点不动"的静态卡，
   只有 SummaryPane 三卡能弹明细。这里对齐同一套底部详情层，明细全部取自
   已返回的字段（不改后端）。 */
type ReportDetail = 'accuracy' | 'week' | 'streak' | 'total' | 'weak' | 'barrier'

const DETAIL_TITLES: Record<ReportDetail, string> = {
  accuracy: '本周正确率',
  week: '本周练习量',
  streak: '连续打卡',
  total: '累计完成练习',
  weak: '需要加强的知识点',
  barrier: '学习特点与家庭配合',
}

/* 导出以便 DOM 级验收（§11 B3 的界面证据）——与 panes.tsx 里三个 Pane 同口径 */
export function ReportPane({ child, parentId }: { child: Child; parentId: string }) {
  const [report, setReport] = useState<ChildReport | null>(null)
  const [weekly, setWeekly] = useState<Weekly | null>(null)
  const [loading, setLoading] = useState(true)
  const [detail, setDetail] = useState<ReportDetail | null>(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      api.get<ChildReport>(`/api/parent/child/${child.student_id}/report?parent_id=${encodeURIComponent(parentId)}`),
      api.get<Weekly>(`/api/parent/child/${child.student_id}/weekly?parent_id=${encodeURIComponent(parentId)}`),
    ])
      .then(([r, w]) => {
        setReport(r)
        setWeekly(w)
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [child.student_id, parentId])

  if (loading) return <MobileLoading />
  if (!report?.success) return <MobileEmpty title="暂无报告数据" hint="孩子完成练习后这里会出现学习报告" />

  const r = report.report || {}
  const w = weekly?.weekly || {}
  const accuracy = Math.round((w.accuracy_rate ?? 0) * 100)
  const barrier = r.barrier_type || {}
  const dominant = Object.entries(barrier).sort((a, b) => (b[1] || 0) - (a[1] || 0))[0]?.[0] || 'concept'
  const weakKps = (w.weak_knowledge_points || []).slice(0, 5)
  const total = w.practice_count ?? 0
  const correct = w.practice_completed ?? 0
  const wrong = Math.max(total - correct, 0)
  const period = w.week_start && w.week_end ? `${w.week_start} ~ ${w.week_end}` : '近 7 天'
  const noWeak = !weakKps.length || (weakKps.length === 1 && weakKps[0].startsWith('暂无'))
  const barrierPct = (['concept', 'reading', 'expression'] as const)
    .map((k) => ({ k, pct: Math.round((barrier[k] ?? 0) * 100) }))

  /* 可点卡片的交互三件套：光标态 + 键盘可达 + 点击开明细（与 SummaryPane 的 CardBox 一致）
     className 各卡不同，只在 JSX 里各写一次 */
  const tapProps = (k: ReportDetail) => ({
    onClick: () => setDetail(k),
    onKeyDown: (e: React.KeyboardEvent<HTMLDivElement>) => {
      if (e.key === 'Enter' || e.key === ' ') setDetail(k)
    },
    role: 'button' as const,
    tabIndex: 0,
  })
  const tappable = 'cursor-pointer active:scale-[0.98] transition-transform'

  return (
    <div className="p-4 space-y-3">
      {/* hero：本周正确率 48px/900（R-P-T1） */}
      <div {...tapProps('accuracy')}
           className={`bg-surface rounded-[var(--radius-card)] p-5 shadow-sm ${tappable}`}>
        <div className="text-[12px] text-ink-muted mb-1.5">本周正确率</div>
        <div className="flex items-end gap-2">
          <span className="font-display text-[48px] font-black leading-none text-ink kpi-number">{accuracy}</span>
          <span className="text-[20px] font-black text-ink pb-0.5">%</span>
          <span className={`flex items-center gap-0.5 ml-2 mb-1 text-[11.5px] px-2.5 py-1 rounded-full font-semibold ${accuracy >= 60 ? 'bg-success-soft text-[var(--color-success)]' : 'bg-warning-soft text-warning'}`}>
            {accuracy >= 60 ? <TrendingUp size={12} /> : accuracy > 0 ? <TrendingDown size={12} /> : <Minus size={12} />}
            {accuracy >= 60 ? '稳步提升' : accuracy > 0 ? '需要加油' : '暂无数据'}
          </span>
        </div>
      </div>

      {/* 学习概览 4 KPI */}
      <div className="grid grid-cols-2 gap-2.5">
        {([
          { k: 'week' as const, v: w.practice_count ?? r.exercises_completed ?? 0, l: '本周练习', u: '道' },
          { k: 'streak' as const, v: w.streak_days ?? 0, l: '连续打卡', u: '天' },
          { k: 'total' as const, v: r.exercises_completed ?? 0, l: '已完成练习', u: '道' },
          { k: 'weak' as const, v: noWeak ? 0 : weakKps.length, l: '薄弱知识点', u: '个' },
        ]).map((s) => (
          <div key={s.l} {...tapProps(s.k)}
               className={`bg-surface rounded-[var(--radius-card)] py-4 text-center shadow-sm ${tappable}`}>
            <div className="font-display text-[26px] font-black text-ink leading-none kpi-number">
              {s.v}
              <span className="text-[12px] font-normal text-ink-muted ml-0.5">{s.u}</span>
            </div>
            <div className="text-micro text-ink-muted mt-1.5">{s.l}</div>
          </div>
        ))}
      </div>

      {/* 知识点掌握 */}
      <div {...tapProps('weak')}
           className={`bg-surface rounded-[var(--radius-card)] p-5 shadow-sm ${tappable}`}>
        <div className="flex items-center gap-1.5 text-[14px] font-bold text-ink mb-2.5">
          <Search size={15} className="text-primary" />需要加强的知识点
        </div>
        {weakKps.length ? (
          <div className="flex flex-wrap gap-1.5">
            {weakKps.map((k) => (
              <span key={k} className="text-[12px] px-3 py-1 rounded-full bg-success-soft text-[var(--color-success)] font-medium">{k}</span>
            ))}
          </div>
        ) : (
          <p className="text-sub text-ink-muted">暂无薄弱数据，表现很稳！</p>
        )}
      </div>

      {/* 学习特点 + 家庭配合建议 */}
      <div {...tapProps('barrier')}
           className={`bg-surface rounded-[var(--radius-card)] p-5 shadow-sm ${tappable}`}>
        <div className="flex items-center gap-1.5 text-[14px] font-bold text-ink mb-3">
          <Lightbulb size={15} className="text-primary" />家庭配合建议
        </div>
        <p className="text-sub leading-relaxed text-ink-secondary">
          <b className="text-ink">{BARRIER_NAMES[dominant]}</b>是需要重点关注的方面。{BARRIER_TIPS[dominant]}
          多问问孩子"今天学到了什么有趣的东西"，而不是"考了多少分"。肯定努力比关注分数更重要。
        </p>
      </div>

      {detail && (
        <div className="fixed inset-0 z-50 bg-black/35 flex items-end justify-center" onClick={() => setDetail(null)}>
          <div className="w-full max-w-[430px] bg-bg rounded-t-[var(--radius-card)] p-5 pb-8 max-h-[70dvh] overflow-y-auto"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-2">
              <h3 className="text-[16px] font-semibold text-ink">{DETAIL_TITLES[detail]}</h3>
              <button onClick={() => setDetail(null)} className="text-ink-muted text-[13px]">关闭</button>
            </div>

            {detail === 'accuracy' && (total > 0 ? (
              <>
                <p className="text-[13.5px] text-ink leading-relaxed">
                  本周共练 <b>{total}</b> 道，答对 <b>{correct}</b> 道、答错 <b>{wrong}</b> 道。
                </p>
                <p className="text-[13px] text-ink-secondary mt-2">统计区间：{period}（滚动近 7 天）</p>
                <p className="text-[12.5px] text-ink-muted mt-3 leading-relaxed">
                  正确率 = 答对数 ÷ 练习数。题量少时这个百分比会跳，看趋势比看一次的数字可靠。
                </p>
              </>
            ) : (
              <p className="text-[13px] text-ink-muted">本周还没有练习记录，孩子开始做题后这里会有数字。</p>
            ))}

            {detail === 'week' && (
              <>
                <p className="text-[13.5px] text-ink leading-relaxed">
                  本周（{period}）共练 <b>{total}</b> 道；累计完成 <b>{r.exercises_completed ?? 0}</b> 道。
                </p>
                <p className="text-[12.5px] text-ink-muted mt-3 leading-relaxed">
                  「本周」是滚动近 7 天，不是自然周一到周日——和老师发来的周报日期对不上是正常的。
                </p>
              </>
            )}

            {detail === 'streak' && (
              <>
                <p className="text-[13.5px] text-ink">当前连击 <b>{w.streak_days ?? 0} 天</b></p>
                <p className="text-[12.5px] text-ink-muted mt-3 leading-relaxed">
                  连击 = 连续每天完成练习。断签不影响已经掌握的知识点，但会让复习节奏断开。
                </p>
              </>
            )}

            {detail === 'total' && (
              <>
                <p className="text-[13.5px] text-ink">累计完成 <b>{r.exercises_completed ?? 0}</b> 道练习。</p>
                <p className="text-[13px] text-ink-secondary mt-2">
                  最近一次练习：{r.last_exercise_at ? new Date(r.last_exercise_at).toLocaleString('zh-CN', { hour12: false }) : '暂无记录'}
                </p>
                <p className="text-[12.5px] text-ink-muted mt-3 leading-relaxed">
                  这个数是开通以来累计的，不受「本周」区间影响。
                </p>
              </>
            )}

            {detail === 'weak' && (
              <>
                {noWeak ? (
                  <p className="text-[13px] text-ink-muted">近 7 天没有错题，暂时看不出薄弱知识点。</p>
                ) : (
                  <div className="space-y-1.5">
                    {weakKps.map((k, i) => (
                      <p key={k} className="text-[13.5px] text-ink py-1.5 border-b border-border/40 last:border-0">
                        {i + 1}. {k}
                      </p>
                    ))}
                  </div>
                )}
                <p className="text-[12.5px] text-ink-muted mt-3 leading-relaxed">
                  按近 7 天错题对应的知识点排序，最多 5 个；越靠前说明出错次数越多。
                </p>
              </>
            )}

            {detail === 'barrier' && (
              <>
                {barrierPct.some((b) => b.pct > 0) ? (
                  <div className="space-y-2.5">
                    {barrierPct.map(({ k, pct }) => (
                      <div key={k}>
                        <div className="flex justify-between text-[13px] text-ink-secondary mb-1">
                          <span>{BARRIER_NAMES[k]}</span><span>{pct}%</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-surface-muted overflow-hidden">
                          <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-[13px] text-ink-muted">还没有诊断数据，孩子做完练习后会有。</p>
                )}
                <p className="text-[13px] text-ink-secondary mt-3 leading-relaxed">
                  占比来自最近一次障碍诊断：<b>{BARRIER_NAMES[dominant]}</b> 占主导，所以建议优先从这一块入手。
                </p>
                <p className="text-[12.5px] text-ink-muted mt-2 leading-relaxed">{BARRIER_TIPS[dominant]}</p>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/* ── 消息 Tab ── */
