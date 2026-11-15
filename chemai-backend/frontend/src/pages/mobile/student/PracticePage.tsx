/* PracticePage — 练习做题（design/学生端.md）：任务列表 → 答题流 → 成绩页。
 * 三态覆盖（R-S1/S2）；提交主按钮 = 亮青实底（2026-09-14 定案 A）。 */

import { ChemGlyph, ChemHeroWatermark } from '../../../components/ui/ChemGlyph'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { PenLine, ChevronLeft, ChevronRight, CheckCircle2, Sparkles } from 'lucide-react'
import { api } from '../../../lib/api'
import { getMobileUser } from '../../../lib/mobile-auth'
import { parseSmartHandoff } from '../../../lib/agent-handoff'
import { useReviewDue } from '../../../lib/use-review-due'
import { MobileLayout, MobileHeader, MobileTabBar, MobileLoading, MobileEmpty, studentTabs } from '../../../components/mobile/MobileLayout'
import { ChemMd } from '../../../components/ChemMd'
import { ChemComposer, type ChemComposerHandle } from '../../../components/mobile/ChemComposer'

interface PracticeTask {
  practice_id?: string
  id?: string
  title?: string
  question_count?: number
  difficulty?: string
  deadline?: string
  due_date?: string
  knowledge_points?: string[]
  status?: string
  score?: number
  total?: number
}

interface Question {
  question_id?: string
  id?: string
  content?: string
  question?: string
  question_type?: string
  options?: string[]
  blank_count?: number
}

function fmtDate(d?: string) {
  if (!d) return '—'
  const t = new Date(d)
  return `${t.getFullYear()}/${t.getMonth() + 1}/${t.getDate()}`
}

export function PracticePage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [user] = useState(() => getMobileUser())
  const dueCount = useReviewDue(user?.id)
  const [view, setView] = useState<'list' | 'quiz' | 'result'>('list')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tasks, setTasks] = useState<PracticeTask[]>([])
  const [quiz, setQuiz] = useState<Question[]>([])
  const [qIdx, setQIdx] = useState(0)
  /* 逐题作答：选择题 [下标]；主观题 [空1, 空2, ...]（X1 多空） */
  const [answers, setAnswers] = useState<Record<number, string[]>>({})
  const [practiceId, setPracticeId] = useState('')
  const composerRef = useRef<ChemComposerHandle>(null)
  const [result, setResult] = useState<{
    correct: number
    total: number
    pending: number
    questions?: { question_id: string; is_correct: boolean; reason: string; pending: boolean }[]
  } | null>(null)

  const loadTasks = useCallback(() => {
    if (!user) return
    setLoading(true)
    setError('')
    api.get<{ data?: { tasks?: PracticeTask[] }; tasks?: PracticeTask[] }>(
      `/api/practice/student/${encodeURIComponent(user.id)}/tasks?page_size=50`,
    )
      .then((d) => {
        setTasks(d.data?.tasks || d.tasks || [])
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
  }, [user])

  useEffect(() => {
    document.title = '我的练习'
    loadTasks()
  }, [loadTasks])

  /* 知识地图「练习此点」转场：定向智能练习（REQ-KMV-004） */
  useEffect(() => {
    const kp = (location.state as { smartKp?: string } | null)?.smartKp
    if (kp && !smartOn && !smartBusy) {
      navigate(location.pathname, { replace: true, state: {} })  // 清 state 防重入
      startSmart(kp)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

  /* AI 助教接棒（ADR-0029）：agent 已建会话，直接置入作答状态机续接，
   * 不自调 /smart/start（会留孤儿会话）——载荷解析见 lib/agent-handoff */
  useEffect(() => {
    const sess = parseSmartHandoff(location.state)
    if (sess && !smartOn && !smartBusy) {
      navigate(location.pathname, { replace: true, state: {} })
      setSmartOn(true)
      setPracticeId(sess.practice_id)
      setQuiz([sess.question as Question])
      setQIdx(0)
      setAnswers({})
      setSmartInfo({ theta: sess.theta, answered: 0 })
      setSmartSummary(null)
      setView('quiz')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

  function startPractice(pid: string) {
    if (!user) return
    setLoading(true)
    api.get<{ data?: { questions?: Question[] }; questions?: Question[] }>(
      `/api/practice/student/${encodeURIComponent(user.id)}/practice/${encodeURIComponent(pid)}`,
    )
      .then((d) => {
        const qs = d.data?.questions || d.questions || []
        if (!qs.length) {
          setError('暂无题目')
          setLoading(false)
          return
        }
        setQuiz(qs)
        setPracticeId(pid)
        setQIdx(0)
        setAnswers({})
        setView('quiz')
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
  }

  function submit() {
    if (!user) return
    setLoading(true)
    /* X1：字段统一为 answer——选择题发选项下标（后端映射字母，兼容不变）；
     * 主观题多空以 `;` 分隔（split_answer_blanks 统一解析），单空为纯字符串 */
    /* 装置图（X2 / REQ-DGM-002）：画布有内容时导出 PNG，随提交落 data/answer_attachments/
     *（canvas 内容归属提交当下的题——答题流一次一题，提交即最后一题） */
    const payload = quiz.map((q, i) => {
      const a = answers[i] || []
      const isChoice = (q.question_type ?? '').includes('choice') || (q.options?.length ?? 0) > 0
      const answer = isChoice ? (a[0] ?? '') : a.map((v) => (v || '').trim()).join('; ')
      return { question_id: q.question_id || q.id || '', answer }
    })
    const post = async () => {
      let attachments: { question_id: string; data_url: string }[] | undefined
      try {
        const blob = await composerRef.current?.exportCanvas()
        if (blob && quiz.length > 0) {
          const lastQ = quiz[quiz.length - 1]
          const b64 = await new Promise<string>((resolve) => {
            const fr = new FileReader()
            fr.onload = () => resolve(String(fr.result || ''))
            fr.readAsDataURL(blob)
          })
          attachments = [{ question_id: lastQ.question_id || lastQ.id || '', data_url: b64 }]
        }
      } catch {
        /* 导出失败不阻塞文本作答提交 */
      }
      return api.post<{
      correct_count?: number
      correct?: number
      total_count?: number
        pending_semantic?: number
        questions?: { question_id: string; is_correct: boolean; reason: string; pending: boolean }[]
      }>('/api/practice/submit', {
        practice_id: practiceId, student_id: user.id, answers: payload, attachments,
      })
    }
    post()
      .then((d) => {
        const correct = d.correct_count ?? d.correct ?? 0
        setResult({
          correct,
          total: quiz.length,
          pending: d.pending_semantic ?? 0,
          questions: d.questions,
        })
        setView('result')
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
  }

  const q = quiz[qIdx]
  const qIsChoice = (q?.question_type ?? '').includes('choice') || (q?.options?.length ?? 0) > 0
  const qBlankCount = qIsChoice ? 1 : Math.max(1, q?.blank_count || 1)
  const pending = tasks.filter((t) => t.status !== 'completed')
  const done = tasks.filter((t) => t.status === 'completed')
  const detailOf = (qid?: string) => result?.questions?.find((r) => r.question_id === qid)

  /* ── 智能练习（批 2 S7）：start→answer 逐题，done 出快照 ── */
  const [smartOn, setSmartOn] = useState(false)
  const [smartBusy, setSmartBusy] = useState(false)
  const [smartInfo, setSmartInfo] = useState<{ theta?: number; answered?: number; se?: number } | null>(null)
  const [smartSummary, setSmartSummary] = useState<{
    theta_start?: number | null
    theta_end?: number
    theta_delta?: number
    answered?: number
    uncalibrated_ratio?: number
    suggestion?: string
    weak_points?: { knowledge_point: string; mastery: number }[]
  } | null>(null)

  const answerOf = (qq: Question) => {
    const a = answers[quiz.indexOf(qq)] || []
    const choice = (qq.question_type ?? '').includes('choice') || (qq.options?.length ?? 0) > 0
    return choice ? (a[0] ?? '') : a.map((v) => (v || '').trim()).join('; ')
  }

  const startSmart = (kp?: string) => {
    if (!user) return
    setSmartBusy(true)
    setError('')
    api.post<{ practice_id: string; question: Question; theta?: number }>('/api/practice/smart/start', {
      student_id: user.id, kps: kp ? [kp] : undefined,
    })
      .then((d) => {
        setSmartOn(true)
        setPracticeId(d.practice_id)
        setQuiz([d.question])
        setQIdx(0)
        setAnswers({})
        setSmartInfo({ theta: d.theta, answered: 0 })
        setSmartSummary(null)
        setView('quiz')
        setSmartBusy(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setSmartBusy(false)
      })
  }

  const answerSmart = async () => {
    if (!user || !q) return
    setSmartBusy(true)
    /* 装置图对称（X2）：智能流单题画布随答导出（捉虫 P2-6：此前静默丢弃） */
    let attachments: { question_id: string; data_url: string }[] | undefined
    try {
      const blob = await composerRef.current?.exportCanvas()
      if (blob && q) {
        const b64 = await new Promise<string>((resolve) => {
          const fr = new FileReader()
          fr.onload = () => resolve(String(fr.result || ''))
          fr.readAsDataURL(blob)
        })
        attachments = [{ question_id: q.question_id || q.id || '', data_url: b64 }]
      }
    } catch {
      /* 导出失败不阻塞文本作答 */
    }
    api.post<{
      is_correct: boolean
      reason?: string
      theta?: number
      standard_error?: number
      answered?: number
      done: boolean
      next?: { question: Question }
    }>('/api/practice/smart/answer', {
      student_id: user.id, practice_id: practiceId,
      question_id: q.question_id || q.id || '', answer: answerOf(q), attachments,
    })
      .then((d) => {
        setSmartInfo({ theta: d.theta, answered: d.answered, se: d.standard_error })
        if (d.done) {
          setSmartOn(false)
          api.post<{
            theta_start?: number | null; theta_end?: number; theta_delta?: number
            answered?: number; uncalibrated_ratio?: number; suggestion?: string
            weak_points?: { knowledge_point: string; mastery: number }[]
          }>('/api/practice/smart/finish', { student_id: user.id, practice_id: practiceId })
            .then((s) => {
              setSmartSummary(s)
              setView('result')
              setSmartBusy(false)
            })
            .catch(() => {
              setView('list')
              setSmartBusy(false)
            })
          return
        }
        if (d.next?.question) {
          setQuiz([d.next.question])
          setAnswers({})
          setQIdx(0)
        }
        setSmartBusy(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setSmartBusy(false)
      })
  }

  const exitSmart = () => {
    setSmartOn(false)
    setSmartSummary(null)
    setView('list')
    loadTasks()
  }

  return (
    <MobileLayout
      theme="student"
      header={<MobileHeader title={view === 'quiz' ? `${qIdx + 1} / ${quiz.length}` : '我的练习'} />}
      footer={
        view === 'quiz' ? (
          /* 做题中隐藏 Tab（专注模式），底栏为导航条 */
          <div className="flex justify-between px-4 py-3 bg-bg border-t border-border/60">
            <button
              onClick={() => setQIdx((v) => Math.max(0, v - 1))}
              disabled={qIdx === 0}
              className="flex items-center gap-1 px-5 py-2.5 rounded-full border border-border bg-surface text-[14px] text-ink-secondary disabled:opacity-30"
            >
              <ChevronLeft size={15} />上一题
            </button>
            {smartOn ? (
              <button
                onClick={answerSmart}
                disabled={smartBusy}
                className="flex items-center gap-1 px-7 py-2.5 rounded-full bg-primary text-on-primary text-[14px] font-bold disabled:opacity-40"
              >
                {smartBusy ? '判定中…' : '提交本题'}<ChevronRight size={15} />
              </button>
            ) : qIdx < quiz.length - 1 ? (
              <button
                onClick={() => setQIdx((v) => Math.min(quiz.length - 1, v + 1))}
                className="flex items-center gap-1 px-7 py-2.5 rounded-full bg-primary text-on-primary text-[14px] font-bold"
              >
                下一题<ChevronRight size={15} />
              </button>
            ) : (
              <button
                onClick={submit}
                className="px-7 py-2.5 rounded-full bg-primary text-on-primary text-[14px] font-bold"
              >
                提交
              </button>
            )}
          </div>
        ) : (
          <MobileTabBar
            tabs={studentTabs('practice', navigate, dueCount)}
          />
        )
      }
    >
      {loading && <MobileLoading />}

      {view === 'list' && (!loading || tasks.length > 0) && (
        <div className="p-4">
          {/* 智能练习入口（REQ-SMP-001） */}
          <button
            onClick={() => startSmart()}
            disabled={smartBusy}
            className="w-full mb-4 p-4 rounded-xl border-[1.5px] border-primary/40 bg-primary-soft/60 active:bg-primary-soft transition-colors text-left disabled:opacity-40"
          >
            <div className="flex items-center gap-2 mb-1">
              <Sparkles size={16} className="text-primary" />
              <span className="text-[14.5px] font-bold text-primary">智能练习</span>
              <span className="text-[10.5px] px-2 py-0.5 rounded-full bg-primary text-on-primary">CAT</span>
            </div>
            <p className="text-[12px] text-ink-secondary leading-relaxed">
              按你的薄弱点逐题出题，越答越准，≤8 题一轮。做错的自动进错题本。
            </p>
          </button>
          {error ? (
            <MobileEmpty title="加载失败" hint={error} action={
              <button onClick={loadTasks} className="px-5 py-2 rounded-full bg-primary text-on-primary text-sub font-bold">重试</button>
            } />
          ) : tasks.length === 0 ? (
            <MobileEmpty
              icon={<PenLine size={30} />}
              title="暂无练习任务"
              hint="老师布置练习后会出现在这里，先去问问 AI 助教今天学了什么吧"
            />
          ) : (
            <>
              <div className="text-micro text-ink-muted tracking-[0.15em] mb-2.5">待完成 · {pending.length} 项</div>
              {pending.length === 0 && <p className="text-sub text-ink-secondary pb-2">全部完成，太棒了！</p>}
              {pending.map((t) => (
                <button
                  key={t.practice_id || t.id}
                  onClick={() => startPractice(t.practice_id || t.id || '')}
                  className="w-full text-left p-4 mb-2 rounded-lg border border-border/70 bg-surface active:bg-surface-muted transition-colors"
                >
                  <div className="text-[15px] font-bold text-ink mb-1.5">{t.title || '练习'}</div>
                  <div className="flex gap-3 text-[12px] text-ink-secondary mb-1.5">
                    <span>{t.question_count ?? '?'} 题</span>
                    <span>{t.difficulty || '中等'}</span>
                    <span>截止 {fmtDate(t.deadline || t.due_date)}</span>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {(t.knowledge_points || []).map((k) => (
                      <span key={k} className="text-[10.5px] px-2 py-0.5 rounded-full bg-primary-soft text-primary">{k}</span>
                    ))}
                  </div>
                </button>
              ))}
              {done.length > 0 && (
                <>
                  <div className="text-micro text-ink-muted tracking-[0.15em] mt-5 mb-2.5">已完成 · {done.length} 项</div>
                  {done.map((t) => (
                    <div key={t.practice_id || t.id} className="p-4 mb-2 rounded-lg border border-border/50 bg-surface/60 opacity-70">
                      <div className="text-[15px] font-bold text-ink">{t.title || '练习'}</div>
                      <div className="text-[12px] text-ink-secondary mt-1">得分：{t.score ?? '?'}/{t.total ?? '?'}</div>
                    </div>
                  ))}
                </>
              )}
            </>
          )}
        </div>
      )}

      {!loading && view === 'quiz' && q && smartOn && (
        <div className="px-5 pt-3 flex items-center gap-2 text-micro text-ink-muted">
          <Sparkles size={11} className="text-primary" />
          智能练习 · 第 {(smartInfo?.answered ?? 0) + 1} 题 · 能力估计 {smartInfo?.theta?.toFixed(2) ?? '—'}
          {smartInfo?.se != null && <span>· 不确定度 {smartInfo.se.toFixed(2)}</span>}
          <button onClick={exitSmart} className="ml-auto text-error/70">放弃本轮</button>
        </div>
      )}

      {!loading && view === 'quiz' && q && (
        <div className="p-5">
          <div className="text-[15px] leading-[1.9] text-ink mb-6">
            <ChemMd text={q.content || q.question || ''} />
          </div>
          {qIsChoice ? (
            <div className="space-y-2.5">
              {(q.options || []).map((o, i) => {
                const selected = (answers[qIdx]?.[0] ?? '') === String(i)
                return (
                  <button
                    key={i}
                    onClick={() => setAnswers((prev) => ({ ...prev, [qIdx]: [String(i)] }))}
                    className={`w-full text-left px-4 py-3.5 rounded-lg border-[1.5px] text-[14px] leading-relaxed transition-colors ${
                      selected
                        ? 'border-primary bg-primary-soft text-primary font-medium'
                        : 'border-border bg-surface text-ink'
                    }`}
                  >
                    <ChemMd text={String(o).replace(/^[A-F]\.\s*/, '')} />
                  </button>
                )
              })}
            </div>
          ) : (
            /* 主观题（X1）：多空字段 + 化学符号键盘 + \ce{} 预览 */
            <ChemComposer
              ref={composerRef}
              canvasEnabled={quiz.length === 1}
              blanks={qBlankCount}
              values={answers[qIdx] || Array.from({ length: qBlankCount }, () => '')}
              onChange={(next) => setAnswers((prev) => ({ ...prev, [qIdx]: next }))}
            />
          )}
        </div>
      )}

      {!loading && view === 'result' && (result || smartSummary) && (
        <div className="pt-10 px-5 pb-6">
          <ChemHeroWatermark formula="ΔH" />
          {/* 智能练习结束页只有快照（REQ-SMP-003），普通练习才有逐题对错 */}
          {result && <div className="text-center mb-6">
            <div className="flex items-center justify-center gap-2 mb-2">
              <CheckCircle2 size={28} className={result.pending > 0 ? 'text-ink-muted' : result.correct === result.total ? 'text-success' : 'text-ink-secondary'} />
              <span className="kpi-number font-display text-[52px] font-black leading-none text-ink">
                {result.correct}<span className="text-[22px] text-ink-muted font-normal">/{result.total}</span>
              </span>
            </div>
            <p className="text-[15px] text-ink-secondary mb-1">
              正确率 {result.total > 0 ? Math.round((result.correct / result.total) * 100) : 0}%
            </p>
            {result.pending > 0 && (
              <p className="text-[12.5px] text-ink-muted">AI 正在判定 {result.pending} 道主观题，稍后可在错题本查看结果</p>
            )}
          </div>}

          {/* 智能练习快照（REQ-SMP-003） */}
          {smartSummary && (
            <div className="mb-5 p-4 surface-card">
              <div className="flex items-center gap-2 mb-2">
                <Sparkles size={14} className="text-primary" />
                <span className="text-[13.5px] font-bold text-ink">本轮能力快照</span>
                <span className={`text-[12px] ml-auto font-medium ${(smartSummary.theta_delta ?? 0) >= 0 ? 'text-success' : 'text-error'}`}>
                  {(smartSummary.theta_delta ?? 0) >= 0 ? '+' : ''}{smartSummary.theta_delta?.toFixed(2) ?? '—'} θ
                </span>
              </div>
              <p className="text-[12.5px] text-ink-secondary leading-relaxed mb-1.5">
                共 {smartSummary.answered ?? 0} 题 · 能力估计 {smartSummary.theta_end?.toFixed(2) ?? '—'}
                {(smartSummary.uncalibrated_ratio ?? 0) > 0 && ` · 未标定题占比 ${Math.round((smartSummary.uncalibrated_ratio ?? 0) * 100)}%（按难度先验选题）`}
              </p>
              {smartSummary.suggestion && (
                <p className="text-[12.5px] text-ink-secondary"><ChemGlyph kind="insight" size={16} /> {smartSummary.suggestion}</p>
              )}
              <div className="mt-2.5 flex gap-2">
                <button
                  onClick={() => navigate('/m/review')}
                  className="flex-1 py-2 rounded-full border border-border bg-surface text-[12.5px] text-ink-secondary"
                >
                  去复习
                </button>
                <button
                  onClick={() => startSmart()}
                  disabled={smartBusy}
                  className="flex-1 py-2 rounded-full bg-primary text-on-primary text-[12.5px] font-medium disabled:opacity-40"
                >
                  再来一轮
                </button>
              </div>
              {!!smartSummary.weak_points?.length && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {smartSummary.weak_points.map((w) => (
                    <span key={w.knowledge_point} className="text-[10.5px] px-2 py-0.5 rounded-full bg-surface-muted text-ink-secondary">
                      {w.knowledge_point} · {Math.round(w.mastery * 100)}%
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* 逐题对错 + 评语（X1 / REQ-CMP-006） */}
          {result && <div className="space-y-2 mb-6">
            {quiz.map((item, i) => {
              const r = detailOf(item.question_id || item.id)
              const ok = r?.is_correct
              return (
                <div
                  key={item.question_id || item.id || i}
                  className={`p-3 rounded-lg border text-sub leading-relaxed ${
                    r?.pending
                      ? 'border-border/60 bg-surface/60 text-ink-muted'
                      : ok
                        ? 'border-success/30 bg-success-soft/40'
                        : 'border-error/30 bg-error-soft/50'
                  }`}
                >
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className={`text-[12px] font-bold ${r?.pending ? 'text-ink-muted' : ok ? 'text-success' : 'text-error'}`}>
                      {r?.pending ? '判定中' : ok ? '✓ 对' : '✕ 错'}
                    </span>
                    <span className="text-ink-muted">第 {i + 1} 题</span>
                  </div>
                  <div className="text-ink-secondary line-clamp-2">
                    <ChemMd text={(item.content || item.question || '').slice(0, 80)} />
                  </div>
                  {r?.reason && !r.pending && <div className="text-[11.5px] text-ink-muted mt-1">{r.reason}</div>}
                </div>
              )
            })}
          </div>}

          <button
            onClick={() => {
              setView('list')
              loadTasks()
            }}
            className="w-full py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold"
          >
            返回练习列表
          </button>
        </div>
      )}
    </MobileLayout>
  )
}
