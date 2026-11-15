/* agent-handoff 接棒载荷解析（ADR-0029 批⑤，spec #23）：三页共用守卫式
 * 解析——形状不对返回 null/false（诚实降级），互不干扰、防重入由页面消费
 * 后清 state（smartKp 先例）。 */
import { describe, expect, it } from 'vitest'
import {
  parseSmartHandoff, parseTrainingHandoff, parseReviewFromChat,
} from '../src/lib/agent-handoff'

const smart = { practice_id: 'smart_1_1', question: { question_id: 'q1', content: 'x' }, theta: 0.3 }
const training = { session_id: 'train_s_1', questions: [{ question_id: 'v1' }], total_count: 1 }

describe('parseSmartHandoff', () => {
  it('合法载荷 → 会话对象', () => {
    expect(parseSmartHandoff({ smartSession: smart })).toEqual(smart)
  })
  it('缺 practice_id / 缺首题 → null（页面跳过接棒）', () => {
    expect(parseSmartHandoff({ smartSession: { question: smart.question } })).toBeNull()
    expect(parseSmartHandoff({ smartSession: { practice_id: 'x' } })).toBeNull()
  })
  it('state 非 object → null', () => {
    expect(parseSmartHandoff(null)).toBeNull()
    expect(parseSmartHandoff('x')).toBeNull()
  })
  it('与 training/fromChat 键互不干扰', () => {
    expect(parseSmartHandoff({ training, fromChat: 'review' })).toBeNull()
  })
})

describe('parseTrainingHandoff', () => {
  it('合法载荷 → 训练会话', () => {
    expect(parseTrainingHandoff({ training })?.session_id).toBe('train_s_1')
  })
  it('空 questions / 缺 session_id → null', () => {
    expect(parseTrainingHandoff({ training: { session_id: 'x', questions: [] } })).toBeNull()
    expect(parseTrainingHandoff({ training: { questions: [{}] } })).toBeNull()
  })
  it('与 smartSession 键互不干扰', () => {
    expect(parseTrainingHandoff({ smartSession: smart })).toBeNull()
  })
})

describe('parseReviewFromChat', () => {
  it('fromChat=review → true；其他值/缺失 → false', () => {
    expect(parseReviewFromChat({ fromChat: 'review' })).toBe(true)
    expect(parseReviewFromChat({ fromChat: 'other' })).toBe(false)
    expect(parseReviewFromChat({})).toBe(false)
    expect(parseReviewFromChat(null)).toBe(false)
  })
})
