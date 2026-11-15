/* ProfilePage — 我的（个人报告与设置，design/学生端.md）：
 * 游戏化首期（连续打卡 streak / 积分 / 徽章，数据来自学生 stats 接口）+
 * KPI（900 大数字）+ 学习计划/学习报告/复习中心/设置 全屏面板（P-08 底层逻辑同源）。 */

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Flame, Star, CalendarCheck, FileText, RotateCcw, Settings, LogOut, Copy, Check, Award, Bell, ChevronRight } from 'lucide-react'
import { api } from '../../../lib/api'
import { getMobileUser, clearMobileSession, type MobileUser } from '../../../lib/mobile-auth'
import { useReviewDue } from '../../../lib/use-review-due'
import { MobileLayout, MobileHeader, MobileTabBar, MobileFullPane, MobileLoading, MobileEmpty, studentTabs } from '../../../components/mobile/MobileLayout'
import { MasteryRing } from '../../../components/mobile/MasteryRing'

interface DiagnosisPlan {
  success?: boolean
  data?: {
    total_answers?: number
    accuracy?: number
    weak_knowledge_points?: string[]
    bind_code?: string
  }
}

interface StudentStats {
  total_answers: number
  accuracy: number
  streak_days: number
  week_practice_count: number
}

interface LearningPlan {
  plan?: {
    plan_title?: string
    plan_period?: string
    weekly_goals?: ({ milestone?: string; goal?: string; text?: string } | string)[]
    daily_tasks?: { day?: string; task?: string; content?: string }[]
    barrier_interventions?: Record<string, string>
    motivation_tips?: ({ tip?: string; text?: string } | string)[]
    raw_text?: string
  }
  /** 老师发送这份计划的时间（后端取自收件通知）；自己生成/从未发送时为空。 */
  sent_at?: string | null
}

/** 收件箱里的一条：老师每发一次计划，这里就多一份（后端 /history）。 */
interface PlanHistoryItem {
  id: number
  plan_title: string
  plan_data?: NonNullable<LearningPlan['plan']>
  created_at: string
}

/* 接口 /api/report/student/{id} 返回的是**扁平**结构（success/student_id/barrier_type/
 * exam_results…），并没有 data 包一层。原来面板只读 reportData.data，于是永远拿空对象
 * 渲染 ReportBody ——「我的 → 学习报告」点进去一片空白就是这么来的（2026-10 实测）。
 * 这里两层都声明：有 data 用 data，没有就把顶层当 data。 */
interface StudentReport {
  success?: boolean
  student_id?: string
  student_name?: string
  exercises_completed?: number
  barrier_type?: Record<string, number>
  exam_results?: { exam_id?: string; exam_name?: string; exam_date?: string; score?: number }[]
  data?: {
    exercises_completed?: number
    avg_score?: number | string
    barrier_type?: Record<string, number>
    recent_exams?: number
    trend?: { exam_name?: string; score?: number }[]
  }
  message?: string
}

interface StudentNotification {
  notification_id: string
  type: string
  title: string
  content: string
  is_read: boolean
  sent_at: string
}

const BARRIER_NAMES: Record<string, string> = { concept: '概念理解', reading: '审题仔细', expression: '答题表述' }

export function ProfilePage() {
  const navigate = useNavigate()
  const [user] = useState<MobileUser | null>(() => getMobileUser() as MobileUser | null)
  const dueCount = useReviewDue(user?.id ?? undefined)
  const [planOpen, setPlanOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const loadNotifs = useCallback(async (reopen = false) => {
    if (!user?.id) return
    setNotifLoading(true)
    try {
      const d = await api.get<{ notifications?: StudentNotification[]; unread_count?: number }>(
        `/api/notification/student/${encodeURIComponent(user.id)}/notifications?limit=30`)
      setNotifs(d.notifications ?? [])
      setUnreadCount(d.unread_count ?? 0)
      if (reopen) setNotifOpen(true)
    } catch {
      setNotifs([]); setUnreadCount(0)
    } finally {
      setNotifLoading(false)
    }
  }, [user?.id])

  const markAllRead = useCallback(async () => {
    if (!user?.id) return
    try {
      await api.post(`/api/notification/student/${encodeURIComponent(user.id)}/read`, { notification_ids: null })
      setNotifs((ns) => ns.map((n) => ({ ...n, is_read: true })))
      setUnreadCount(0)
    } catch { /* 静默：下次进入再试 */ }
  }, [user?.id])
  const [planData, setPlanData] = useState<LearningPlan | null>(null)
  const [planLoading, setPlanLoading] = useState(false)
  /* 收件卡 → 正文是两级：默认停在卡片上，点开才渲染 PlanBody。 */
  const [planDetailOpen, setPlanDetailOpen] = useState(false)
  /* 收件箱：面板里列的是**历次收到**的计划，不是只有当前那一份。只显示当前版本
   * 会让历史计划凭空消失 —— 2026-10 实测：学生已经收到 3 份，面板上只有一张卡。 */
  const [planHistory, setPlanHistory] = useState<PlanHistoryItem[] | null>(null)
  const [planDetail, setPlanDetail] = useState<PlanHistoryItem | null>(null)
  const [reportData, setReportData] = useState<StudentReport | null>(null)
  const [stats, setStats] = useState<StudentStats | null>(null)
  const [notifOpen, setNotifOpen] = useState(false)
  const [notifs, setNotifs] = useState<StudentNotification[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const [notifLoading, setNotifLoading] = useState(false)

  /* 打开面板时拉一次历史（最近 20 份）。拉不到就退回「当前计划」那一张，
   * 不至于整面板空白。 */
  useEffect(() => {
    if (!planOpen || !user?.id) return
    api.get<{ history?: PlanHistoryItem[] }>(
      `/api/diagnosis/learning-plan/${encodeURIComponent(user.id)}/history`)
      .then((r) => setPlanHistory(r?.history ?? []))
      .catch(() => setPlanHistory([]))
  }, [planOpen, user?.id])

  const planItems: PlanHistoryItem[] = (planHistory && planHistory.length)
    ? planHistory
    : (planData?.plan
      ? [{ id: -1, plan_title: planData.plan.plan_title || '个性化学习计划',
          plan_data: planData.plan, created_at: planData.sent_at || '' }]
      : [])

  const tabs = studentTabs('me', navigate, dueCount)

  const loadPlan = useCallback(() => {
    if (!user) return
    setPlanLoading(true)
    /* 端点把 plan 包在 data 里（{success, data:{plan}}）；只认顶层 plan 会把
     * 家长派发/教师发送的计划误判为空，走自动生成把现计划覆盖掉（E2E E14 实测） */
    const unwrap = (d: LearningPlan & { data?: LearningPlan }): LearningPlan => d?.data?.plan ? d.data : d
    api.get<LearningPlan & { data?: LearningPlan }>(`/api/diagnosis/learning-plan/${encodeURIComponent(user.id)}`)
      .then((raw) => {
        const d = unwrap(raw)
        if (d.plan) {
          setPlanData(d)
          setPlanLoading(false)
        } else {
          // 真无计划 → 生成一份再加载
          return api.post<LearningPlan & { data?: LearningPlan }>('/api/diagnosis/learning-plan/generate', {
            student_id: user.id, class_id: user.class_id,
          }).then(() => api.get<LearningPlan & { data?: LearningPlan }>(`/api/diagnosis/learning-plan/${encodeURIComponent(user.id)}`))
            .then((raw2) => {
              setPlanData(unwrap(raw2))
              setPlanLoading(false)
            })
        }
      })
      .catch(() => setPlanLoading(false))
  }, [user])

  useEffect(() => {
    document.title = '我的'
    if (!user) return
    api.get<DiagnosisPlan>(`/api/diagnosis/plan/${encodeURIComponent(user.id)}`).catch(() => {})
    api.get<StudentStats>(`/api/practice/student/${encodeURIComponent(user.id)}/stats`)
      .then((d) => setStats(d))
      .catch(() => setStats(null))
    loadNotifs()
  }, [user])

  function openReport() {
    if (!user) return
    setReportOpen(true)
    api.get<StudentReport>(`/api/report/student/${encodeURIComponent(user.id)}`)
      .then(setReportData)
      .catch(() => setReportData({ success: false }))
  }

  if (!user) return null

  const accuracyPct = stats ? Math.round((stats.accuracy || 0) * 100) : 0
  const badges = [
    { label: accuracyPct >= 80 ? '化学达人' : accuracyPct >= 60 ? '稳步提升' : '初出茅庐', got: stats ? stats.total_answers > 0 : false },
    { label: '连续7天', got: (stats?.streak_days || 0) >= 7 },
    { label: '百题斩', got: (stats?.total_answers || 0) >= 100 },
  ]

  return (
    <MobileLayout
      theme="student"
      header={
        <MobileHeader
          title={user.name || '同学'}
          right={
            <button
              onClick={() => {
                clearMobileSession()
                navigate('/m/login', { replace: true })
              }}
              className="flex items-center gap-1 text-[12px] text-ink-muted hover:text-error cursor-pointer"
            >
              <LogOut size={13} />退出
            </button>
          }
        />
      }
      footer={<MobileTabBar tabs={tabs} />}
    >
      {/* 掌握环（批 2 S2）：BKT 四级 + FSRS 褪色，遗忘点直达复习中心 */}
      <MasteryRing studentId={user.id} />
      {/* 顶部：头像 + 班级 */}
      <div className="px-4 pt-5 pb-3 flex items-center gap-3">
        <div className="w-12 h-12 rounded-full bg-primary-soft flex items-center justify-center text-[20px] font-black text-primary font-display">
          {(user.name || '学')[0]}
        </div>
        <div>
          <div className="text-[17px] font-bold text-ink font-display">{user.name || '同学'}</div>
          <div className="text-[12px] text-ink-muted">{user.class_name || '班级未设置'} · {user.id}</div>
        </div>
      </div>

      {/* 游戏化卡（首期：连续打卡 + 积分 + 徽章，R-S-C3 900 大数字） */}
      <div className="mx-4 mb-3 p-4 rounded-lg bg-surface border border-border/60 shadow-[var(--shadow-sm)]">
        <div className="grid grid-cols-2 gap-3">
          <div className="flex items-center gap-2.5">
            <Flame size={26} className="text-warning shrink-0" />
            <div>
              <div className="font-display text-[30px] font-black text-warning leading-none kpi-number">
                {stats?.streak_days ?? '—'}
              </div>
              <div className="text-micro text-ink-muted mt-1">连续打卡（天）</div>
            </div>
          </div>
          <div className="flex items-center gap-2.5">
            <Star size={26} className="text-primary shrink-0" />
            <div>
              <div className="font-display text-[30px] font-black text-primary leading-none kpi-number">
                {stats?.total_answers ?? '—'}
              </div>
              <div className="text-micro text-ink-muted mt-1">累计积分（答题）</div>
            </div>
          </div>
        </div>
        <div className="flex gap-1.5 mt-3.5 pt-3 border-t border-border/50">
          {badges.map((b) => (
            <span
              key={b.label}
              className={`flex items-center gap-1 text-[10.5px] px-2.5 py-1 rounded-full ${
                b.got ? 'bg-primary-soft text-primary' : 'bg-surface-muted text-ink-muted opacity-60'
              }`}
            >
              <Award size={11} />{b.label}
            </span>
          ))}
        </div>
      </div>

      {/* KPI（P-06） */}
      <div className="mx-4 mb-3 grid grid-cols-3 gap-2">
        {[
          { v: stats?.week_practice_count ?? '—', l: '本周练习' },
          { v: stats ? `${accuracyPct}%` : '—', l: '正确率' },
          { v: stats?.total_answers ?? '—', l: '累计答题' },
        ].map((s) => (
          <div key={s.l} className="rounded-lg bg-surface border border-border/60 py-3.5 text-center">
            <div className="font-display text-[22px] font-black text-ink leading-none kpi-number">{s.v}</div>
            <div className="text-micro text-ink-muted mt-1.5">{s.l}</div>
          </div>
        ))}
      </div>

      {/* 入口列表 */}
      <div className="mx-4 mb-6 rounded-lg bg-surface border border-border/60 divide-y divide-border/50 overflow-hidden">
        {[
          { icon: <Bell size={17} className="text-primary" />, label: '我的通知', onClick: () => { loadNotifs(true) },
            badge: unreadCount },
          { icon: <CalendarCheck size={17} className="text-primary" />, label: '学习计划', onClick: () => { setPlanOpen(true); loadPlan() } },
          { icon: <FileText size={17} className="text-primary" />, label: '学习报告', onClick: openReport },
          { icon: <RotateCcw size={17} className="text-primary" />, label: '复习中心', onClick: () => navigate('/m/review') },
          { icon: <Settings size={17} className="text-primary" />, label: '个人设置', onClick: () => setSettingsOpen(true) },
        ].map((e) => (
          <button key={e.label} onClick={e.onClick} className="w-full flex items-center gap-3 px-4 py-3.5 text-[14px] text-ink hover:bg-surface-muted transition-colors text-left cursor-pointer">
            {e.icon}
            <span className="flex-1">{e.label}</span>
            {'badge' in e && (e as { badge?: number }).badge ? (
              <span data-testid="notif-unread" className="min-w-[20px] h-5 px-1.5 rounded-full bg-error text-white text-micro font-bold flex items-center justify-center">
                {(e as { badge?: number }).badge}
              </span>
            ) : null}
            <span className="text-ink-muted">›</span>
          </button>
        ))}
      </div>

      {/* ── 我的通知面板 ── */}
      <MobileFullPane open={notifOpen} title="我的通知" onClose={() => setNotifOpen(false)}>
        {notifLoading ? (
          <MobileLoading />
        ) : notifs.length === 0 ? (
          <MobileEmpty title="暂无通知" hint="老师的通知和学习提醒会出现在这里" />
        ) : (
          <div className="px-4 pb-6">
            <div className="flex items-center justify-between mb-3">
              <span className="text-[12px] text-ink-muted">共 {notifs.length} 条 · 未读 {unreadCount}</span>
              {unreadCount > 0 && (
                <button
                  onClick={markAllRead}
                  className="text-[12px] text-primary font-bold px-3 py-1.5 rounded-full bg-primary-soft hover:opacity-80 transition-opacity cursor-pointer"
                >
                  全部已读
                </button>
              )}
            </div>
            <div className="rounded-lg bg-surface border border-border/60 divide-y divide-border/50 overflow-hidden">
              {notifs.map((n) => (
                <div key={n.notification_id} className="px-4 py-3">
                  <div className="flex items-start gap-2">
                    {!n.is_read && <span className="mt-1.5 w-2 h-2 rounded-full bg-error shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <div className={`text-[14px] leading-snug ${n.is_read ? 'text-ink-secondary' : 'text-ink font-bold'}`}>{n.title}</div>
                      {n.content && n.content !== n.title && (
                        <div className="text-[12.5px] text-ink-secondary mt-1 leading-relaxed whitespace-pre-wrap break-words">{n.content}</div>
                      )}
                      <div className="text-micro text-ink-muted mt-1.5">{n.sent_at}</div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </MobileFullPane>

      {/* ── 学习计划面板 ── */}
      <MobileFullPane open={planOpen} title="学习计划" onClose={() => setPlanOpen(false)}>
        {planLoading && !planHistory ? (
          <MobileLoading />
        ) : !planItems.length ? (
          <MobileEmpty title="暂无学习计划" hint="请联系老师布置，或稍后再试" />
        ) : (
          /* 收件箱：老师每发一次就是一份，各自带收到时间。以前只列「当前那一份」，
           * 学生换过几次计划后，旧的凭空消失（2026-10 实测：3 份只显示 1 张卡）。 */
          <div className="space-y-2.5">
            {planItems.map((it, i) => (
              <button
                key={String(it.id)}
                type="button"
                onClick={() => { setPlanDetail(it); setPlanDetailOpen(true) }}
                className="w-full text-left bg-surface border border-border rounded-[var(--radius-md)] p-3.5 flex items-center gap-3 active:scale-[0.99] transition-transform"
              >
            <span className="w-9 h-9 rounded-[var(--radius-sm)] bg-primary-soft text-primary flex items-center justify-center shrink-0">
              <CalendarCheck size={17} />
            </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-sub font-medium text-ink truncate">
                    {it.plan_title || '个性化学习计划'}
                    {i === 0 && <span className="ml-1.5 text-micro font-normal text-primary">最新</span>}
                  </span>
                  <span className="block text-micro text-ink-muted mt-0.5">
                    {it.created_at ? `收到时间：${it.created_at}` : '老师发来的学习计划'}
                  </span>
                </span>
                <ChevronRight size={16} className="text-ink-muted shrink-0" />
              </button>
            ))}
          </div>
        )}
      </MobileFullPane>

      {/* 正文：点开哪一份就渲染哪一份，返回一层回到收件箱 */}
      <MobileFullPane open={planDetailOpen} title={planDetail?.plan_title || '学习计划'}
        onClose={() => setPlanDetailOpen(false)}>
        {planDetail?.plan_data && <PlanBody plan={planDetail.plan_data} />}
      </MobileFullPane>

      {/* ── 学习报告面板 ── */}
      <MobileFullPane open={reportOpen} title={`${user.name || '同学'} 学习报告`} onClose={() => setReportOpen(false)}>
        {!reportData ? (
          <MobileLoading />
        ) : !reportData.success && !reportData.data ? (
          <MobileEmpty title="加载失败" hint={reportData.message || '请稍后再试'} />
        ) : (
          <ReportBodyV2 report={(reportData.data || reportData) as unknown as Record<string, unknown>} />
        )}
      </MobileFullPane>

      {/* ── 设置面板 ── */}
      <MobileFullPane open={settingsOpen} title="个人设置" onClose={() => setSettingsOpen(false)}>
        <SettingsBody user={user} />
      </MobileFullPane>
    </MobileLayout>
  )
}

function PlanBody({ plan }: { plan: NonNullable<LearningPlan['plan']> }) {
  const weeks = plan.weekly_goals || []
  const tasks = plan.daily_tasks || []
  const barriers = plan.barrier_interventions || {}
  const tips = plan.motivation_tips || []
  const asText = (x: unknown) =>
    typeof x === 'string' ? x : ((x as { milestone?: string; goal?: string; text?: string; tip?: string })?.milestone || (x as { goal?: string })?.goal || (x as { text?: string })?.text || (x as { tip?: string })?.tip || '')
  return (
    <div>
      <h3 className="font-display text-[19px] font-black text-ink">{plan.plan_title || '个性化学习计划'}</h3>
      {plan.plan_period && <p className="text-[12px] text-ink-muted mt-1 mb-3">周期：{plan.plan_period}</p>}
      {weeks.length > 0 && (
        <>
          <div className="text-[13.5px] font-bold text-primary mt-4 mb-1.5">周目标</div>
          {weeks.map((g, i) => (
            <p key={i} className="text-[14px] text-ink py-1.5 border-b border-border/40">{asText(g)}</p>
          ))}
        </>
      )}
      {tasks.length > 0 && (
        <>
          <div className="text-[13.5px] font-bold text-primary mt-4 mb-1.5">每日任务</div>
          {tasks.slice(0, 14).map((t, i) => (
            <p key={i} className="text-[14px] text-ink py-1.5 border-b border-border/40">
              <span className="text-ink-muted text-[12px] mr-2">{t.day}</span>
              {t.task || t.content}
            </p>
          ))}
        </>
      )}
      {Object.keys(barriers).length > 0 && (
        <>
          <div className="text-[13.5px] font-bold text-primary mt-4 mb-1.5">障碍干预</div>
          {Object.entries(barriers).map(([b, v]) => (
            <p key={b} className="text-[14px] text-ink py-1.5 border-b border-border/40">
              <b>{BARRIER_NAMES[b] || b}</b>：{v}
            </p>
          ))}
        </>
      )}
      {tips.length > 0 && (
        <>
          <div className="text-[13.5px] font-bold text-primary mt-4 mb-1.5">激励建议</div>
          {tips.map((t, i) => (
            <p key={i} className="text-[14px] text-ink py-1.5 border-b border-border/40">{asText(t)}</p>
          ))}
        </>
      )}
      {!weeks.length && !tasks.length && plan.raw_text && (
        <p className="text-[14px] leading-[1.8] text-ink whitespace-pre-wrap">{plan.raw_text}</p>
      )}
      {!weeks.length && !tasks.length && !plan.raw_text && (
        <MobileEmpty title="暂无学习计划详情" hint="请联系老师布置" />
      )}
    </div>
  )
}

/** 学习报告 v2 —— 2026-10 重做。
 *
 * 旧版只渲染四个数字（练习完成/平均正确率/主要障碍/近期考试），其中两个还因为接口是
 * 扁平结构而常年显示 -- 和 0。同类优秀产品（猿辅导/作业帮/学而思的学习报告）实际都在
 * 回答四个问题：**我在什么水平、错在哪、进步了没有、下一步做什么**。这里按这四条重组，
 * 数据全部取自真实接口，不构造占位值：
 *   /api/practice/student/{id}/stats   总答题/正确率/本周练习/连击
 *   /api/report/student/{id}           错因分布 + 历次考试
 *   /api/knowledge-map/student/{id}    知识点掌握（BKT 四级）
 *   /api/review/student/{id}/count     待复习数
 */
function ReportBodyV2({ report }: { report: Record<string, unknown> }) {
  const sid = String(report.student_id || '')
  const [stats, setStats] = useState<{ total_answers?: number; accuracy?: number; week_practice_count?: number; streak_days?: number } | null>(null)
  const [mastery, setMastery] = useState<Record<string, number> | null>(null)
  const [due, setDue] = useState<number | null>(null)

  useEffect(() => {
    if (!sid) return
    api.get<{ total_answers?: number; accuracy?: number; week_practice_count?: number; streak_days?: number }>(`/api/practice/student/${sid}/stats`)
      .then(setStats).catch(() => setStats(null))
    api.get<{ mastery?: Record<string, number> }>(`/api/knowledge-map/student/${sid}`)
      .then((d) => setMastery(d.mastery || {})).catch(() => setMastery({}))
    api.get<{ due_count?: number }>(`/api/review/student/${sid}/count`)
      .then((d) => setDue(d.due_count ?? 0)).catch(() => setDue(null))
  }, [sid])

  const barrier = (report.barrier_type || {}) as Record<string, number>
  const barrierRows = [
    { key: 'concept', label: '概念理解' },
    { key: 'reading', label: '审题障碍' },
    { key: 'expression', label: '表述障碍' },
  ].map((r) => ({ ...r, v: Number(barrier[r.key] || 0) }))
  const mainB = [...barrierRows].sort((a, b) => b.v - a.v)[0]
  const exams = ((report.exam_results || []) as { exam_name?: string; exam_date?: string; score?: number }[])
  const scores = exams.map((e) => Number(e.score || 0))
  const avgScore = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null
  /* 趋势阈值（2026-10）：只有样本 ≥3 次、且首末跨度 ≥7 天时才给涨跌箭头。
     实测周翔三次记录是「100 / 0 / 0」、同一天、同名智能练习——这种数据上算出来的
     「↑100 分」是噪声，显示出来只会误导学生。宁可不说，也不给假信号。 */
  const fmt = (d?: string) => (d ? new Date(d + 'T00:00:00').getTime() : NaN)
  const spanDays = exams.length >= 2
    ? Math.round((fmt(exams[0].exam_date) - fmt(exams[exams.length - 1].exam_date)) / 86400000)
    : 0
  const trendDelta = (scores.length >= 3 && Number.isFinite(spanDays) && Math.abs(spanDays) >= 7)
    ? Math.round(scores[0] - scores[scores.length - 1])
    : null
  const kpEntries = Object.entries(mastery || {})
    .map(([k, v]) => ({ k, v: Number(v) })).filter((x) => !Number.isNaN(x.v))
  const levelNames = ['探索', '熟悉', '熟练', '精通']
  const dist = [0, 0, 0, 0]
  kpEntries.forEach((x) => { dist[Math.min(3, Math.max(0, Math.round(x.v)))] += 1 })
  const weak = [...kpEntries].sort((a, b) => a.v - b.v).slice(0, 5)

  return (
    <div className="pb-4">
      <div className="text-[13.5px] font-bold text-primary mb-2">学习概况</div>
      <div className="grid grid-cols-2 gap-2.5">
        {[
          { v: stats ? stats.total_answers ?? 0 : '--', l: '累计答题' },
          { v: stats && stats.accuracy != null ? `${Math.round((stats.accuracy || 0) * 100)}%` : '--', l: '正确率' },
          { v: stats ? stats.week_practice_count ?? 0 : '--', l: '本周练习' },
          { v: stats ? `${stats.streak_days ?? 0}天` : '--', l: '连续打卡' },
        ].map((s) => (
          <div key={s.l} className="rounded-lg bg-surface border border-border/60 py-4 text-center">
            <div className="font-display text-[24px] font-black text-ink leading-none kpi-number">{s.v}</div>
            <div className="text-micro text-ink-muted mt-1.5">{s.l}</div>
          </div>
        ))}
      </div>

      <div className="text-[13.5px] font-bold text-primary mt-5 mb-2">错在哪</div>
      <div className="rounded-lg bg-surface border border-border/60 p-3.5 space-y-2.5">
        {barrierRows.map((r) => (
          <div key={r.key}>
            <div className="flex justify-between text-[12.5px] mb-1">
              <span className="text-ink-secondary">{r.label}</span>
              <span className="font-semibold text-ink kpi-number">{Math.round(r.v * 100)}%</span>
            </div>
            <div className="h-1.5 rounded-full bg-surface-muted overflow-hidden">
              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.min(100, Math.round(r.v * 100))}%` }} />
            </div>
          </div>
        ))}
        <p className="text-[12.5px] text-ink-secondary pt-1">
          主要卡在<strong className="text-ink">{mainB.label}</strong>（{Math.round(mainB.v * 100)}%），
          这部分占比最高，优先攻它收益最大。
        </p>
      </div>

      <div className="text-[13.5px] font-bold text-primary mt-5 mb-2">知识点掌握</div>
      <div className="rounded-lg bg-surface border border-border/60 p-3.5">
        <div className="flex gap-1.5 mb-3">
          {dist.map((n, i) => (
            <div key={i} className="flex-1 text-center rounded-md bg-surface-muted py-2">
              <div className="text-[15px] font-bold text-ink kpi-number">{n}</div>
              <div className="text-micro text-ink-muted mt-0.5">{levelNames[i]}</div>
            </div>
          ))}
        </div>
        {weak.length > 0 ? (
          <div className="space-y-1">
            <div className="text-[12px] text-ink-muted mb-1">最薄弱的 {weak.length} 个知识点</div>
            {weak.map((w) => (
              <div key={w.k} className="flex justify-between text-[13px] py-1 border-b border-border/40 last:border-0">
                <span className="text-ink truncate">{w.k}</span>
                <span className="text-ink-muted shrink-0 ml-2">{levelNames[Math.min(3, Math.max(0, Math.round(w.v)))]}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[12.5px] text-ink-muted">还没有足够作答数据来评估知识点掌握。</p>
        )}
      </div>

      <div className="text-[13.5px] font-bold text-primary mt-5 mb-2">进步了没有</div>
      <div className="rounded-lg bg-surface border border-border/60 p-3.5">
        {exams.length > 0 ? (
          <>
            <div className="flex items-baseline gap-2 mb-2">
              <span className="font-display text-[22px] font-black text-ink kpi-number">{avgScore}分</span>
              <span className="text-[12px] text-ink-muted">近 {exams.length} 次平均</span>
              {trendDelta != null && (
                <span className={`text-[12px] font-semibold ${trendDelta >= 0 ? 'text-success' : 'text-error'}`}>
                  {trendDelta >= 0 ? '↑' : '↓'} {Math.abs(trendDelta)} 分
                </span>
              )}
            </div>
            {exams.slice(0, 5).map((e, i) => (
              <div key={i} className="flex justify-between items-center py-1.5 border-b border-border/40 last:border-0">
                <span className="text-[12.5px] text-ink-secondary truncate">{e.exam_name || '练习'}</span>
                <span className="text-[12.5px] font-semibold text-ink shrink-0 ml-2">{Math.round(Number(e.score || 0))}分</span>
              </div>
            ))}
          </>
        ) : (
          <p className="text-[12.5px] text-ink-muted">还没有考试或练习记录。</p>
        )}
      </div>

      <div className="text-[13.5px] font-bold text-primary mt-5 mb-2">下一步做什么</div>
      <div className="rounded-lg bg-primary-soft border border-primary/30 p-3.5 space-y-1.5">
        {[
          due != null && due > 0
            ? `先清掉今天到期的 ${due} 张复习卡（间隔复习排在最前面，性价比最高）。`
            : '今天的复习卡已经清空，保持住这个节奏。',
          `针对「${mainB.label}」做专项：${mainB.key === 'concept' ? '先把概念图讲一遍给自己听，再做题' : mainB.key === 'reading' ? '读题时圈出条件与所求，再动笔' : '把解答过程写完整，符号规范'}。`,
          weak.length > 0
            ? `最薄弱的「${weak[0].k}」优先，配上 5 道同类题做即时验证。`
            : '多做几道题，系统才能给出针对性建议。',
        ].map((s, i) => (
          <p key={i} className="text-[13px] text-ink leading-relaxed">{i + 1}. {s}</p>
        ))}
      </div>
    </div>
  )
}
/** 旧版四格报告 —— 已被 ReportBodyV2 取代，保留导出以便回滚对照（不再被引用）。 */
export function ReportBody({ report }: { report: NonNullable<StudentReport['data']> }) {
  const barrier = report.barrier_type || {}
  const main = Object.entries(barrier).sort((a, b) => (b[1] || 0) - (a[1] || 0))[0]?.[0]
  return (
    <div>
      <div className="grid grid-cols-2 gap-2.5 mb-4">
        {[
          { v: report.exercises_completed ?? 0, l: '练习完成' },
          { v: report.avg_score != null ? `${report.avg_score}%` : '--', l: '平均正确率' },
          { v: main ? BARRIER_NAMES[main] : '--', l: '主要障碍' },
          { v: report.recent_exams ?? 0, l: '近期考试' },
        ].map((s) => (
          <div key={s.l} className="rounded-lg bg-surface border border-border/60 py-4 text-center">
            <div className="font-display text-[24px] font-black text-ink leading-none kpi-number">{s.v}</div>
            <div className="text-micro text-ink-muted mt-1.5">{s.l}</div>
          </div>
        ))}
      </div>
      {(report.trend || []).length > 0 && (
        <>
          <div className="text-[13.5px] font-bold text-primary mb-2">成绩趋势</div>
          <div className="space-y-1.5">
            {(report.trend || []).map((t, i) => (
              <div key={i} className="flex justify-between items-center px-3.5 py-2.5 rounded-lg bg-surface border border-border/50">
                <span className="text-[13.5px] text-ink truncate">{t.exam_name}</span>
                <span className="font-bold text-[13.5px] text-primary kpi-number">{t.score}分</span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function SettingsBody({ user }: { user: MobileUser }) {
  /* 修改密码改成**点开弹窗**（2026-10 反馈：设置页里直接摆一张改密表单不合理，
     正常产品都是"设置列表 → 点一项 → 弹层操作"）。 */
  const [pwOpen, setPwOpen] = useState(false)
  /* 只为让字号按钮在点击后重渲染（真实值在 localStorage，MobileLayout 负责应用） */
  const [, setFontTick] = useState(0)
  const [bindCode, setBindCode] = useState('加载中…')
  const [copied, setCopied] = useState(false)
  const [oldPw, setOldPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [newPw2, setNewPw2] = useState('')
  const [pwMsg, setPwMsg] = useState('')

  useEffect(() => {
    api.get<DiagnosisPlan>(`/api/diagnosis/plan/${encodeURIComponent(user.id)}`)
      .then((d) => setBindCode(d.data?.bind_code || user.bind_code || '暂无'))
      .catch(() => setBindCode('获取失败'))
  }, [user])

  function copyBindCode() {
    if (!bindCode || bindCode === '暂无' || bindCode === '加载中…') return
    navigator.clipboard.writeText(bindCode).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }).catch(() => {})
  }

  function changePassword() {
    if (!oldPw || !newPw) return setPwMsg('请填写所有密码字段')
    if (newPw !== newPw2) return setPwMsg('两次新密码不一致')
    if (newPw.length < 4) return setPwMsg('新密码至少4位')
    api.post<{ success?: boolean; message?: string; detail?: string }>('/api/auth/change-password', {
      old_password: oldPw, new_password: newPw,
    })
      .then((d) => setPwMsg(d.success ? '密码修改成功' : d.message || d.detail || '修改失败'))
      .catch(() => setPwMsg('网络错误'))
  }

  return (
    <div className="space-y-3.5">
      <div className="rounded-lg bg-surface border border-border/60 p-4">
        <div className="font-bold text-[14.5px] text-ink mb-2.5">个人信息</div>
        <div className="space-y-1.5 text-[13.5px]">
          <div className="flex justify-between"><span className="text-ink-muted">姓名</span><span className="text-ink">{user.name || '—'}</span></div>
          <div className="flex justify-between"><span className="text-ink-muted">学号</span><span className="text-ink">{user.id}</span></div>
          <div className="flex justify-between"><span className="text-ink-muted">班级</span><span className="text-ink">{user.class_name || '—'}</span></div>
        </div>
      </div>

      <div className="rounded-lg bg-surface border border-border/60 p-4">
        <div className="font-bold text-[14.5px] text-ink mb-1.5">家长绑定码</div>
        <p className="text-[12px] text-ink-muted leading-relaxed mb-2.5">将此码发给家长，家长在家长端输入后即可绑定查看你的学习情况</p>
        <div className="flex items-center gap-3">
          <span className="kpi-number text-[26px] font-black tracking-[0.35em] text-primary">{bindCode}</span>
          <button onClick={copyBindCode} className="flex items-center gap-1 px-3 py-1.5 rounded-full border border-border text-[12px] text-ink-secondary hover:bg-surface-muted cursor-pointer">
            {copied ? <Check size={12} className="text-success" /> : <Copy size={12} />}
            {copied ? '已复制' : '复制'}
          </button>
        </div>
      </div>

      <div className="rounded-lg bg-surface border border-border/60 p-4">
        <div className="font-bold text-[14.5px] text-ink mb-2.5">字号</div>
        <div className="flex gap-2">
          {[{ l: '小', v: 0.9 }, { l: '中', v: 1 }, { l: '大', v: 1.12 }].map((o) => {
            const cur = Number(localStorage.getItem('chemai_font_scale') || '1')
            const active = Math.abs(cur - o.v) < 0.001
            return (
              <button
                key={o.l}
                onClick={() => {
                  localStorage.setItem('chemai_font_scale', String(o.v))
                  window.dispatchEvent(new Event('chemai-font-scale'))
                  setFontTick((n) => n + 1)
                }}
                className={`flex-1 h-10 rounded-xl border text-[14px] ${active ? 'border-primary bg-primary-soft text-primary font-bold' : 'border-border text-ink-secondary'}`}
              >
                {o.l}
              </button>
            )
          })}
        </div>
      </div>

      <div className="rounded-lg bg-surface border border-border/60">
        <button
          onClick={() => { setPwOpen(true); setPwMsg('') }}
          className="w-full flex items-center justify-between px-4 py-3.5 cursor-pointer"
        >
          <span className="text-[14px] text-ink">修改密码</span>
          <span className="text-ink-muted text-[16px] leading-none">›</span>
        </button>
      </div>

      {pwOpen && (
        <div className="fixed inset-0 z-50 bg-black/35 flex items-center justify-center px-6" onClick={() => setPwOpen(false)}>
          <div className="w-full max-w-[360px] bg-bg rounded-[var(--radius-card)] p-4" onClick={(e) => e.stopPropagation()}>
            <div className="font-bold text-[15px] text-ink mb-3">修改密码</div>
            <div className="space-y-2">
              <input type="password" value={oldPw} onChange={(e) => setOldPw(e.target.value)} placeholder="当前密码" className="w-full h-11 px-4 rounded-xl border-[1.5px] border-border bg-surface-muted text-[14px] text-ink placeholder:text-ink-muted outline-none focus:border-primary" />
              <input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} placeholder="新密码" className="w-full h-11 px-4 rounded-xl border-[1.5px] border-border bg-surface-muted text-[14px] text-ink placeholder:text-ink-muted outline-none focus:border-primary" />
              <input type="password" value={newPw2} onChange={(e) => setNewPw2(e.target.value)} placeholder="确认新密码" className="w-full h-11 px-4 rounded-xl border-[1.5px] border-border bg-surface-muted text-[14px] text-ink placeholder:text-ink-muted outline-none focus:border-primary" />
            </div>
            {pwMsg && (
              <p className={`text-center text-[12.5px] mt-2 ${pwMsg.includes('成功') ? 'text-success' : 'text-error'}`}>{pwMsg}</p>
            )}
            <div className="flex gap-2 mt-3">
              <button onClick={() => setPwOpen(false)} className="flex-1 h-11 rounded-xl border border-border text-[14px] text-ink-secondary">取消</button>
              <button onClick={changePassword} className="flex-1 h-11 rounded-xl bg-primary text-on-primary text-[14px] font-bold">确认修改</button>
            </div>
          </div>
        </div>
      )}

      <div className="rounded-lg bg-surface border border-border/60 p-4">
        <div className="font-bold text-[14.5px] text-ink mb-1">关于</div>
        <p className="text-[12.5px] text-ink-muted leading-relaxed">智辅化学 (ChemAI) v2.0<br />面向高中化学的 AI 教学辅助平台<br />教师出题 · 学生辅导 · 家长关注</p>
      </div>
    </div>
  )
}
