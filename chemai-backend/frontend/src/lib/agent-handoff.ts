/* agent-handoff.ts — AI 助教接棒载荷解析（ADR-0029，纯函数）。
 *
 * agent 开学习会话后经 navigate 事件跳页，会话上下文经 location.state 传递；
 * 三个消费页（Practice/WrongBook/ReviewCenter）共用这里的守卫式解析——
 * 形状不对一律返回 null/false（诚实降级），页面据此跳过接棒。
 * 载荷契约：docs/designs/learning-skills-contract.md。
 */

export interface SmartHandoff {
  practice_id: string
  question: Record<string, unknown>
  theta?: number
}

export interface TrainingHandoff {
  session_id: string
  questions: unknown[]
  total_count?: number
}

function stateOf(state: unknown): Record<string, unknown> {
  return (state && typeof state === 'object') ? state as Record<string, unknown> : {}
}

/** smart_practice 接棒：agent 已建会话（practice_id + 首题），页面续接作答。 */
export function parseSmartHandoff(state: unknown): SmartHandoff | null {
  const s = stateOf(state).smartSession as SmartHandoff | undefined | null
  if (!s || typeof s.practice_id !== 'string' || !s.practice_id) return null
  if (!s.question || typeof s.question !== 'object') return null
  return s
}

/** variant_training 接棒：端侧已建好的训练会话（变式题已落库）。 */
export function parseTrainingHandoff<T extends TrainingHandoff>(state: unknown): T | null {
  const t = stateOf(state).training as T | undefined | null
  if (!t || typeof t.session_id !== 'string' || !t.session_id) return null
  if (!Array.isArray(t.questions) || t.questions.length === 0) return null
  return t
}

/** review_today 接棒标记（纯提示，无行为差异）。 */
export function parseReviewFromChat(state: unknown): boolean {
  return stateOf(state).fromChat === 'review'
}
