/* context-meter.ts — 上下文占用前端工具(DSH token-meter 启发式移植)
 * 流式回复结束时后端下发 provider usage 锚定的精确值;
 * 历史加载/新会话用同款启发式(4 字符 ≈ 1 token + 结构开销)估算占位。
 * 窗口一律由后端下发(`/chat/history` 的 `context_window` 与 SSE `context` 事件),
 * 这里不留默认值 —— 后端配置改成 64k 而前端还按 128k 算,圆环会安静地显示一个错一半的数。 */

import type { ChatItem } from '../components/chat/MessageList'
import type { ContextEvent } from './agent-sse'

const CHARS_PER_TOKEN = 4
const ROLE_OVERHEAD = 4
const BLOCK_OVERHEAD = 4

export function estimateTokens(text: string): number {
  return Math.ceil((text || '').length / CHARS_PER_TOKEN) + BLOCK_OVERHEAD
}

/** 占用百分比：与后端 `agent/context_meter` 同一口径 —— half-up 取整、封顶 100、窗口 0 不炸。
 *  单独可导入是为了让黄金三元组用例走生产代码断言，而不是在测试里再抄一遍算式。 */
export function contextPercent(used: number, windowSize: number): number {
  return windowSize > 0 ? Math.min(100, Math.round((used / windowSize) * 100)) : 0
}

/** 按历史消息估算上下文占用(三段构成:系统提示/工具/对话)。 */
export function estimateContextFromHistory(
  items: ChatItem[],
  windowSize: number,
): ContextEvent {
  let system = 0
  let tools = 0
  let messages = 0
  for (const it of items) {
    const text =
      it.kind === 'tool' ? JSON.stringify(it.card?.data ?? '') : (it as { text?: string }).text ?? ''
    const tokens = estimateTokens(text) + ROLE_OVERHEAD
    if (it.kind === 'system') system += tokens
    else if (it.kind === 'tool') tools += tokens
    else messages += tokens
  }
  const used = system + tools + messages
  return {
    type: 'context',
    used_tokens: used,
    context_window: windowSize,
    percent: contextPercent(used, windowSize),
    breakdown: { system_tokens: system, tools_tokens: tools, message_tokens: messages },
  }
}
