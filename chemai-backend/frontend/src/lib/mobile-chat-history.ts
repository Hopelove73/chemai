/* mobile-chat-history.ts — 移动端对话历史 → ChatItem 的纯映射（student-daily-loop S4）。
 * 后端 /chat/history 对 tool 消息返回结构化条目（name/status/data），
 * 工具卡在重开对话后完整还原（REQ-MSC-001），不再只剩纯文本。 */

import type { ChatItem } from '../components/chat/MessageList'

export interface MobileHistoryMessage {
  type?: string
  role?: string
  content?: string
  name?: string
  status?: string
  data?: unknown
}

/** /chat/history 顶层的 gate 载荷（对话自己的待决门——家长派任务卡等） */
export interface MobileHistoryGate {
  gate_id?: string
  draft_id?: string
  version?: number
  action?: string
  message?: string
}

export function historyToItems(
  messages: MobileHistoryMessage[] | undefined,
  newId: () => string,
  gate?: MobileHistoryGate | null,
): ChatItem[] {
  const items: ChatItem[] = []
  if (gate && gate.gate_id) {
    // 刷新后 pending 门卡照常可见可决（教师端 ChatPage 恢复语义同源）；
    // 缺它：门开着时家长打字会被后端判为「打回+意见」（channel reject 分支）
    items.push({
      id: newId(),
      kind: 'approval',
      approval: {
        text: gate.message || '请确认操作',
        status: 'pending',
        action: gate.action || '',
        draft_id: gate.draft_id,
        version: gate.version,
        gate_id: gate.gate_id,
      },
    })
  }
  for (const m of messages || []) {
    const isUser = m.type === 'user' || m.role === 'user' || m.role === 'human'
    if (m.type === 'tool' || (m.name && !m.type)) {
      const status = m.status === 'error' ? 'error' : 'done'
      items.push({
        id: newId(),
        kind: 'tool',
        card: { tool: m.name || '', status, data: (m.data ?? undefined) as Record<string, unknown> | undefined },
      })
      continue
    }
    const text = String(m.content || '')
    if (!text.trim()) continue
    items.push({ id: newId(), kind: isUser ? 'user' : 'ai', text })
  }
  return items
}
