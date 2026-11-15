/* mobile-stream.test.ts — S4 移动端流消费的移动侧纯函数单测：
 *  - allowedMobileNavigate：/m/* 白名单（REQ-MSC-003）
 *  - historyToItems：工具卡结构化还原（REQ-MSC-001） */

import { describe, it, expect } from 'vitest'
import { allowedMobileNavigate } from '../src/lib/mobile-routes'
import { historyToItems } from '../src/lib/mobile-chat-history'

describe('allowedMobileNavigate', () => {
  it('放行白名单内的移动路由', () => {
    expect(allowedMobileNavigate('/m')).toBe('/m')
    expect(allowedMobileNavigate('/m/practice')).toBe('/m/practice')
    expect(allowedMobileNavigate('/m/review')).toBe('/m/review')
  })

  it('放行动态前缀路由（工件详情）', () => {
    expect(allowedMobileNavigate('/m/artifact/abc123')).toBe('/m/artifact/abc123')
  })

  it('拒绝教师页面目标（Gateway 的 navigate 目标表是教师页集合）', () => {
    expect(allowedMobileNavigate('/exam-v2')).toBeNull()
    expect(allowedMobileNavigate('/diagnosis')).toBeNull()
    expect(allowedMobileNavigate('/students')).toBeNull()
    expect(allowedMobileNavigate('/exam')).toBeNull()
  })

  it('拒绝 /m 变体路径穿越与空值', () => {
    expect(allowedMobileNavigate('/m/secret')).toBeNull()
    expect(allowedMobileNavigate('/m/../teacher')).toBeNull()
    expect(allowedMobileNavigate('')).toBeNull()
  })
})

describe('historyToItems', () => {
  it('user/ai 映射为气泡，空内容跳过', () => {
    const items = historyToItems(
      [
        { type: 'user', content: '什么是盐类水解' },
        { type: 'ai', content: '先想一想……' },
        { type: 'ai', content: '' },
      ],
      () => 'x',
    )
    expect(items).toHaveLength(2)
    expect(items[0].kind).toBe('user')
    expect(items[1].kind).toBe('ai')
  })

  it('工具条目还原为工具卡（REQ-MSC-001：不再只剩纯文本）', () => {
    const items = historyToItems(
      [
        { type: 'tool', name: 'chemistry_tutor', status: 'done', data: { answer: 'ok' } },
        { type: 'tool', name: 'web_search', status: 'error' },
      ],
      () => 'x',
    )
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ kind: 'tool', card: { tool: 'chemistry_tutor', status: 'done' } })
    expect(items[1]).toMatchObject({ kind: 'tool', card: { tool: 'web_search', status: 'error' } })
  })

  it('旧格式（role 字段）兼容', () => {
    const items = historyToItems([{ role: 'human', content: 'hi' }], () => 'x')
    expect(items[0].kind).toBe('user')
  })

  it('undefined/空数组安全', () => {
    expect(historyToItems(undefined, () => 'x')).toEqual([])
    expect(historyToItems([], () => 'x')).toEqual([])
  })
})
