/* 哑门（2026-10 第 4 轮，A 的取证）：流结束时那次 /history 重读会**整体替换** items
 * （ChatPage.loadHistory → `applyMessages(items)` + `streamRef.state = createStreamState()`）。
 * 如果那次快照是门行提交之前拍的（实测：门建好 9.01s 后页面上仍没有卡，只有 F5 才回来），
 * 它会把同一个毫秒批里 `gate_open` 刚渲染出来的卡一把抹掉，而且之后再没有补回的机会。
 *
 * 这里钉住两个方向：
 *   1) 旧快照（不含那张门）+ 事件已经到 → 卡必须还在、还能点（历史不得覆盖 live 待批卡）；
 *   2) 旧快照里那扇门**已被批过**（closed_gates 有终态）→ 卡不得又画回可点（以历史为准）。
 *
 * 回归方向：把 loadHistory 里的 keepLivePendingGates 去掉，第 1 条立刻红。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../src/pages/teacher/ChatPage'
import { ToastProvider } from '../src/components/ui'
import { __resetPendingCountForTests } from '../src/lib/use-pending-count'
import { __resetInDoubtForTests } from '../src/lib/use-in-doubt'

const enc = new TextEncoder()

/** 第 2 次及以后的 /history 响应体由用例控制（模拟"比 live 流更旧的快照"） */
let staleHistory: { gate: unknown; closed_gates?: unknown[] } | null = null
/** 挂住第 2 次 /history，直到用例放行 */
let releaseStale: (() => void) | null = null
let historyCalls = 0
/** 每次 POST 的流控制器（按调用顺序） */
let streams: ReadableStreamDefaultController<Uint8Array>[] = []

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

function sse(start: (c: ReadableStreamDefaultController<Uint8Array>) => void): Response {
  return new Response(new ReadableStream<Uint8Array>({ start }), {
    status: 200, headers: { 'Content-Type': 'text/event-stream' },
  })
}

function stubFetch() {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (init?.method === 'POST') {
      return sse((c) => { streams.push(c) })
    }
    if (url.includes('/api/agent/gates/pending')) {
      return json({ items: [], owned_total: 0, unowned_items: [], unowned_total: 0 })
    }
    if (url.includes('/api/agent/gates/in-doubt')) {
      return json({ items: [], owned_total: 0, unowned_items: [], unowned_total: 0 })
    }
    if (url.includes('/api/agent/chat/conversations')) {
      return json({ conversations: [
        { thread_id: 't-target', title: '期中成绩分析', last_at: '2026-09-25T00:00:00' },
      ] })
    }
    if (url.includes('/api/agent/chat/history/')) {
      historyCalls += 1
      const base = {
        messages: [{ role: 'user', content: '给周翔做一份学习计划' }],
        run: null,
        context_window: 128_000,
      }
      if (historyCalls === 1) return json({ ...base, gate: null })
      // 第二次（done 之后触发）挂住：等用例放行，模拟"快照比门行提交更早"
      await new Promise<void>((resolve) => { releaseStale = resolve })
      return json({ ...base, gate: staleHistory?.gate ?? null, closed_gates: staleHistory?.closed_gates ?? [] })
    }
    if (url.includes('/api/auth/me')) return json({ role: 'teacher', menus: ['chat', 'pending'] })
    return json({})
  }))
}

function mount() {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={['/chat?thread=t-target']}>
        <Routes>
          <Route path="/chat" element={<ChatPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  )
}

const emit = (c: ReadableStreamDefaultController<Uint8Array>, evt: unknown) =>
  c.enqueue(enc.encode('data: ' + JSON.stringify(evt) + '\n\n'))
const finish = (c: ReadableStreamDefaultController<Uint8Array>) => {
  c.enqueue(enc.encode('data: {"type":"done"}\n\n'))
  c.close()
}

/** 起一轮流（第 n 次 POST） */
async function startTurn(text: string, index: number) {
  const box = await screen.findByPlaceholderText(/输入化学教学问题/)
  await waitFor(() => expect(box).not.toBeDisabled(), { timeout: 5000 })
  fireEvent.change(box, { target: { value: text } })
  fireEvent.keyDown(box, { key: 'Enter' })
  await waitFor(() => expect(streams.length).toBeGreaterThan(index), { timeout: 5000 })
  return streams[index]
}

beforeEach(() => {
  Element.prototype.scrollIntoView = () => {}
  staleHistory = null
  releaseStale = null
  historyCalls = 0
  streams = []
  __resetPendingCountForTests()
  __resetInDoubtForTests()
  localStorage.clear()
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('流结束那次历史重读，不得覆盖 live 流刚渲染的待批卡', () => {
  it('旧快照里没有这扇门，但 gate_open 已经到了 → 卡必须还在、还能点', async () => {
    stubFetch()
    mount()
    await screen.findByText('给周翔做一份学习计划')

    // 第 1 轮：正常结束 → onDone 触发第 2 次 /history（这次会被挂住）
    const s1 = await startTurn('先看一下学情', 0)
    emit(s1, { type: 'text', content: '好的。' })
    finish(s1)
    await waitFor(() => expect(historyCalls).toBeGreaterThanOrEqual(2), { timeout: 5000 })

    // 第 2 轮：门在这一轮里开出来（事件到得比那份旧快照还晚，但快照随后才回来）
    const s2 = await startTurn('给周翔做一份学习计划', 1)
    emit(s2, { type: 'gate_open', gate_id: 'g-1', draft_id: 'd-1', version: 1, message: '学习计划等待确认' })
    expect(await screen.findByRole('button', { name: '批准' }, { timeout: 5000 })).toBeInTheDocument()

    // 放行那份**不含门**的旧快照
    await waitFor(() => expect(releaseStale).not.toBeNull(), { timeout: 5000 })
    releaseStale!()

    // 卡不能被抹掉，而且要还能点
    await waitFor(
      () => expect(screen.getByRole('button', { name: '批准' })).toBeInTheDocument(),
      { timeout: 5000 },
    )
    expect(screen.getByText('学习计划等待确认')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '批准' })).toBeEnabled()
  }, 40_000)

  it('旧快照里这扇门已经被批过（closed_gates 有终态）→ 不许又画回可点，以历史为准', async () => {
    staleHistory = {
      gate: null,
      closed_gates: [{ gate_id: 'g-1', decision: 'approved', message: '学习计划等待确认' }],
    }
    stubFetch()
    mount()
    await screen.findByText('给周翔做一份学习计划')

    const s1 = await startTurn('先看一下学情', 0)
    finish(s1)
    await waitFor(() => expect(historyCalls).toBeGreaterThanOrEqual(2), { timeout: 5000 })

    const s2 = await startTurn('给周翔做一份学习计划', 1)
    emit(s2, { type: 'gate_open', gate_id: 'g-1', draft_id: 'd-1', version: 1, message: '学习计划等待确认' })
    await screen.findByRole('button', { name: '批准' }, { timeout: 5000 })

    await waitFor(() => expect(releaseStale).not.toBeNull(), { timeout: 5000 })
    releaseStale!()

    // 历史说这扇门已经批过了：屏幕上要变成终态卡，不能再给一个可点的待批卡
    await waitFor(() => expect(screen.queryByRole('button', { name: '批准' })).toBeNull(), { timeout: 5000 })
    expect(screen.getByText('已批准执行：学习计划')).toBeInTheDocument()
  }, 40_000)
})
