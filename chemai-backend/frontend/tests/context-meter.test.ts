/* context-meter.ts 的算式与后端 `agent/context_meter.build_context_event` 必须同形：
 * 同一批消息、同一个窗口，页面上的圆环和流式结束后的精确值不该差出一截。
 * 窗口在这里没有默认值可退回（ADR-0023 Q14），所以用例都得显式给一个。 */
import { describe, expect, it } from 'vitest'
import { contextPercent, estimateContextFromHistory, estimateTokens } from '../src/lib/context-meter'
import type { ChatItem } from '../src/components/chat/MessageList'

const item = (kind: 'user' | 'ai' | 'tool', text: string): ChatItem =>
  ({ id: kind, kind, text } as ChatItem)

const HISTORY = [item('user', '给张三出 5 道盐类水解的题'.repeat(20)),
                 item('ai', '已生成'.repeat(40))]

/** 与 `tests/test_context_meter.py::GOLDEN_PERCENT` 同一组三元组，逐条对着生产代码
 *  `contextPercent` 断言（不是测试自己抄一遍算式 —— 那样改了前端算式也不会红，上一版就是这样）。
 *  两份清单由 `test_context_meter.py::the_two_golden_lists_are_the_same_numbers` 钉住同步。 */
const GOLDEN: [number, number, number][] = [
  [640, 128_000, 1], [3200, 128_000, 3], [5760, 128_000, 5],
  [1280, 128_000, 1], [64_000, 128_000, 50], [99_900, 128_000, 78],
]

describe('上下文估算', () => {
  it('用的就是传进来的那个窗口', () => {
    const ev = estimateContextFromHistory(HISTORY, 64_000)
    expect(ev.context_window).toBe(64_000)
    expect(ev.used_tokens).toBeGreaterThan(0)
    expect(ev.percent).toBe(contextPercent(ev.used_tokens, 64_000))
  })

  it('同一个数：三段之和等于 used，百分比按同一个算式', () => {
    const ev = estimateContextFromHistory(HISTORY, 32_000)
    const b = ev.breakdown
    expect(b.system_tokens + b.tools_tokens + b.message_tokens).toBe(ev.used_tokens)
    expect(ev.percent).toBe(contextPercent(ev.used_tokens, 32_000))
  })

  it('窗口为 0 不炸百分比，也不给一个假总数', () => {
    const ev = estimateContextFromHistory(HISTORY, 0)
    expect(ev.percent).toBe(0)
    expect(ev.context_window).toBe(0)
  })

  it('百分比按 half-up 取整，与后端同一组黄金三元组', () => {
    for (const [used, win, pct] of GOLDEN) {
      expect(contextPercent(used, win)).toBe(pct)
    }
  })

  it('估算密度与后端一致：4 字符 1 token 加固定结构开销', () => {
    expect(estimateTokens('abcd')).toBe(1 + 4)
    expect(estimateTokens('')).toBe(4)
  })
})
