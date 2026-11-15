/* panes.tsx — 家长端三个自包含 Pane（批 3 P1/P2 / REQ-GDS-001/005、REQ-PFD-002、REQ-PAT-003）。
 * SummaryPane 三卡摘要（dashboard/parent 的 cards）+ 错因人话卡（ai-summary + 消灭率 +
 * 一件事）；FeedPane 动态流（点赞 + 类型图标）；SettingsPane 摘要频率 + 外发订阅。
 * 从 ParentHomePage 的内联实现拆出——每 Pane 自取数据，父组件只传身份。 */

import { useCallback, useEffect, useState } from 'react'
import {
  Bell, CheckCircle2, ChevronRight, Clock, Flame,
  Lightbulb, Loader2, Mail, Sparkles, Target, ThumbsUp,
} from 'lucide-react'
import { api } from '../../../lib/api'
import { MobileEmpty, MobileLoading } from '../../../components/mobile/MobileLayout'

interface Cards {
  unfinished: { count: number; items: { practice_id?: string; title: string }[] }
  due_soon: { items: { title: string; deadline: string }[] }
  week_activity: { accuracy?: number; accuracy_delta?: number; completed?: number; streak_days?: number }
}

/* ── SummaryPane：三卡摘要（REQ-GDS-001/005）────────────────────────── */

export function SummaryPane({ studentId }: { studentId: string }) {
  const [cards, setCards] = useState<Cards | null>(null)
  const [loading, setLoading] = useState(true)
  const [plain, setPlain] = useState('')
  const [plainLoading, setPlainLoading] = useState(true)
  const [elim, setElim] = useState<{ eliminated: number; total: number } | null>(null)
  /* 三卡下钻（2026-10 反馈：家长端所有面板都是"只能看、点不动"的静态卡）。
     点任一卡弹底部详情层，把该卡背后的明细摊开。 */
  const [detail, setDetail] = useState<'unfinished' | 'due' | 'streak' | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.get<{ cards?: Cards }>(`/api/parent/child/${encodeURIComponent(studentId)}/summary`)
      .then((d) => {
        setCards(d.cards || null)
        setLoading(false)
      })
      .catch(() => setLoading(false))
    api.get<{ eliminated?: number; total?: number }>(
      `/api/analytics/student/${encodeURIComponent(studentId)}/elimination`)
      .then((d) => setElim({ eliminated: d.eliminated ?? 0, total: d.total ?? 0 }))
      .catch(() => {})
  }, [studentId])

  useEffect(() => {
    load()
  }, [load])

  /* 错因人话（REQ-GDS-005）：ai-summary 端点，挂载即取一次
   *（div 无 tabindex，onFocus 不会触发——审查修正）。
   * 失败显式显示错误 + 保留重试按钮——NEVER 静默吞掉（2026-10-07 用户实测） */
  const [plainErr, setPlainErr] = useState('')
  useEffect(() => {
    api.post<{ summary?: string; text?: string }>(
      `/api/parent/child/${encodeURIComponent(studentId)}/report/ai-summary`,
      { section: 'weekly', data: { student_id: studentId } })
      .then((d) => {
        setPlain(d.summary || d.text || '')
        setPlainErr('')
      })
      .catch((e: Error) => {
        setPlain('')
        setPlainErr(e.message || '生成失败，请稍后重试')
      })
      .finally(() => setPlainLoading(false))
  }, [studentId])

  const loadPlain = () => {
    if (plain || plainLoading) return
    setPlainLoading(true)
    setPlainErr('')
    api.post<{ summary?: string; text?: string }>(
      `/api/parent/child/${encodeURIComponent(studentId)}/report/ai-summary`,
      { section: 'weekly', data: { student_id: studentId } })
      .then((d) => {
        setPlain(d.summary || d.text || '')
        setPlainErr('')
      })
      .catch((e: Error) => {
        setPlain('')
        setPlainErr(e.message || '生成失败，请稍后重试')
      })
      .finally(() => setPlainLoading(false))
  }

  if (loading) return <MobileLoading />
  if (!cards) return <MobileEmpty title="暂无摘要" hint="孩子开始学习后，这里会出现本周摘要" />

  const act = cards.week_activity || {}
  const unfinished = cards.unfinished || { count: 0, items: [] }
  const due = cards.due_soon || { items: [] }
  const canDo = unfinished.count > 0
    ? '提醒孩子先清掉未完成的练习任务'
    : (act.streak_days ?? 0) > 0 ? '保持节奏：今天别忘了清复习卡'
    : '陪孩子聊聊今天学了什么'

  return (
    <div className="p-3.5 space-y-3">
      {/* 三卡（刻意不放分数） */}
      <div className="grid grid-cols-3 gap-2">
        <CardBox icon={<Target size={16} className="text-error" />} label="未完成"
          value={`${unfinished.count} 项`} onClick={() => setDetail('unfinished')} />
        <CardBox icon={<Clock size={16} className="text-warning" />} label="即将到期"
          value={due.items[0] ? due.items[0].title.slice(0, 6) : '无'} onClick={() => setDetail('due')} />
        <CardBox icon={<Flame size={16} className="text-warning" />} label="连击"
          value={`${act.streak_days ?? 0} 天`} onClick={() => setDetail('streak')} />
      </div>
      {unfinished.items.length > 0 && (
        <div className="p-3 surface-card space-y-1">
          {unfinished.items.map((i, idx) => (
            <div key={idx} className="text-sub text-ink-secondary flex items-center gap-1.5">
              <ChevronRight size={13} className="text-ink-muted" />{i.title}
            </div>
          ))}
        </div>
      )}

      {/* 错因人话卡（ai-summary + 消灭率 + 一件事） */}
      <div className="p-3.5 surface-card">
        <div className="flex items-center gap-2 mb-2">
          <Lightbulb size={16} className="text-warning" />
          <span className="text-body font-semibold text-ink">错因解读</span>
          {elim && elim.total > 0 && (
            <span className="ml-auto text-caption text-ink-muted">消灭率 {elim.eliminated}/{elim.total}</span>
          )}
        </div>
        {plain ? (
          <p className="text-sub text-ink-secondary leading-relaxed">{plain}</p>
        ) : (
          <>
            <button onClick={loadPlain} disabled={plainLoading}
              className="text-sub text-primary flex items-center gap-1">
              {plainLoading ? <Loader2 size={13} className="animate-spin" /> : <Sparkles size={13} />}
              {plainLoading ? '生成中…' : plainErr ? '重试生成一句话解读' : '生成一句话解读'}
            </button>
            {plainErr && <p className="text-caption text-error mt-1.5">{plainErr}</p>}
          </>
        )}
        <div className="mt-2.5 pt-2.5 border-t border-border/50 flex items-start gap-1.5">
          <CheckCircle2 size={15} className="text-success shrink-0 mt-0.5" />
          <p className="text-sub text-ink-secondary">现在能帮上的一件事：{canDo}</p>
        </div>
      </div>

      {detail && (
        <div className="fixed inset-0 z-50 bg-black/35 flex items-end justify-center" onClick={() => setDetail(null)}>
          <div className="w-full max-w-[430px] bg-bg rounded-t-[var(--radius-card)] p-5 pb-8 max-h-[70dvh] overflow-y-auto"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-2">
              <h3 className="text-[16px] font-semibold text-ink">
                {detail === 'unfinished' ? '未完成的练习' : detail === 'due' ? '即将到期' : '复习连击'}
              </h3>
              <button onClick={() => setDetail(null)} className="text-ink-muted text-[13px]">关闭</button>
            </div>
            {detail === 'unfinished' && (
              unfinished.items.length
                ? <div className="space-y-1.5">{unfinished.items.map((i, k) => (
                    <p key={k} className="text-[13.5px] text-ink py-1.5 border-b border-border/40 last:border-0">{i.title}</p>
                  ))}</div>
                : <p className="text-[13px] text-ink-muted">没有未完成的练习。</p>
            )}
            {detail === 'due' && (
              due.items.length
                ? <div className="space-y-1.5">{due.items.map((i, k) => (
                    <p key={k} className="text-[13.5px] text-ink py-1.5 border-b border-border/40 last:border-0">{i.title}</p>
                  ))}</div>
                : <p className="text-[13px] text-ink-muted">近期没有到期任务。</p>
            )}
            {detail === 'streak' && (
              <div className="space-y-2">
                <p className="text-[14px] text-ink">当前连击 <strong>{act.streak_days ?? 0} 天</strong></p>
                <p className="text-[13px] text-ink-secondary leading-relaxed">
                  连击 = 连续每天完成当日到期复习。断签不会影响已掌握的知识点，但会让复习节奏断开。
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function CardBox({ icon, label, value, onClick }:
  { icon: React.ReactNode; label: string; value: string; onClick?: () => void }) {
  return (
    <div
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === 'Enter' || e.key === ' ') onClick() } : undefined}
      className={`p-3 surface-card text-center ${onClick ? 'cursor-pointer active:scale-[0.98] transition-transform' : ''}`}
    >
      <div className="flex justify-center mb-1">{icon}</div>
      {/* 卡片关键数字：title 档 + 900（R-P-T1）；label 用 caption——40+ 家长可读 */}
      <div className="text-title font-black text-ink leading-tight">{value}</div>
      <div className="text-caption text-ink-muted mt-0.5">{label}</div>
    </div>
  )
}

/* ── FeedPane：动态流（REQ-PFD-002）────────────────────────────────── */

interface FeedItem {
  notification_id: string
  student_name?: string
  type: string
  title: string
  content?: string
  is_read?: boolean
  liked?: boolean
  created_at: string
}

const FEED_ICON: Record<string, { icon: typeof Bell; cls: string }> = {
  artifact_shared: { icon: Sparkles, cls: 'text-teal bg-teal-soft' },
  practice_completed: { icon: CheckCircle2, cls: 'text-success bg-success-soft' },
  streak_milestone: { icon: Flame, cls: 'text-warning bg-warning-soft' },
  weekly_report: { icon: Mail, cls: 'text-primary bg-primary-soft' },
  daily_report: { icon: Mail, cls: 'text-primary bg-primary-soft' },
}

export function FeedPane({ parentId }: { parentId: string }) {
  const [items, setItems] = useState<FeedItem[] | null>(null)
  const [liking, setLiking] = useState<Record<string, boolean>>({})
  /* 2026-10 反馈：家长端所有消息都是"只能看标题、只能点赞"的静面板，没有任何可点开
     看详情的地方。这里补一层详情浮层——行整行可点，正文（content）与完整时间摊开。 */
  const [openItem, setOpenItem] = useState<FeedItem | null>(null)

  const load = useCallback(() => {
    api.get<{ notifications?: FeedItem[] }>(
      `/api/notification/parent/${encodeURIComponent(parentId)}/notifications?limit=30`)
      .then((d) => setItems(d.notifications || []))
      .catch(() => setItems([]))
  }, [parentId])

  useEffect(() => {
    load()
  }, [load])

  const like = (id: string) => {
    setLiking((prev) => ({ ...prev, [id]: true }))
    api.put(`/api/notification/${encodeURIComponent(id)}/like`, {})
      .then(() => load())
      .catch(() => {})
      .finally(() => setLiking((prev) => ({ ...prev, [id]: false })))
  }

  if (items === null) return <MobileLoading />
  if (items.length === 0) {
    return <MobileEmpty icon={<Bell size={30} />} title="还没有动态"
      hint="孩子练习、看讲解、复习连击都会在这里出现" />
  }
  return (
    <div className="p-3.5 space-y-2">
      {items.map((n) => {
        const conf = FEED_ICON[n.type] || { icon: Bell, cls: 'text-ink-muted bg-surface-muted' }
        const Icon = conf.icon
        return (
          <div key={n.notification_id}
            role="button"
            tabIndex={0}
            onClick={() => setOpenItem(n)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setOpenItem(n) }}
            className="p-3.5 surface-card flex items-start gap-2.5 cursor-pointer active:scale-[0.99] transition-transform">
            <span className={`w-8 h-8 rounded-[10px] flex items-center justify-center shrink-0 ${conf.cls}`}>
              <Icon size={14} />
            </span>
            <div className="flex-1 min-w-0">
              <p className="text-sub text-ink leading-snug break-words">{n.title}</p>
              <p className="text-[10.5px] text-ink-muted mt-0.5">
                {n.student_name && `${n.student_name} · `}
                {n.created_at ? new Date(n.created_at).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : ''}
              </p>
            </div>
            <button
              onClick={(e) => { e.stopPropagation(); like(n.notification_id) }}
              disabled={liking[n.notification_id]}
              className={`shrink-0 flex items-center gap-1 px-2 py-1 rounded-full text-micro border transition-colors ${
                n.liked ? 'border-error/40 bg-error-soft text-error' : 'border-border text-ink-muted'
              } disabled:opacity-40`}
              aria-label="点赞"
            >
              <ThumbsUp size={11} />{n.liked ? '已赞' : '赞'}
            </button>
          </div>
        )
      })}

      {openItem && (
        <div className="fixed inset-0 z-50 bg-black/35 flex items-end justify-center"
             onClick={() => setOpenItem(null)}>
          <div className="w-full max-w-[430px] bg-bg rounded-t-[var(--radius-card)] p-5 pb-8 max-h-[75dvh] overflow-y-auto"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3">
              <h3 className="text-[16px] font-semibold text-ink leading-snug">{openItem.title}</h3>
              <button onClick={() => setOpenItem(null)} className="text-ink-muted text-[13px] shrink-0" aria-label="关闭">关闭</button>
            </div>
            <p className="mt-1 text-[11px] text-ink-muted">
              {openItem.student_name ? openItem.student_name + ' · ' : ''}
              {openItem.created_at ? new Date(openItem.created_at).toLocaleString('zh-CN') : ''}
            </p>
            <p className="mt-3 text-[13.5px] text-ink leading-relaxed whitespace-pre-wrap break-words">
              {openItem.content || '这条动态没有更多正文。'}
            </p>
            <button
              onClick={() => { like(openItem.notification_id) }}
              disabled={liking[openItem.notification_id]}
              className={`mt-4 flex items-center justify-center gap-1.5 w-full py-2.5 rounded-[var(--radius-md)] text-[13px] border transition-colors ${
                openItem.liked ? 'border-error/40 bg-error-soft text-error' : 'border-border text-ink-secondary'
              } disabled:opacity-40`}
            >
              <ThumbsUp size={13} />{openItem.liked ? '已赞' : '点个赞'}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
