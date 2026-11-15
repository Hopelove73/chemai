/* T21：聊天页接收「指定会话」入口（?thread=）——待确认队列的行跳转靠它落地。
 *
 * 三件事各一条断言：
 *   1. 带 `?thread=` 进来时选的是那一行所属会话，**不是**列表第一条；
 *   2. 目标会话取不到（403 归属被拒 / 404 已删）时落人话空态，不悄悄换成别的对话；
 *   3. 会话与地址栏同步：切会话会改写 URL，刷新后还在同一条。
 * 真渲染（jsdom + fetch 桩）而非源码文本断言：这三条都是"少了没人报、错了老师遭殃"的行为。
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation, useSearchParams } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ChatPage } from '../src/pages/teacher/ChatPage'
import { ToastProvider } from '../src/components/ui'
import { __resetPendingCountForTests, peekPendingStore } from '../src/lib/use-pending-count'
import { __resetInDoubtForTests } from '../src/lib/use-in-doubt'

const CALLS: string[] = []

/** 历史接口下发的门（默认无门）。三条门恢复用例只换这一个变量。 */
let HISTORY_GATE: unknown = null
/** 历史接口下发的上下文窗口。圆环只认这个数，前端不再有默认值（ADR-0023 Q14）。 */
let HISTORY_WINDOW: number | null = 128_000

function reply(url: string) {
  if (url.includes('/api/agent/gates/pending')) {
    return {
      items: [
        { gate_id: 'g-1', conversation_id: 't-first', kind: 'boolean', action: '',
          action_label: '', subject_label: '执行命令 · 批卷脚本', message: '',
          last_opened_at: '2026-09-25T00:00:00' },
        { gate_id: 'g-2', conversation_id: 't-target', kind: 'boolean', action: '',
          action_label: '', subject_label: '读取文件 · 成绩表.csv', message: '',
          last_opened_at: '2026-09-25T01:00:00' },
      ],
      owned_total: 2, unowned_items: [], unowned_total: 0,
    }
  }
  if (url.includes('/api/agent/chat/conversations')) {
    return { conversations: [
      { thread_id: 't-first', title: '列表第一条', last_at: '2026-09-25T00:00:00' },
      { thread_id: 't-target', title: '期中成绩分析', last_at: '2026-09-24T00:00:00' },
    ] }
  }
  if (url.includes('/api/agent/chat/history/')) {
    return {
      messages: [{ role: 'user', content: '那份报告发出去了吗' }],
      run: null, gate: HISTORY_GATE,
      ...(HISTORY_WINDOW === null ? {} : { context_window: HISTORY_WINDOW }),
    }
  }
  if (url.includes('/api/auth/me')) {
    return { role: 'teacher', menus: ['chat', 'pending'] }
  }
  return {}
}

/** 把当前地址查询串显式渲染出来：MemoryRouter 不写 window.location，
 *  「改写地址栏」这件事只能这样验。 */
function LocationProbe() {
  const [params] = useSearchParams()
  return <div data-testid="location-probe">{`?${params.toString()}`}</div>
}

/** 队列页占位：把跳转时带的 `justApproved` 原样露出来，验批准后的回程。 */
function PendingProbe() {
  const location = useLocation()
  const just = (location.state as { justApproved?: string } | null)?.justApproved ?? ''
  return <><div>队列页占位</div><div data-testid="approved-probe">{just}</div></>
}

function mount(url: string) {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/chat" element={<><ChatPage /><LocationProbe /></>} />
          <Route path="/pending" element={<PendingProbe />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  )
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })
}

/** 一份只改 `/gates/pending` 的桩；其余接口沿用上面的 `reply()`。 */
function stubPending(payload: unknown) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    CALLS.push(url)
    if (url.includes('/api/agent/gates/pending')) return json(payload)
    return json(reply(url))
  }))
}

beforeEach(() => {
  // jsdom 没有 scrollIntoView（MessageList 自动滚动要用）
  Element.prototype.scrollIntoView = () => {}
  CALLS.length = 0
  HISTORY_GATE = null
  HISTORY_WINDOW = 128_000
  __resetPendingCountForTests()
  __resetInDoubtForTests()
  localStorage.clear()
  sessionStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    CALLS.push(url)
    return new Response(JSON.stringify(reply(url)), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }))
})

describe('聊天页的 ?thread= 入口', () => {
  it('带参数进来选的是那条会话，不是列表第一条', async () => {
    mount('/chat?thread=t-target')

    await waitFor(() => {
      expect(CALLS.some((u) => u.includes('/api/agent/chat/history/t-target'))).toBe(true)
    })
    expect(CALLS.some((u) => u.includes('/api/agent/chat/history/t-first'))).toBe(false)
  })

  it('不带参数时仍走原有的自动选第一条', async () => {
    mount('/chat')

    await waitFor(() => {
      expect(CALLS.some((u) => u.includes('/api/agent/chat/history/t-first'))).toBe(true)
    })
  })

  it('目标会话取不到时给人话空态，不换会话', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      CALLS.push(url)
      if (url.includes('/api/agent/chat/history/')) {
        return new Response(JSON.stringify({ detail: '无权访问该会话' }), { status: 403 })
      }
      return new Response(JSON.stringify(reply(url)), { status: 200 })
    }))

    mount('/chat?thread=t-deleted')

    expect(await screen.findByRole('status')).toHaveTextContent('这条会话已经不在了')
    // 没有偷偷换成别人的：一条额外的历史都没拉
    expect(CALLS.filter((u) => u.includes('/api/agent/chat/history/'))).toHaveLength(1)
  })

  it('历史是 200 空数组、且这条不在会话列表里，也算「不在了」', async () => {
    // 后端对不存在的 `t-` 会话返回 200 空历史（`require_owned` 的第三条会当场认领），
    // 所以"取不到"不能只看状态码 —— 这一条就是照那个真实形状写的。
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      CALLS.push(url)
      if (url.includes('/api/agent/chat/conversations')) {
        return new Response(JSON.stringify({ conversations: [] }), { status: 200 })
      }
      return new Response(JSON.stringify({ messages: [], run: null, gate: null }), { status: 200 })
    }))

    mount('/chat?thread=t-never-existed')

    expect(await screen.findByRole('status')).toHaveTextContent('这条会话已经不在了')
  })

  it('点侧栏另一条会话会把地址栏改写过去', async () => {
    mount('/chat?thread=t-first')
    const row = await screen.findByText('期中成绩分析')
    row.click()

    await waitFor(() => {
      expect(CALLS.some((u) => u.includes('/api/agent/chat/history/t-target'))).toBe(true)
    })
    // 地址栏也跟着走 —— 刷新要能回到同一条对话
    await waitFor(() => {
      expect(screen.getByTestId('location-probe').textContent).toBe('?thread=t-target')
    })
  })
})

describe('工作台顶栏的待确认入口（本轮 D2：入口从侧栏搬进工作台）', () => {
  it('按钮念得出数量，点下去进的是队列页', async () => {
    mount('/chat')

    const btn = await screen.findByRole('button', { name: '待确认，2 项待处理' })
    expect(within(btn).getByText('待确认')).toBeInTheDocument()
    btn.click()
    expect(await screen.findByText('队列页占位')).toBeInTheDocument()
  })

  it('进工作台时会重拉一次计数（重拉点跟着入口从侧栏搬到这里）', async () => {
    mount('/chat')

    await waitFor(() => {
      expect(CALLS.some((u) => u.includes('/api/agent/gates/pending'))).toBe(true)
    })
  })

  it('0 时不涂数字：可及名只说「待确认」', async () => {
    stubPending({ items: [], owned_total: 0, unowned_items: [], unowned_total: 0 })
    mount('/chat')

    await waitFor(() => expect(peekPendingStore().loaded).toBe(true))
    const btn = screen.getByRole('button', { name: '待确认' })
    expect(within(btn).queryByText(/^\d/)).toBeNull()
  })

  it('≥100 时显示 99+，可及名念「99 项以上」', async () => {
    stubPending({ items: [], owned_total: 250, unowned_items: [], unowned_total: 0 })
    mount('/chat')

    const btn = await screen.findByRole('button', { name: '待确认，99 项以上待处理' })
    expect(within(btn).getByText('99+')).toBeInTheDocument()
  })
})

describe('批准后的回程（R19 第②点，2026-10 改为留在对话）', () => {
  /* 原契约：批准后立刻 navigate('/pending', {justApproved})，把「我做成了什么」交给
     队列那一行补回来。实际体验是反的——在对话里点确认却被弹出对话，既看不到执行
     结果，也接不上刚说的话；正常流向是「队列点开某行 → 进对话」，不是反过来。
     新契约：决定照旧带 gate_id / 乐观减一 / 流读完强制重拉，但不跳走。 */
  it('决定带 gate_id、乐观减一、留在对话，流读完再强制重拉', async () => {
    HISTORY_GATE = {
      draft_id: 'd-1', student_id: 's-1', version: 1, action: 'send_student',
      message: '计划等待确认', gate_id: 'g-draft-d-1', kind: 'draft',
    }
    const posts: { url: string; body: Record<string, unknown> }[] = []
    let pendingGets = 0
    const encoder = new TextEncoder()
    /* 流先挂住：让「乐观减一 / 跳回队列」发生在流读完之前，重拉之后再验
       —— 三者的顺序正是这段回程要钉的东西。 */
    let releaseStream!: () => void
    const streamGate = new Promise<void>((resolve) => { releaseStream = resolve })
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      CALLS.push(url)
      if (init?.method === 'POST') {
        posts.push({ url, body: JSON.parse(String(init.body ?? '{}')) })
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            streamGate.then(() => {
              c.enqueue(encoder.encode('data: {"type":"done"}\n\n'))
              c.close()
            })
          },
        })
        return new Response(stream, {
          status: 200, headers: { 'Content-Type': 'text/event-stream' },
        })
      }
      if (url.includes('/api/agent/gates/pending')) {
        pendingGets += 1
        return json({
          items: [{ gate_id: 'g-draft-d-1', conversation_id: 't-target', kind: 'draft',
                    action: 'send_student', tool_name: '', action_label: '发送给学生',
                    subject_label: '张三', message: '计划等待确认',
                    last_opened_at: '2026-09-25T00:00:00' }],
          owned_total: 1, unowned_items: [], unowned_total: 0,
        })
      }
      return json(reply(url))
    }))

    mount('/chat?thread=t-target')
    await waitFor(() =>
      expect(peekPendingStore().snapshot.items.some((i) => i.gate_id === 'g-draft-d-1'))
        .toBe(true))

    fireEvent.click(await screen.findByRole('button', { name: '发送给学生' }))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0].url).toContain('/api/agent/chat/langgraph/resume')
    expect(posts[0].body.decision).toMatchObject({
      action: 'approve', draft_id: 'd-1', gate_id: 'g-draft-d-1',
    })
    // 不再跳队列：仍停在对话页（队列占位不该被挂出来）
    expect(screen.queryByTestId('approved-probe')).toBeNull()
    expect(screen.getByTestId('location-probe')).toBeInTheDocument()
    // 同一帧先乐观减一（流读完后的重拉会以服务端为准，另行断言）
    expect(peekPendingStore().snapshot.owned_total).toBe(0)
    expect(pendingGets).toBe(1)
    releaseStream()
    // 流读完后 force 重拉：决定前那一发反映的还是旧状态，不能合并进去
    await waitFor(() => expect(pendingGets).toBeGreaterThanOrEqual(2))
  })
})

describe('刷新后确认卡的恢复（浏览器实测抓到的死路）', () => {
  it('重新打开已完成会话：规划工具卡从 run 快照恢复逐步状态', async () => {
    const goal = '查看学情并布置练习'
    const steps = [
      { step: 1, skill: 'diagnose_barrier', description: '查看学情', status: 'completed' },
      { step: 2, skill: 'assign_adaptive_practice', description: '布置练习', status: 'cancelled' },
    ]
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (url.includes('/api/agent/chat/history/')) return json({
        messages: [{ type: 'tool', name: 'plan_task', status: 'done', data: {
          goal, steps: steps.map((s) => ({ ...s, status: 'pending' })),
        } }],
        run: { run_id: 'run-done', goal, status: 'completed', steps }, gate: null,
      })
      return json(reply(url))
    }))
    mount('/chat?thread=t-target')

    // 「任务规划」工具卡已被 MessageList 刻意去掉（计划卡接管同一份数据），
    // 计划卡没有可点的头、步骤行常驻 —— 按步骤行直接读状态。
    await screen.findByText(goal)
    const rows = Array.from(document.querySelectorAll('div')).filter((el) => {
      const c = (el as HTMLElement).className
      return typeof c === 'string' && c.includes('items-center')
        && c.includes('gap-2.5') && c.includes('text-sub')
    })
    expect(rows[0]).toHaveTextContent(/✓.*完成/)
    expect(rows[1]).toHaveTextContent(/⊘.*已放弃/)
  })

  it('布尔门只有 gate_id、没有 draft_id，也必须恢复出可决定的卡', async () => {
    // `/history` 对布尔门下发的就是这种形状（draft_id 空串、version 0）。
    // 原实现只看 draft_id，于是队列里点「打开 →」跳进来什么控件都没有，
    // 那扇门只能永远开着。
    HISTORY_GATE = {
      draft_id: '', student_id: '', version: 0, action: '',
      message: '批准后执行批卷脚本', gate_id: 'g-tool-bash-1e98fa8c', kind: 'boolean',
    }
    mount('/chat?thread=t-target')

    expect(await screen.findByText('批准后执行批卷脚本')).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '批准' })).toBeInTheDocument()
    // 布尔门没有"修订"这回事：打回按钮不该出现
    expect(screen.queryByRole('button', { name: '打回修改' })).not.toBeInTheDocument()
  })

  it('草稿门照旧：版本号与打回入口都在', async () => {
    HISTORY_GATE = {
      draft_id: 'd-1', student_id: 's-1', version: 2, action: 'send_parent',
      message: '报告等待确认', gate_id: 'g-draft-d-1', kind: 'draft',
    }
    mount('/chat?thread=t-target')

    expect(await screen.findByText(/需要确认 · 第 2 版/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '同步给家长' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打回修改' })).toBeInTheDocument()
  })

  it('两种身份都没有时不堆一张点不动的假卡', async () => {
    HISTORY_GATE = { draft_id: '', version: 0, action: '', message: '脏数据', gate_id: '' }
    mount('/chat?thread=t-target')

    await waitFor(() => {
      expect(CALLS.some((u) => u.includes('/api/agent/chat/history/t-target'))).toBe(true)
    })
    expect(screen.queryByText('脏数据')).not.toBeInTheDocument()
  })
})

describe('上下文圆环的窗口来自后端（ADR-0023 Q14）', () => {
  it('圆环上的总量是历史接口下发的窗口，不是前端写的数', async () => {
    // 改前端那个常量以前是"改一次 CONTEXT_WINDOW，圆环安静地错一半"的唯一藏身处。
    // 这里下发一个绝不可能被写死的数，看它是否原样出现在提示里。
    HISTORY_WINDOW = 64_000
    mount('/chat')

    const ring = await screen.findByRole('button', { name: /上下文已用/ })
    expect(ring.getAttribute('title')).toContain('/ 64K')
  })

  it('按「回到我的对话」离开时圆环跟着清掉，不留刚才那条会话的数', async () => {
    // 那条按钮只清了消息、没清 ctxInfo：离开一条已经画了占用百分比的会话后，
    // 圆环还挂着上一条的数 —— 老师会照着它决定要不要开新对话。
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      CALLS.push(url)
      if (url.includes('/api/agent/chat/conversations')) {
        return new Response(JSON.stringify({ conversations: [] }), { status: 200 })
      }
      if (url.includes('/api/auth/me')) {
        return new Response(JSON.stringify({ role: 'teacher', menus: ['chat', 'pending'] }),
                            { status: 200 })
      }
      // 空历史 + 本人列表里没有这条 → 页面进「不在了」态；窗口照样下发，圆环照样画出来
      return new Response(JSON.stringify({
        messages: [], run: null, gate: null, context_window: 64_000,
      }), { status: 200 })
    }))

    mount('/chat?thread=t-gone')
    const ring = await screen.findByRole('button', { name: /上下文已用/ })
    expect(ring.getAttribute('title')).toContain('/ 64K')

    fireEvent.click(screen.getByRole('button', { name: '回到我的对话' }))
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /上下文已用/ })).toBeNull()
    })
  })

  it('后端没下发窗口就宁可不显示圆环', async () => {
    // 缺字段（老后端 / 异常响应）时按猜的窗口画环，比不画更糟：那也是个百分比。
    // 这里用 null 表示"不下发这个键"，比塞一个 0 更接近真实缺字段的响应形状。
    HISTORY_WINDOW = null
    mount('/chat')

    await waitFor(() => {
      expect(CALLS.some((u) => u.includes('/api/agent/chat/history/'))).toBe(true)
    })
    expect(screen.queryByRole('button', { name: /上下文已用/ })).toBeNull()
  })
})
