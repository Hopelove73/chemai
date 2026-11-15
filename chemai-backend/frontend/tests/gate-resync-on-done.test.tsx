/* 门视图契约 T3：一轮**正常**结束后，未决门必须重新出现在页面上。
 *
 * 钉住的不变式（契约 D4/D7 的可执行形态）：
 *   流结束那一刻，前端手里的未决门集合 == 库里 status='open' 的那些门。
 *
 * 为什么必须真渲染：这条测试的失败形态就是「老师屏幕上的卡不见了」，
 * 源码文本断言永远看不出来。桩里刻意**不发** gate_open 事件——门只存在于
 * 历史接口的下发里，模拟的正是 A5 现场（事件丢了 / 门在上一轮就开着）。
 *
 * 回归方向：把 ChatPage 的 onDone 重读历史删掉，这条立刻红。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../src/pages/teacher/ChatPage'
import { ToastProvider } from '../src/components/ui'
import { __resetPendingCountForTests } from '../src/lib/use-pending-count'
import { __resetInDoubtForTests } from '../src/lib/use-in-doubt'

/** 历史接口下发的门。流开始后由用例置上，模拟「门是这一轮里开的」。 */
let HISTORY_GATE: unknown = null

function reply(url: string) {
  if (url.includes('/api/agent/gates/pending')) {
    return { items: [], owned_total: 0, unowned_items: [], unowned_total: 0 }
  }
  if (url.includes('/api/agent/gates/in-doubt')) {
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
      gate: HISTORY_GATE,
      context_window: 128_000,
    }
  }
  if (url.includes('/api/auth/me')) {
    return { role: 'teacher', menus: ['chat', 'pending'] }
  }
  return {}
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

function mount(url: string) {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  )
}

beforeEach(() => {
  // jsdom 没有 scrollIntoView（MessageList 自动滚动要用）
  Element.prototype.scrollIntoView = () => {}
  HISTORY_GATE = null
  __resetPendingCountForTests()
  __resetInDoubtForTests()
  localStorage.clear()
  sessionStorage.clear()
})

describe('一轮正常结束后的门重同步', () => {
  it('流里没有 gate_open 事件，门也必须从历史投影回到页面', async () => {
    const encoder = new TextEncoder()
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST') {
        /* 刻意只发文本 + done：**不发 gate_open**。
           真实形态就是这条事件在断流/重连里丢了，或者门根本不是这一轮开的。 */
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(encoder.encode('data: {"type":"text","content":"好的，我来安排。"}\n\n'))
            c.enqueue(encoder.encode('data: {"type":"done"}\n\n'))
            c.close()
          },
        })
        return new Response(stream, {
          status: 200, headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      return json(reply(url))
    }))

    mount('/chat?thread=t-target')
    // 首屏历史已到（此时库里还没有门）
    await screen.findByText('给周翔做一份学习计划')
    expect(screen.queryByText(/需要确认/)).toBeNull()

    // 门在「这一轮」里开出：只写进库，不进流
    HISTORY_GATE = {
      draft_id: 'd-1', version: 1, action: 'send_student',
      message: '学习计划等待确认', gate_id: 'g-draft-d-1',
    }

    const box = screen.getByPlaceholderText(/输入化学教学问题/)
    fireEvent.change(box, { target: { value: '给周翔做一份学习计划' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    // 流读完 → onDone 重读历史 → 卡回来
    expect(await screen.findByText(/需要确认/, undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getByText('学习计划等待确认')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '发送给学生' })).toBeInTheDocument()
    // jsdom 全量并发下这个页面实测要 5s+，给显式预算（放宽的是环境容忍度，不是断言）
  }, 20_000)

  it('库里没有门时，正常结束不凭空造出一张卡', async () => {
    const encoder = new TextEncoder()
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (init?.method === 'POST') {
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            c.enqueue(encoder.encode('data: {"type":"done"}\n\n'))
            c.close()
          },
        })
        return new Response(stream, {
          status: 200, headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      return json(reply(url))
    }))

    mount('/chat?thread=t-target')
    await screen.findByText('给周翔做一份学习计划')

    const box = screen.getByPlaceholderText(/输入化学教学问题/)
    fireEvent.change(box, { target: { value: '在吗' } })
    fireEvent.keyDown(box, { key: 'Enter' })

    // 等流跑完（输入框随 streaming 解锁），确认没有多出确认卡。
    // 这条防的是「矫枉过正」：把卡无条件画出来。
    await waitFor(() => expect(box).not.toBeDisabled(), { timeout: 5000 })
    expect(screen.queryByText(/需要确认/)).toBeNull()
  }, 20_000)
})
