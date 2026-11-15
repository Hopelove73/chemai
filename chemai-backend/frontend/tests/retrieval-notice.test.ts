/* 相似题检索降级的那句话（工单 VS1）。
 *
 * 后端在 /api/question/generate 的响应里带 `retrieval`。这一层没干活时题照样生成，只是不再
 * 锚在历年真题上 —— 页面长得一模一样，区别全靠这句话说出来，所以它的三种输入都要钉住。
 */
import { describe, expect, it } from 'vitest'
import { retrievalNotice } from '../src/lib/retrieval-notice'

describe('retrievalNotice', () => {
  it('正常时什么都不说', () => {
    expect(retrievalNotice({ used: 'vector', degraded: false })).toBeNull()
    expect(retrievalNotice({ used: 'blueprint' })).toBeNull()
    expect(retrievalNotice(undefined)).toBeNull()
    expect(retrievalNotice(null)).toBeNull()
  })

  it('降级时把后端给的原因带出来', () => {
    expect(
      retrievalNotice({ degraded: true, reason: 'Cannot open header file（本轮只用简单匹配）' }),
    ).toBe(
      '相似题检索暂时不可用：Cannot open header file（本轮只用简单匹配）（本次生成的题目没锚定历年真题）',
    )
  })

  it('后端没给原因也要说话，而不是退回"什么都没说"', () => {
    expect(retrievalNotice({ degraded: true, reason: '   ' })).toBe(
      '相似题检索暂时不可用（本次生成的题目没锚定历年真题）',
    )
    expect(retrievalNotice({ degraded: true })).toBe(
      '相似题检索暂时不可用（本次生成的题目没锚定历年真题）',
    )
  })
})
