/* T2 前端：已处置的门在时间线上要留痕（验收 §7.3「五态都在，刷新后仍是同一部史」）。
 *
 * 数据来自历史接口的 `closed_gates`（账本），不是门行 —— 门行是当前态，重开门会覆盖它。
 * 卡片复用 ApprovalCard 的静态终态，不另造一套渲染。
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../src/pages/teacher/ChatPage'
import { ToastProvider } from '../src/components/ui'
import { __resetPendingCountForTests } from '../src/lib/use-pending-count'
import { __resetInDoubtForTests } from '../src/lib/use-in-doubt'

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

function reply(url: string) {
  if (url.includes('/api/agent/gates/pending') || url.includes('/api/agent/gates/in-doubt')) {
    return { items: [], owned_total: 0, unowned_items: [], unowned_total: 0 }
  }
  if (url.includes('/api/agent/chat/conversations')) {
    return { conversations: [
      { thread_id: 't-target', title: '期中成绩分析', last_at: '2026-09-25T00:00:00' },
    ] }
  }
  if (url.includes('/api/agent/chat/history/')) {
    return {
      messages: [{ role: 'user', content: '给周翔做一份学习计划' }],
      run: null,
      gate: null,
      context_window: 128_000,
      closed_gates: [
        { gate_id: 'g-1', decision: 'approved', actor_user_id: 'u-t',
          kind: 'draft', action: 'send_student', message: '计划待发',
          decided_at: '2026-09-25T00:05:00' },
        { gate_id: 'g-2', decision: 'rejected', actor_user_id: 'u-t',
          kind: 'draft', action: 'send_student', message: '计划待发',
          decided_at: '2026-09-25T00:10:00' },
        { gate_id: 'g-3', decision: 'cancelled', actor_user_id: 'u-t',
          kind: 'boolean', action: '', message: '执行命令',
          decided_at: '2026-09-25T00:15:00' },
      ],
    }
  }
  if (url.includes('/api/auth/me')) return { role: 'teacher', menus: ['chat', 'pending'] }
  return {}
}

beforeEach(() => {
  Element.prototype.scrollIntoView = () => {}
  __resetPendingCountForTests()
  __resetInDoubtForTests()
  localStorage.clear()
  sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => json(reply(String(input)))))
})

describe('已处置的门在时间线上留痕', () => {
  it('批准 / 打回 / 放弃三种终态都渲染出来，且不再出现待批卡', async () => {
    render(
      <ToastProvider>
        <MemoryRouter initialEntries={['/chat?thread=t-target']}>
          <Routes><Route path="/chat" element={<ChatPage />} /></Routes>
        </MemoryRouter>
      </ToastProvider>,
    )
    await screen.findByText('给周翔做一份学习计划')
    expect(await screen.findByText('已批准执行：计划待发')).toBeInTheDocument()
    expect(screen.getByText('已打回，按意见修订中：计划待发')).toBeInTheDocument()
    expect(screen.getByText('已放弃：执行命令')).toBeInTheDocument()
    // 终态卡不是待批卡：不能出现需要老师再确认的字样
    expect(screen.queryByText(/需要确认/)).toBeNull()
  })

  it('多次批准各带标签：不再是一摞无法区分的「已批准执行」（2026-10-26 实测用户指出）', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/agent/chat/history/')) {
        return json({
          messages: [{ role: 'user', content: '计划+练习+报告' }],
          run: null, gate: null,
          closed_gates: [
            { gate_id: 'g-a', decision: 'approved', kind: 'draft', action: 'send_student',
              message: '学习计划（第 1 版）等待确认', decided_at: '2026-10-26T01:00:00' },
            { gate_id: 'g-b', decision: 'approved', kind: 'boolean', tool_name: 'assign_adaptive_practice',
              action: '', message: '此操作需要老师确认后执行', decided_at: '2026-10-26T02:00:00' },
            { gate_id: 'g-c', decision: 'approved', kind: 'draft', action: 'send_parent',
              message: '学习报告（第 1 版）等待确认', decided_at: '2026-10-26T03:00:00' },
          ],
        })
      }
      return json(reply(url))
    }))
    render(
      <ToastProvider>
        <MemoryRouter initialEntries={['/chat?thread=t-target']}>
          <Routes><Route path="/chat" element={<ChatPage />} /></Routes>
        </MemoryRouter>
      </ToastProvider>,
    )
    await screen.findByText('计划+练习+报告')
    // 三条小票各说各的：草稿门带版本标签，布尔门按工具短标签
    expect(await screen.findByText('已批准执行：学习计划（第 1 版）')).toBeInTheDocument()
    expect(screen.getByText('已批准执行：布置练习')).toBeInTheDocument()
    expect(screen.getByText('已批准执行：学习报告（第 1 版）')).toBeInTheDocument()
  })

  it('没有账本记录时不凭空造卡', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/agent/chat/history/')) {
        return json({ messages: [{ role: 'user', content: '随便聊聊' }], run: null, gate: null })
      }
      return json(reply(url))
    }))
    render(
      <ToastProvider>
        <MemoryRouter initialEntries={['/chat?thread=t-target']}>
          <Routes><Route path="/chat" element={<ChatPage />} /></Routes>
        </MemoryRouter>
      </ToastProvider>,
    )
    await screen.findByText('随便聊聊')
    expect(screen.queryByText('已批准执行')).toBeNull()
    expect(screen.queryByText('已放弃')).toBeNull()
  })
})
