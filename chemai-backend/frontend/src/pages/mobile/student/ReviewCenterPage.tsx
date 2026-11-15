/* ReviewCenterPage — 今日复习中心（student-daily-loop S1 / REQ-RCS-001..004）。
 * 消费 /api/review 全家桶：到期队列 → 重做（选择题自动判定）/自评（主观题三档）
 * → 下一次间隔；顶部复习连击卡（streak/冻结/修复）；底栏到期徽章同源。 */

import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { parseReviewFromChat } from '../../../lib/agent-handoff'
import { Flame, Snowflake, CheckCircle2, XCircle, Eye, RefreshCw, History } from 'lucide-react'
import { api } from '../../../lib/api'
import { getMobileUser } from '../../../lib/mobile-auth'
import { MobileLayout, MobileHeader, MobileTabBar, MobileLoading, MobileEmpty, MobileFullPane, MobileBackButton, STUDENT_TABS } from '../../../components/mobile/MobileLayout'
import { ChemMd } from '../../../components/ChemMd'
import { useReviewDue, refetchReviewDue } from '../../../lib/use-review-due'

interface ReviewTask {
  task_id: string
  question_id: string
  content: string
  options?: string[] | null
  answer?: string | null
  analysis?: string | null
  knowledge_points: string[]
  difficulty: string
  review_level: number
  next_review_at?: string | null
}

interface StreakInfo {
  current: number
  best: number
  freeze_count: number
  freeze_max: number
  cleared_today: boolean
  repair_available: boolean
}

interface HistoryRow {
  question_id?: string
  reviewed_at?: string
  is_correct?: boolean
  self_rating?: string
  [k: string]: unknown
}

const RATING_LABEL: Record<string, string> = { forgot: '忘了', vague: '模糊', remembered: '记得' }

export function ReviewCenterPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [user] = useState(() => getMobileUser())
  const dueCount = useReviewDue(user?.id)
  /* AI 助教接棒入口标记（ADR-0029）：复习中心主加载本就拉到期队列，
   * 这里只做来源提示 + 清 state 防重入，无行为差异。
   * useState 固化首渲染取值——清 state 后标记不消失 */
  const [fromChat] = useState(() => parseReviewFromChat(location.state))
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tasks, setTasks] = useState<ReviewTask[]>([])
  const [idx, setIdx] = useState(0)
  const [reviewing, setReviewing] = useState(false)
  const [revealed, setRevealed] = useState(false)
  const [feedback, setFeedback] = useState<{ ok: boolean; message: string } | null>(null)
  const [streak, setStreak] = useState<StreakInfo | null>(null)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [repairing, setRepairing] = useState(false)

  const sid = user?.id || ''

  const loadStreak = useCallback(() => {
    if (!sid) return
    api.get<StreakInfo>(`/api/review/streak/${encodeURIComponent(sid)}`)
      .then(setStreak)
      .catch(() => {})
  }, [sid])

  const loadDue = useCallback(() => {
    if (!sid) return
    setLoading(true)
    setError('')
    api.get<{ due_count?: number; tasks?: ReviewTask[] }>(
      `/api/review/student/${encodeURIComponent(sid)}/due`,
    )
      .then((d) => {
        setTasks(d.tasks || [])
        setIdx(0)
        setReviewing((d.tasks?.length || 0) > 0)
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
    loadStreak()
  }, [sid, loadStreak])

  useEffect(() => {
    document.title = '复习中心'
    loadDue()
  }, [loadDue])

  useEffect(() => {
    if (fromChat) {
      navigate(location.pathname, { replace: true, state: {} })  // 清 state 防重入
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fromChat])

  const submitReview = (payload: { is_correct?: boolean; self_rating?: string }) => {
    const t = tasks[idx]
    if (!t) return
    api.post<{ message: string; is_correct: boolean }>('/api/review/submit', {
      task_id: t.task_id, ...payload,
    })
      .then((d) => {
        setFeedback({ ok: payload.is_correct !== false, message: d.message || '已记录' })
        refetchReviewDue(sid, true)
        loadStreak()
      })
      .catch((e: Error) => setFeedback({ ok: false, message: e.message || '提交失败' }))
  }

  const next = () => {
    setFeedback(null)
    setRevealed(false)
    if (idx + 1 < tasks.length) {
      setIdx(idx + 1)
    } else {
      setReviewing(false)
      setTasks([])
      loadDue()
    }
  }

  const doRepair = () => {
    if (!sid) return
    setRepairing(true)
    api.post<{ message: string }>(`/api/review/streak/${encodeURIComponent(sid)}/repair`, {})
      .then((d) => {
        setFeedback({ ok: true, message: d.message })
        loadStreak()
      })
      .catch((e: Error) => setFeedback({ ok: false, message: e.message }))
      .finally(() => setRepairing(false))
  }

  const loadHistory = () => {
    if (!sid) return
    api.get<{ tasks?: HistoryRow[]; history?: HistoryRow[]; items?: HistoryRow[] }>(
      `/api/review/history/${encodeURIComponent(sid)}`,
    )
      .then((d) => setHistory(d.tasks || d.history || d.items || []))
      .catch(() => setHistory([]))
  }

  const t = tasks[idx]
  const isChoice = (t?.options?.length ?? 0) > 0

  const tabbar = (
    <MobileTabBar
      tabs={STUDENT_TABS.map((tab) => ({
        id: tab.id,
        label: tab.label,
        icon: tab.icon,
        onClick: () => navigate(tab.path || '/m'),
        active: tab.id === 'review',
        badge: tab.id === 'review' ? dueCount : undefined,
      }))}
    />
  )

  return (
    <MobileLayout
      theme="student"
      header={<MobileHeader title={<>复习中心{fromChat && (
        <span className="ml-2 text-[12px] font-normal text-ink-muted">· 来自 AI 助教</span>
      )}</>} left={<MobileBackButton fallback="/m/me" />} right={
        <button
          onClick={() => {
            setHistoryOpen(true)
            loadHistory()
          }}
          className="p-2 -mr-1 text-ink-muted cursor-pointer"
          aria-label="复习历史"
        >
          <History size={19} />
        </button>
      } />}
      footer={reviewing ? undefined : tabbar}
    >
      {loading && <MobileLoading />}

      {!loading && error && (
        <MobileEmpty title="加载失败" hint={error} action={
          <button onClick={loadDue} className="px-5 py-2 rounded-full bg-primary text-on-primary text-sub font-bold">重试</button>
        } />
      )}

      {/* 今日复习效果总结（2026-10）：这一页原来只有「还剩多少张到期卡」——
          那是**任务量**不是**效果**。学生复习完想知道的是「我今天干了多少、连击保住没」。
          数据全部来自已有接口（due 队列 + streak），不新增请求、不编造。 */}
      {!loading && !error && streak && (
        <div className="mx-4 mt-3 p-3.5 surface-card">
          <div className="text-[12.5px] font-semibold text-ink mb-2">今日复习效果</div>
          <div className="grid grid-cols-3 gap-2">
            {[
              { v: streak.cleared_today ? '已完成' : String(dueCount ?? 0) + ' 张',
                l: streak.cleared_today ? '今日清卡' : '待清卡' },
              { v: String(streak.current) + ' 天', l: '当前连击' },
              { v: String(streak.freeze_count) + '/' + String(streak.freeze_max), l: '冻结卡' },
            ].map((s) => (
              <div key={s.l} className="rounded-md bg-surface-muted py-2.5 text-center">
                <div className="text-[15px] font-bold text-ink kpi-number">{s.v}</div>
                <div className="text-micro text-ink-muted mt-0.5">{s.l}</div>
              </div>
            ))}
          </div>
          <p className="text-[11.5px] text-ink-secondary mt-2 leading-relaxed">
            {streak.cleared_today
              ? '今天的到期卡已清完，连击保住了。明天按同样节奏来就行。'
              : '清完今天的到期卡才能续上连击；记没记住不重要，按间隔重复到记住为止。'}
          </p>
        </div>
      )}

      {/* 连击卡（REQ-RCS-002 展示面） */}
      {!loading && !error && streak && (
        <div className="mx-4 mt-3 p-3.5 surface-card flex items-center gap-3">
          <Flame size={22} className={streak.current > 0 ? 'text-warning' : 'text-ink-muted/50'} />
          <div className="flex-1 min-w-0">
            <div className="text-[14px] font-bold text-ink">
              连击 {streak.current} 天
              <span className="text-micro text-ink-muted font-normal ml-2">最高 {streak.best}</span>
            </div>
            <div className="text-[11.5px] text-ink-muted mt-0.5 flex items-center gap-1">
              <Snowflake size={11} />
              冻结卡 {streak.freeze_count}/{streak.freeze_max} · {streak.cleared_today ? '今日已清完' : '清完今日到期卡续连击'}
            </div>
          </div>
          {streak.repair_available && !streak.cleared_today && streak.current > 0 && (
            <button
              onClick={doRepair}
              disabled={repairing}
              className="shrink-0 text-[11.5px] px-3 py-1.5 rounded-full border border-border bg-surface-muted text-ink-secondary disabled:opacity-40"
            >
              {repairing ? '修复中…' : '补签修复'}
            </button>
          )}
        </div>
      )}

      {/* 空态：今日到期卡已清完 */}
      {!loading && !error && !reviewing && tasks.length === 0 && (
        <MobileEmpty
          icon={<CheckCircle2 size={30} className={streak?.cleared_today ? 'text-success' : 'text-ink-muted'} />}
          title={streak?.cleared_today ? '今日到期卡已清完！' : '暂无到期复习'}
          hint={streak?.cleared_today ? '连击已续上，明天再来' : '错题会自动生成记忆卡，也可以从错题本手动同步'}
          action={
            <div className="flex gap-2 justify-center">
              <button
                onClick={() => {
                  if (!sid) return
                  api.post(`/api/review/sync/${encodeURIComponent(sid)}`, {}).then(() => loadDue()).catch(() => {})
                }}
                className="px-5 py-2 rounded-full border border-border bg-surface text-sub text-ink-secondary flex items-center gap-1.5"
              >
                <RefreshCw size={13} />从错题本同步
              </button>
              {/* 「去练习」已删除（2026-10）：底栏本来就有「练习」tab，同一功能两个出口；
                  空态下只保留「从错题本同步」。 */}
            </div>
          }
        />
      )}

      {/* 复习流：重做（选择）/翻面自评（主观） */}
      {!loading && !error && reviewing && t && (
        <div className="p-4">
          <div className="flex items-center justify-between text-micro text-ink-muted mb-2">
            <span>{idx + 1} / {tasks.length} · {t.knowledge_points?.[0] || '综合'}</span>
            <span>第 {t.review_level} 次复习</span>
          </div>
          <div className="p-4 surface-card text-[15px] leading-[1.9] text-ink mb-4">
            <ChemMd text={t.content || ''} />
          </div>

          {!feedback && isChoice && (
            <div className="space-y-2.5">
              {(t.options || []).map((o, i) => {
                const letter = String.fromCharCode(65 + i)
                const selected = (t.answer || '').trim().toUpperCase().startsWith(letter)
                return (
                  <button
                    key={i}
                    onClick={() => submitReview({ is_correct: selected })}
                    className="w-full text-left px-4 py-3.5 rounded-lg border-[1.5px] border-border bg-surface text-[14px] leading-relaxed text-ink active:border-primary active:bg-primary-soft transition-colors"
                  >
                    <ChemMd text={String(o).replace(/^[A-F]\.\s*/, '')} />
                  </button>
                )
              })}
            </div>
          )}

          {!feedback && !isChoice && (
            <div className="space-y-3">
              {!revealed ? (
                <button
                  onClick={() => setRevealed(true)}
                  className="w-full py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold flex items-center justify-center gap-2"
                >
                  <Eye size={15} />心里作答后，点击显示答案
                </button>
              ) : (
                <>
                  <div className="p-3.5 rounded-lg bg-success-soft/50 border border-success/30 text-[14px] leading-relaxed text-ink">
                    <div className="text-micro text-success mb-1">参考答案</div>
                    <ChemMd text={t.answer || '—'} />
                    {t.analysis && <div className="text-[12px] text-ink-secondary mt-2 border-t border-success/20 pt-2">{t.analysis.slice(0, 200)}</div>}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {[['forgot', 'text-error'], ['vague', 'text-warning'], ['remembered', 'text-success']].map(([rating, cls]) => (
                      <button
                        key={rating}
                        onClick={() => submitReview({ self_rating: rating })}
                        className={`py-2.5 rounded-lg border-[1.5px] border-border bg-surface text-[13.5px] font-medium ${cls} active:bg-primary-soft transition-colors`}
                      >
                        {RATING_LABEL[rating]}
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          {feedback && (
            <div className="text-center space-y-4 pt-2">
              <div className="flex flex-col items-center gap-1.5">
                {feedback.ok
                  ? <CheckCircle2 size={30} className="text-success" />
                  : <XCircle size={30} className="text-error" />}
                <p className="text-[14px] text-ink">{feedback.message}</p>
              </div>
              <button onClick={next} className="px-10 py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold">
                {idx + 1 < tasks.length ? '下一张' : '完成复习'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* 复习历史 */}
      <MobileFullPane open={historyOpen} title="复习历史" onClose={() => setHistoryOpen(false)}>
        {history.length === 0 ? (
          <MobileEmpty title="还没有复习记录" hint="完成今日复习后，这里会记录每次信号与下次到期时间" />
        ) : (
          <div className="space-y-2">
            {history.map((h, i) => (
              <div key={i} className="p-3 rounded-lg border border-border/60 bg-surface text-[12.5px] text-ink-secondary flex items-center justify-between">
                <span>{h.reviewed_at ? new Date(String(h.reviewed_at)).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : `#${i + 1}`}</span>
                <span className={h.is_correct === true ? 'text-success' : h.is_correct === false ? 'text-error' : ''}>
                  {h.is_correct === true ? '答对' : h.is_correct === false ? '答错' : RATING_LABEL[String(h.self_rating || '')] || '已复习'}
                </span>
              </div>
            ))}
          </div>
        )}
      </MobileFullPane>
    </MobileLayout>
  )
}
