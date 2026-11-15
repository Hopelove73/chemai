/* WrongBookPage — 错题本 + 举一反三闭环（student-daily-loop S3 / REQ-WBR-001..004）。
 * 错因三色标签（probe_dimension 归因直达学生）+ 逐空对比 + 标已掌握
 * （POST /wrong/{qid}/master）+ 变式训练会话 + 消灭率清单（mastery 单一真源）。 */

import { useCallback, useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { parseTrainingHandoff } from '../../../lib/agent-handoff'
import { motion } from 'framer-motion'
import { staggerContainer, staggerItem } from '../../../lib/motion'
import {
  BookX, ChevronRight, Check, Target, Sparkles, Flame,
} from 'lucide-react'
import { api } from '../../../lib/api'
import { getMobileUser } from '../../../lib/mobile-auth'
import { useReviewDue } from '../../../lib/use-review-due'
import { MobileLayout, MobileHeader, MobileTabBar, MobileLoading, MobileEmpty, MobileFullPane, studentTabs } from '../../../components/mobile/MobileLayout'
import { ChemMd } from '../../../components/ChemMd'
import { ChemComposer } from '../../../components/mobile/ChemComposer'
import { splitAnswerBlanks } from '../../../lib/chem-blanks'

interface WrongQuestion {
  question_id?: string
  mastered?: boolean
  content?: string
  options?: string[] | null
  answer?: string
  your_answer?: string
  knowledge_points?: string[]
  probe_dimension?: string | null
  question_type?: string | null
  wrong_count?: number
  last_error_at?: string | null
  analysis?: string
}

interface ElimPoint {
  knowledge_point: string
  mastery: number
  samples: number
  eliminated: boolean
}

interface ElimInfo {
  total: number
  eliminated: number
  points: ElimPoint[]
}

interface VariantQuestion {
  question_id: string
  content: string
  question_type?: string | null
  options?: string[] | null
  answer?: string | null
  knowledge_points?: string[]
}

interface TrainingSession {
  session_id: string
  questions: VariantQuestion[]
  total_count: number
}

const BARRIER_TAGS: Record<string, { label: string; cls: string }> = {
  concept: { label: '概念理解', cls: 'bg-accent-interactive-subtle text-accent-interactive' },
  reading: { label: '审题障碍', cls: 'bg-warning-soft text-warning' },
  expression: { label: '表述障碍', cls: 'bg-accent-interactive-subtle text-accent-interactive' },
}

export function WrongBookPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [user] = useState(() => getMobileUser())
  const dueCount = useReviewDue(user?.id)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [items, setItems] = useState<WrongQuestion[]>([])
  const [expanded, setExpanded] = useState<string | null>(null)
  const [elim, setElim] = useState<ElimInfo | null>(null)
  const [elimOpen, setElimOpen] = useState(false)
  const [training, setTraining] = useState<TrainingSession | null>(null)
  const [trainIdx, setTrainIdx] = useState(0)
  const [trainAnswers, setTrainAnswers] = useState<Record<string, string[]>>({})
  const [trainResult, setTrainResult] = useState<{ correct: number; total: number } | null>(null)
  const [generating, setGenerating] = useState(false)

  const sid = user?.id || ''

  const load = useCallback(() => {
    if (!user) return
    setLoading(true)
    api.get<{ data?: { wrong_questions?: WrongQuestion[] }; wrong_questions?: WrongQuestion[] }>(
      `/api/practice/wrong/list?student_id=${encodeURIComponent(user.id)}&limit=30`,
    )
      .then((d) => {
        setItems(d.data?.wrong_questions || d.wrong_questions || [])
        setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
    api.get<ElimInfo>(`/api/analytics/student/${encodeURIComponent(user.id)}/elimination`)
      .then(setElim)
      .catch(() => {})
  }, [user])

  useEffect(() => {
    document.title = '错题本'
    load()
  }, [load])

  /* AI 助教接棒（ADR-0029）：variant_training 已在端侧建好会话（落库变式题），
   * 直接置入训练视图作答，不再走页内「举一反三」生成步骤 */
  useEffect(() => {
    const t = parseTrainingHandoff<TrainingSession>(location.state)
    if (t) {
      navigate(location.pathname, { replace: true, state: {} })  // 清 state 防重入
      setTraining(t)
      setTrainIdx(0)
      setTrainAnswers({})
      setTrainResult(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state])

  const markMastered = (qid: string) => {
    if (!sid) return
    api.post(`/api/practice/wrong/${encodeURIComponent(qid)}/master?student_id=${encodeURIComponent(sid)}`, {})
      .then(load)  // 服务端持久化标记（卡片 last_signal=remembered），刷新后重拉仍在
      .catch(() => {})
  }

  const startTraining = async (qid: string) => {
    if (!sid) return
    setGenerating(true)
    try {
      const gen = await api.post<{ success: boolean; variants?: VariantQuestion[] }>(
        '/api/practice/wrong-topic/variant/generate',
        { original_question_id: qid, quantity: 2 },
      )
      if (!gen.success || !gen.variants?.length) {
        setTraining({ session_id: '', questions: [], total_count: 0 })
        return
      }
      const created = await api.post<TrainingSession>('/api/practice/wrong-topic/training/create', {
        student_id: sid,
        question_ids: gen.variants.map((v) => v.question_id),
      })
      setTraining(created)
      setTrainIdx(0)
      setTrainAnswers({})
      setTrainResult(null)
    } catch {
      setTraining({ session_id: '', questions: [], total_count: 0 })
    } finally {
      setGenerating(false)
    }
  }

  const submitTraining = async () => {
    if (!training || !sid) return
    const answers = Object.entries(trainAnswers).map(([question_id, arr]) => ({
      question_id,
      answer: (arr || []).map((v) => (v || '').trim()).join('; '),
    }))
    try {
      const r = await api.post<{ correct_count?: number; total_count?: number }>(
        '/api/practice/wrong-topic/training/submit',
        { session_id: training.session_id, student_id: sid, answers },
      )
      setTrainResult({ correct: r.correct_count ?? 0, total: training.questions.length })
    } catch {
      setTrainResult({ correct: 0, total: training.questions.length })
    }
  }

  const tq = training?.questions[trainIdx]
  const tqIsChoice = (tq?.question_type ?? '').includes('choice') || (tq?.options?.length ?? 0) > 0

  const tabs = studentTabs('wrong', navigate, dueCount)

  return (
    <MobileLayout
      theme="student"
      header={<MobileHeader title={`错题本 · ${items.length} 道`} />}
      footer={<MobileTabBar tabs={tabs} />}
    >
      {loading && items.length === 0 && <MobileLoading />}
      {!loading && error && (
        <MobileEmpty title="加载失败" hint={error} action={
          <button onClick={load} className="px-5 py-2 rounded-full bg-primary text-on-primary text-sub font-bold">重试</button>
        } />
      )}
      {!loading && !error && items.length === 0 && (
        <MobileEmpty
          icon={<BookX size={30} />}
          title="暂无错题记录"
          hint="做错的题会自动收进错题本，去练习页挑战一下吧"
          action={
            <button onClick={() => navigate('/m/practice')} className="px-5 py-2 rounded-full bg-primary text-on-primary text-sub font-bold">
              去练习
            </button>
          }
        />
      )}

      {/* 消灭率清单（REQ-WBR-004） */}
      {!loading && elim && elim.total > 0 && (
        <div className="mx-3.5 mt-3 p-3.5 surface-card">
          <button onClick={() => setElimOpen((v) => !v)} className="w-full flex items-center gap-2 cursor-pointer">
            <Target size={16} className="text-primary shrink-0" />
            <span className="text-sub font-semibold text-ink">薄弱点消灭</span>
            <span className="text-[12px] text-ink-muted flex-1 text-left">已消灭 {elim.eliminated}/{elim.total}</span>
            <ChevronRight size={15} className={`text-ink-muted transition-transform ${elimOpen ? 'rotate-90' : ''}`} />
          </button>
          <div className="mt-2 h-1.5 rounded-full bg-surface-muted overflow-hidden">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: elim.total ? `${(elim.eliminated / elim.total) * 100}%` : '0%' }}
            />
          </div>
          {elimOpen && (
            <ul className="mt-2.5 space-y-1.5 border-t border-border/50 pt-2.5">
              {elim.points.map((p) => (
                <li key={p.knowledge_point} className="flex items-center gap-2 text-[12px]">
                  <span className={p.eliminated ? 'text-success' : 'text-ink-muted'}>{p.eliminated ? '✓' : '○'}</span>
                  <span className="flex-1 min-w-0 truncate text-ink-secondary">{p.knowledge_point}</span>
                  <span className="text-ink-muted w-16 text-right">掌握 {Math.round(p.mastery * 100)}%</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <motion.div className="p-3.5" variants={staggerContainer} initial="initial" animate="animate">
        {[...items].sort((a, b) => Number(a.mastered || false) - Number(b.mastered || false)).map((w, i) => ({ w, vi: i < 8 ? staggerItem : ({} as typeof staggerItem) })).map(({ w, vi }) => {
          const kp = (w.knowledge_points || [])[0] || '综合'
          const qid = `${w.question_id || w.content || 'q'}-${w.last_error_at ?? ''}`
          const open = expanded === qid
          const barrier = w.probe_dimension ? BARRIER_TAGS[w.probe_dimension] : undefined
          const yourBlanks = splitAnswerBlanks(w.your_answer)
          const answerBlanks = splitAnswerBlanks(w.answer)
          const perBlank = yourBlanks.length > 1 || answerBlanks.length > 1
          return (
            <motion.div key={qid} variants={vi} className="mb-2 rounded-lg border border-border/70 bg-surface">
              <button
                onClick={() => setExpanded(open ? null : qid)}
                className="w-full text-left p-4 cursor-pointer"
              >
                <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                  <span className="text-[10.5px] px-2 py-0.5 rounded-full bg-primary-soft text-primary">{kp}</span>
                  {barrier && (
                    <span className={`text-[10.5px] px-2 py-0.5 rounded-full ${barrier.cls}`}>{barrier.label}</span>
                  )}
                  {w.mastered && (
                    <span className="text-[10.5px] px-2 py-0.5 rounded-full bg-success-soft text-success flex items-center gap-0.5">
                      <Check size={10} />已掌握
                    </span>
                  )}
                  <span className="text-micro text-ink-muted ml-auto">
                    {w.last_error_at ? w.last_error_at.replaceAll('/', '-') : ''}
                    {w.wrong_count ? ` · 做错 ${w.wrong_count} 次` : ''}
                  </span>
                </div>
                <p className="text-[14px] leading-relaxed text-ink">{w.content}</p>
              </button>
              {open && (
                <div className="px-4 pb-4">
                  <div className="p-3 rounded-lg bg-error-soft/60 border border-error/20 text-sub leading-relaxed">
                    {perBlank ? (
                      <>
                        {answerBlanks.map((ans, i) => (
                          <div key={i} className="flex gap-2">
                            <span className="text-ink-muted shrink-0">第{i + 1}空</span>
                            <div className="min-w-0">
                              <div className="text-error">你的：{yourBlanks[i] || '—'}</div>
                              <div className="text-success">正确：{ans}</div>
                            </div>
                          </div>
                        ))}
                      </>
                    ) : (
                      <>
                        <p className="text-error">你的答案：{w.your_answer || '—'}</p>
                        <p className="text-success mt-0.5">正确答案：{w.answer || '—'}</p>
                      </>
                    )}
                    {w.analysis && <p className="text-ink-secondary mt-1.5">{w.analysis}</p>}
                  </div>
                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => markMastered(qid)}
                      disabled={w.mastered}
                      className="flex-1 py-2.5 rounded-full border-[1.5px] border-success/40 bg-success-soft/40 text-success text-sub font-medium disabled:opacity-40"
                    >
                      {w.mastered ? '已掌握' : '标已掌握'}
                    </button>
                    <button
                      onClick={() => startTraining(qid)}
                      disabled={generating}
                      className="flex-1 py-2.5 rounded-full bg-primary text-on-primary text-sub font-bold flex items-center justify-center gap-1.5 disabled:opacity-40"
                    >
                      <Sparkles size={13} />{generating ? '生成变式中…' : '举一反三'}
                    </button>
                  </div>
                </div>
              )}
            </motion.div>
          )
        })}
      </motion.div>

      {/* 变式训练会话（REQ-WBR-003） */}
      <MobileFullPane
        open={!!training}
        title="举一反三 · 强化训练"
        onClose={() => setTraining(null)}
      >
        {!training && <MobileLoading />}
        {training && training.total_count === 0 && (
          <MobileEmpty
            icon={<Flame size={28} className="text-ink-muted" />}
            title="变式题生成失败"
            hint="AI 暂时没能生成这道题的变式，稍后再试"
            action={<button onClick={() => setTraining(null)} className="px-5 py-2 rounded-full bg-primary text-on-primary text-sub font-bold">返回错题本</button>}
          />
        )}
        {training && training.total_count > 0 && !trainResult && tq && (
          <div>
            <div className="text-micro text-ink-muted mb-2">
              {trainIdx + 1} / {training.total_count} · {tq.knowledge_points?.[0] || '综合'}
            </div>
            <div className="p-4 surface-card text-[15px] leading-[1.9] text-ink mb-4">
              <ChemMd text={tq.content || ''} />
            </div>
            {tqIsChoice ? (
              <div className="space-y-2.5">
                {(tq.options || []).map((o, i) => {
                  const selected = (trainAnswers[tq.question_id]?.[0] ?? '') === String(i)
                  return (
                    <button
                      key={i}
                      onClick={() => setTrainAnswers((prev) => ({ ...prev, [tq.question_id]: [String(i)] }))}
                      className={`w-full text-left px-4 py-3.5 rounded-lg border-[1.5px] text-[14px] leading-relaxed transition-colors ${
                        selected ? 'border-primary bg-primary-soft text-primary font-medium' : 'border-border bg-surface text-ink'
                      }`}
                    >
                      {String(o).replace(/^[A-F]\.\s*/, '')}
                    </button>
                  )
                })}
              </div>
            ) : (
              <ChemComposer
                blanks={1}
                values={trainAnswers[tq.question_id] || ['']}
                onChange={(next) => setTrainAnswers((prev) => ({ ...prev, [tq.question_id]: next }))}
              />
            )}
            <div className="mt-5">
              {trainIdx < training.total_count - 1 ? (
                <button
                  onClick={() => setTrainIdx(trainIdx + 1)}
                  disabled={!(trainAnswers[tq.question_id]?.some((v) => (v || '').trim()))}
                  className="w-full py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold disabled:opacity-40"
                >
                  下一题
                </button>
              ) : (
                <button
                  onClick={submitTraining}
                  disabled={!(trainAnswers[tq.question_id]?.some((v) => (v || '').trim()))}
                  className="w-full py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold disabled:opacity-40"
                >
                  提交训练
                </button>
              )}
            </div>
          </div>
        )}
        {training && trainResult && (
          <div className="text-center pt-14 px-6">
            <Target size={34} className="mx-auto text-primary mb-3" />
            <div className="kpi-number font-display text-[44px] font-black text-ink leading-none mb-2">
              {trainResult.correct}<span className="text-[20px] text-ink-muted font-normal">/{trainResult.total}</span>
            </div>
            <p className="text-[13.5px] text-ink-secondary mb-6">
              {trainResult.correct === trainResult.total ? '全部消灭，这个知识点稳了！' : '做错的变式已回炉错题本，继续消灭它们'}
            </p>
            <button
              onClick={() => {
                setTraining(null)
                load()
              }}
              className="px-8 py-3 rounded-full bg-primary text-on-primary text-[14px] font-bold"
            >
              返回错题本
            </button>
          </div>
        )}
      </MobileFullPane>
    </MobileLayout>
  )
}
