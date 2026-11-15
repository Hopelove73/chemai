/* 流式期间的确认门决定不能丢（2026-10 实测现场）。
 *
 * 现象：门在流没结束时就渲染成可点，老师点「批准」——handleDecision 里
 * `if (streamingRef.current) { toast.info(); return }` 把点击**丢掉**：后端 0 条
 * /resume，老师以为点了没反应，这一轮后续步骤被标 failed、门继续 open。
 *
 * 钉住的不变式（方案：排队执行，不禁用按钮 —— 2026-10 那次死锁的教训是
 * 「按钮一灰，门就永远关不掉」，所以这里一个按钮都不许锁）：
 *   1. 流式中点下的决定立刻在卡片上留下**看得见**的回执「已记录「X」：本轮结束后自动确认」；
 *   2. 那一刻**不发**请求；
 *   3. 流结束（done / 出错 / 断连三条收尾路径都 setStreaming(false)）后**恰好一次**
 *      /resume，payload 就是卡片上写着的那一条；
 *   4. 按钮始终可点（自管的 1.2s 在途锁放开后立刻回来）；
 *   5. 非流式时行为不变：点了立刻发。
 *
 * 回归方向：把 handleDecision 的队列分支改回 `return`，第 1/2 条立刻红；
 * 把 flush 里的 `queuedDecisionRef.current = null` 去掉，第 3 条会变成两次 /resume。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../src/pages/teacher/ChatPage'
import { ToastProvider } from '../src/components/ui'
import { __resetPendingCountForTests } from '../src/lib/use-pending-count'
import { __resetInDoubtForTests } from '../src/lib/use-in-doubt'

/** 卡片上的回执原文（data-gate-queued） */
const queuedText = (label: string) => `已记录「${label}」：本轮结束后自动确认`
const enc = new TextEncoder()

/** 历史接口下发的门（首屏就有一张待批卡时用） */
let HISTORY_GATE: unknown = null
/** 主轮流的控制器：用例自己决定这一轮什么时候结束 */
let streamCtl: ReadableStreamDefaultController<Uint8Array> | null = null
/** 每次 POST /resume 的 body（按顺序） */
let resumeBodies: string[] = []

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

function sseResponse(start: (c: ReadableStreamDefaultController<Uint8Array>) => void): Response {
  return new Response(new ReadableStream<Uint8Array>({ start }), {
    status: 200, headers: { 'Content-Type': 'text/event-stream' },
  })
}

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

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST') {
      if (url.includes('/resume')) {
        resumeBodies.push(String(init.body || ''))
        // 决定发出去之后的续接流：立刻收尾，别把测试挂在读流上
        return sseResponse((c) => {
          c.enqueue(enc.encode('data: {"type":"done"}\n\n'))
          c.close()
        })
      }
      // 主轮：流**一直开着**，直到用例调用 finish()
      return sseResponse((c) => { streamCtl = c })
    }
    return json(reply(url))
  }))
}

function mount(url = '/chat?thread=t-target') {
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

function emit(evt: unknown) {
  if (!streamCtl) throw new Error('主轮流还没开')
  streamCtl.enqueue(enc.encode('data: ' + JSON.stringify(evt) + '\n\n'))
}

function finish() {
  if (!streamCtl) throw new Error('主轮流还没开')
  streamCtl.enqueue(enc.encode('data: {"type":"done"}\n\n'))
  streamCtl.close()
}

/** 起一轮流，并在流里开一扇门（真实形态：门是这一轮里开的，不是首屏历史里就有的） */
async function startTurnWithGate() {
  const box = await screen.findByPlaceholderText(/输入化学教学问题/)
  fireEvent.change(box, { target: { value: '给周翔做一份学习计划' } })
  fireEvent.keyDown(box, { key: 'Enter' })
  await waitFor(() => expect(streamCtl).not.toBeNull(), { timeout: 5000 })
  emit({
    type: 'gate_open', gate_id: 'g-1', draft_id: 'd-1', version: 1,
    message: '学习计划等待确认',
  })
  return await screen.findByRole('button', { name: '批准' }, { timeout: 5000 })
}

beforeEach(() => {
  Element.prototype.scrollIntoView = () => {}
  HISTORY_GATE = null
  streamCtl = null
  resumeBodies = []
  __resetPendingCountForTests()
  __resetInDoubtForTests()
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('流式期间的门决定：排队执行，不丢弃也不禁用', () => {
  it('点「批准」：卡片立刻写「已记录「批准」」，那一刻不发请求；流结束后恰好一次 /resume', async () => {
    stubFetch()
    mount()
    await screen.findByText('给周翔做一份学习计划')
    const approve = await startTurnWithGate()

    fireEvent.click(approve)

    // 1) 看得见的回执（不是 toast）
    expect(await screen.findByText(queuedText('批准'), undefined, { timeout: 5000 })).toBeInTheDocument()
    // 2) 那一刻没有发请求
    expect(resumeBodies).toHaveLength(0)

    // 4) 按钮没被锁死：自管的 1.2s 在途锁放开后立刻可点（门永远有一条能关的路）
    await waitFor(
      () => expect(screen.getByRole('button', { name: '批准' })).toBeEnabled(),
      { timeout: 5000 },
    )

    // 3) 流结束 → 自动发出去，payload 就是卡片上写着的那一条，且只发一次
    finish()
    await waitFor(() => expect(resumeBodies).toHaveLength(1), { timeout: 5000 })
    expect(JSON.parse(resumeBodies[0]).decision).toMatchObject({
      action: 'approve', draft_id: 'd-1', gate_id: 'g-1',
    })
    // 再多等一拍：不得冒出第二次
    await new Promise((r) => setTimeout(r, 300))
    expect(resumeBodies).toHaveLength(1)
  }, 30_000)

  it('流式中连点 3 次只留一条；之后再换「放弃」→ 回执跟着换，最终只发一次', async () => {
    stubFetch()
    mount()
    await screen.findByText('给周翔做一份学习计划')
    const approve = await startTurnWithGate()

    fireEvent.click(approve)
    fireEvent.click(approve)
    fireEvent.click(approve)
    await screen.findByText(queuedText('批准'), undefined, { timeout: 5000 })
    expect(resumeBodies).toHaveLength(0)

    // 1.2s 自管在途锁放开后换一个决定：队列只留一条，卡片文字必须跟着换
    await waitFor(
      () => expect(screen.getByRole('button', { name: '批准' })).toBeEnabled(),
      { timeout: 5000 },
    )
    fireEvent.click(screen.getByRole('button', { name: /放弃/ }))

    // 连点 3 次 + 换一次决定，最终只发一条，而且是卡片最后写着的那条
    await waitFor(() => expect(screen.getByText(queuedText('放弃'))).toBeInTheDocument(), { timeout: 5000 })
    finish()
    await waitFor(() => expect(resumeBodies).toHaveLength(1), { timeout: 5000 })
    expect(JSON.parse(resumeBodies[0]).decision.action).toBe('abandon')
  }, 30_000)

  it('「放弃」在流式中同样可点，同样会被排队执行', async () => {
    stubFetch()
    mount()
    await screen.findByText('给周翔做一份学习计划')
    await startTurnWithGate()

    const abandon = screen.getByRole('button', { name: /放弃/ })
    expect(abandon).toBeEnabled()
    fireEvent.click(abandon)

    expect(await screen.findByText(queuedText('放弃'), undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(resumeBodies).toHaveLength(0)

    finish()
    await waitFor(() => expect(resumeBodies).toHaveLength(1), { timeout: 5000 })
    expect(JSON.parse(resumeBodies[0]).decision.action).toBe('abandon')
  }, 30_000)

  it('两扇门并开：批准其中一张不会把另一张做成终态（哑门），刷新也不需要', async () => {
    stubFetch()
    mount()
    await screen.findByText('给周翔做一份学习计划')
    const box = await screen.findByPlaceholderText(/输入化学教学问题/)
    fireEvent.change(box, { target: { value: '给周翔做一份学习计划' } })
    fireEvent.keyDown(box, { key: 'Enter' })
    await waitFor(() => expect(streamCtl).not.toBeNull(), { timeout: 5000 })
    emit({ type: 'gate_open', gate_id: 'g-1', draft_id: 'd-1', version: 1, message: '学习计划等待确认' })
    emit({ type: 'gate_open', gate_id: 'g-2', draft_id: 'd-2', version: 1, message: '家长报告等待确认' })

    // 两张卡都在（同一轮里两扇门并开）
    await waitFor(() => expect(screen.getAllByRole('button', { name: '批准' })).toHaveLength(2), { timeout: 5000 })

    fireEvent.click(screen.getAllByRole('button', { name: '批准' })[0])

    // 回执只挂在被点的那一张上
    expect(await screen.findByText(queuedText('批准'), undefined, { timeout: 5000 })).toBeInTheDocument()
    expect(screen.getAllByText(queuedText('批准'))).toHaveLength(1)
    // 两张都还能按（谁都没被禁用）
    expect(screen.getAllByRole('button', { name: '批准' })).toHaveLength(2)

    finish()
    await waitFor(() => expect(resumeBodies).toHaveLength(1), { timeout: 5000 })
    expect(JSON.parse(resumeBodies[0]).decision.gate_id).toBe('g-1')

    // 关键：另一扇门不得被顺手写成终态 —— 它必须还是可点的待批卡
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: '批准' })).toHaveLength(1)
    }, { timeout: 5000 })
    // 且留下的那张不是「已批准执行」的终态壳（旧实现就是这样把门做成哑门的）
    const stillPending = screen.getByText('家长报告等待确认')
    expect(stillPending).toBeInTheDocument()
  }, 30_000)

  it('非流式时行为不变：点了立刻发，不出现回执', async () => {
    // 首屏历史里就有一扇待批的门，此时没有流在跑
    HISTORY_GATE = {
      draft_id: 'd-1', version: 1, message: '学习计划等待确认', gate_id: 'g-1',
    }
    stubFetch()
    mount()
    const approve = await screen.findByRole('button', { name: '批准' }, { timeout: 5000 })
    expect(screen.queryByText(queuedText('批准'))).toBeNull()

    fireEvent.click(approve)
    await waitFor(() => expect(resumeBodies).toHaveLength(1), { timeout: 5000 })
    expect(JSON.parse(resumeBodies[0]).decision).toMatchObject({
      action: 'approve', draft_id: 'd-1', gate_id: 'g-1',
    })
    expect(screen.queryByText(queuedText('批准'))).toBeNull()
  }, 20_000)
})
