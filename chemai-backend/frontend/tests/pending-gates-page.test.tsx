/* 待确认页与入口（T9 / R19 / R27）—— 队列口径与注册一起钉。
 *
 * 覆盖面说清楚：
 *   · 页面是**真渲染**（jsdom），计数、尾行、折叠段、行首真链接、三态都验行为；
 *   · 路由注册读配置对象（`ROUTE_MENU` / `MENU_REGISTRY`），每个菜单 key 都有
 *     对应路由的交叉校验在 `menu-capabilities.test.ts`；守卫的 JSX 在 App.tsx
 *     内部（`Shell` 未导出），渲染整棵 App 的代价与它证明的东西不成比例；
 *   · 顶栏胶囊与「批准后减一/跳回队列」的行为在 `chat-thread-param.test.tsx`
 *     里真挂 ChatPage + 喂 SSE 流验证（含 0 / ≥100 两种口径与 approve 回程）。
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MENU_REGISTRY, NAV_OWNER, ROUTE_MENU, navItemsFor } from '../src/App'
import { PendingGatesPage, humanWait } from '../src/pages/teacher/PendingGatesPage'
import { ToastProvider } from '../src/components/ui'
import {
  __resetPendingCountForTests,
  decrementPendingCount,
  peekPendingStore,
  refetchPendingCount,
} from '../src/lib/use-pending-count'
import { __resetInDoubtForTests } from '../src/lib/use-in-doubt'

const COUNT_CALLS: string[] = []
/** 本页发出的 POST（`{url, body}`）—— 「结果未知」段那两个动作全走它。 */
const POSTS: { url: string; body: string }[] = []

function gate(i: number, kind: 'draft' | 'boolean' = 'draft') {
  const opened = new Date(Date.UTC(2026, 8, 20, 0, 0, 0) + i * 3600_000).toISOString().slice(0, 19)
  return {
    gate_id: `g${i}`,
    conversation_id: `t-${i}`,
    kind,
    action: kind === 'draft' ? 'send_parent' : '',
    tool_name: kind === 'draft' ? '' : 'bash',
    action_label: kind === 'draft' ? '发送给家长' : '',
    subject_label: kind === 'draft' ? `王梓涵(高一(${i % 9 + 1})班)` : '执行命令 · 批卷脚本',
    message: '方案待发',
    last_opened_at: opened,
  }
}

function stub(
  payload: { items: unknown[]; owned_total: number; unowned_items: unknown[]; unowned_total: number },
  titleOf: (id: string) => string = (id) => `会话 ${id}`,
  /* 「结果未知」那一份。默认空 —— 老用例照旧走「这一段不出现」的分支。 */
  inDoubt: unknown[] = [],
) {
  COUNT_CALLS.length = 0
  POSTS.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    COUNT_CALLS.push(url)
    if (init?.method === 'POST') POSTS.push({ url, body: String(init.body ?? '') })
    if (url.includes('/api/agent/gates/pending')) {
      return new Response(JSON.stringify(payload), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.includes('/api/agent/gates/in-doubt')) {
      return new Response(JSON.stringify(
        { items: inDoubt, owned_total: inDoubt.length,
          unowned_items: [], unowned_total: 0 },
      ), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.includes('/api/agent/chat/conversations')) {
      const conversations = payload.items.concat(payload.unowned_items as never[]).map(
        (raw) => {
          const r = raw as { conversation_id: string }
          return { thread_id: r.conversation_id, title: titleOf(r.conversation_id) }
        },
      )
      return new Response(JSON.stringify({ conversations }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } })
  }))
}

function mount() {
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[{ pathname: '/pending' }]}>
        <PendingGatesPage />
      </MemoryRouter>
    </ToastProvider>,
  )
}

beforeEach(() => {
  __resetPendingCountForTests()
  /* 「结果未知」那份也是模块级 store，不复位会串味：头一条用例把它标成 loaded，
     后面的用例就再也不会去拉，于是 stub 里的数据永远进不来。 */
  __resetInDoubtForTests()
  localStorage.clear()
  Element.prototype.scrollIntoView = () => {}
})

describe('入口的注册：路由与权限在 App.tsx，按钮在 Agent 工作台顶栏（R27/D4 + 本轮 D2）', () => {
  it('MENU_REGISTRY 认得 pending，且标成不占侧栏一格', () => {
    expect(MENU_REGISTRY.pending).toMatchObject({
      path: '/pending', label: '待确认', symbol: 'Pd', nav: false,
    })
    expect(ROUTE_MENU['/pending']).toBe('pending')
  })

  it('后端照发 pending，侧栏也不许长出这一项（门是工作台内部的事，不与 Agent 工作台平级）', () => {
    const nav = navItemsFor(['chat', 'pending', 'class']).map((n) => n.key)
    expect(nav).toEqual(['chat', 'class'])
    // 页脚那句当前页名靠 NAV_OWNER 认回去
    expect(NAV_OWNER['/pending']).toBe('/chat')
  })

  it('路由配置把 /pending 交给 pending 菜单守卫', () => {
    // ROUTE_MENU 是守卫读的那份配置：路由必须映射到真实存在的菜单 key
    expect(ROUTE_MENU['/pending']).toBe('pending')
    expect(MENU_REGISTRY[ROUTE_MENU['/pending']].path).toBe('/pending')
    // 侧栏那条旧通路必须真的拆掉，否则两处各画一个数字
    expect(navItemsFor(['chat', 'pending']).map((n) => n.key)).toEqual(['chat'])
  })

})

describe('待确认页', () => {
  it('副标题给计数与最久时长；行首是真链接且指向该行会话', async () => {
    stub({ items: [gate(0), gate(1)], owned_total: 2, unowned_items: [], unowned_total: 0 })
    mount()

    expect(await screen.findByText(/你有 2 件事在等你的决定/)).toBeInTheDocument()
    expect(screen.getByText(/最久的已等/)).toBeInTheDocument()

    // 行链接要按 href 挑：页首后来加了「← 返回工作台」那条 Link，[0] 已经不是行了。
    const link = screen.getAllByRole('link')
      .find((l) => (l.getAttribute('href') || '').startsWith('#/chat?thread='))!
    expect(link.getAttribute('href')).toBe('#/chat?thread=t-0')
    expect(link.textContent).toContain('发送给家长 · 王梓涵')
  })

  it('「来自会话」列的标题走化学式渲染（ADR-0022：渲染只发生在显示层）', async () => {
    stub(
      { items: [gate(0), gate(1)], owned_total: 2, unowned_items: [], unowned_total: 0 },
      (id) => (id === 't-0' ? 'H2O 的电解实验' : `会话 ${id}`),
    )
    mount()

    expect(await screen.findByText('H₂O 的电解实验')).toBeInTheDocument()
    expect(screen.queryByText('H2O 的电解实验')).not.toBeInTheDocument()
    expect(screen.getByText('会话 t-1')).toBeInTheDocument()
  })

  it('胶囊读 owned_total：60 件时页面仍只列 50 行，尾行说清差额（D14）', async () => {
    const items = Array.from({ length: 50 }, (_, i) => gate(i))
    stub({ items, owned_total: 60, unowned_items: [], unowned_total: 0 })
    mount()

    await waitFor(() => expect(peekPendingStore().snapshot.owned_total).toBe(60))
    expect(await screen.findByText(/你有 60 件事在等你的决定/)).toBeInTheDocument()
    expect(screen.getByText(/另有 10 件未显示/)).toBeInTheDocument()
    // 待确认页不再发第二份计数请求（R19 第③点）
    expect(COUNT_CALLS.filter((u) => u.includes('/api/agent/gates/pending'))).toHaveLength(1)
  })

  it('折叠段：段头读 unowned_total，超过返回条数时标注截断（D3/R24）', async () => {
    stub({
      items: [gate(0)], owned_total: 1,
      unowned_items: [gate(5, 'boolean'), gate(6, 'boolean')], unowned_total: 243,
    })
    mount()

    const header = await screen.findByRole('button', { name: /另有 243 条来自归属记录上线前的老会话/ })
    expect(header).toHaveAttribute('aria-expanded', 'false')
    // 段头那句必须与展开后可见的条数对上：写死"20 条"会在返回不足 20 条时打脸
    expect(header.textContent).toContain('按等待时长显示最久的 2 条')

    header.click()
    await waitFor(() => expect(header).toHaveAttribute('aria-expanded', 'true'))
    expect(document.getElementById('unowned-gates')).toBeTruthy()
  })

  it('首屏加载中出骨架，不是一句「你有 0 件事」', async () => {
    // 一个永不落地的请求：让 loading 一直为真
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})))
    const { container } = mount()
    await new Promise((r) => setTimeout(r, 60))
    expect(container.querySelectorAll('.animate-pulse').length).toBeGreaterThan(0)
    expect(screen.queryByText(/你有 0 件事/)).not.toBeInTheDocument()
    expect(screen.queryByText('没能取到待确认列表')).not.toBeInTheDocument()
  })

  it('名下空但有老会话：不给插画、副标题改口，折叠段默认展开（D5 第二种空）', async () => {
    stub({ items: [], owned_total: 0, unowned_items: [gate(7)], unowned_total: 1 })
    mount()

    expect(await screen.findByText(/你名下没有在等的门/)).toBeInTheDocument()
    expect(screen.queryByText('没有在等你的事')).not.toBeInTheDocument()
    // 「说明这里为什么有别人的旧门」这一存在理由，收起来就等于没说过
    expect(screen.getByRole('button', { name: /另有 1 条来自归属记录上线前的老会话/ }))
      .toHaveAttribute('aria-expanded', 'true')
    expect(document.getElementById('unowned-gates')).toBeTruthy()
  })

  it(`owned_total ≤ 50 时它 == items.length（T9 Verify 的另一半）`, async () => {
    const items = Array.from({ length: 3 }, (_, i) => gate(i))
    stub({ items, owned_total: 3, unowned_items: [], unowned_total: 0 })
    mount()
    await screen.findByText(/你有 3 件事在等你的决定/)
    expect(peekPendingStore().snapshot.items).toHaveLength(3)
    expect(document.querySelectorAll('tbody tr')).toHaveLength(3)
  })

  it('跳回时那份快照被 50 行上限截断过 → 不演「已批准」，宁可不显示终态行（D9）', async () => {
    const items = Array.from({ length: 50 }, (_, i) => gate(i))
    stub({ items, owned_total: 60, unowned_items: [], unowned_total: 0 })
    await refetchPendingCount()
    stub({ items: items.slice(1), owned_total: 59, unowned_items: [], unowned_total: 0 })

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={[{ pathname: '/pending', state: { justApproved: 'g0' } }]}>
          <PendingGatesPage />
        </MemoryRouter>
      </ToastProvider>,
    )
    await refetchPendingCount()

    // 快照不完整时"它不在结果里"说明不了任何事，不能把它演成已批准
    expect(screen.queryByText(/已批准，AI 正在执行/)).not.toBeInTheDocument()
  })

  it('批准那一帧的乐观减一不算「快照不完整」，终态行照演（D7 与 D9 相撞，浏览器实测踩过）', async () => {
    stub({ items: [gate(0), gate(1)], owned_total: 2, unowned_items: [], unowned_total: 0 })
    await refetchPendingCount()
    // 聊天页批准时先乐观减一：`owned_total` 变 1、`items` 还是 2 条。
    // 旧写法用 `items.length === owned_total` 猜截断，于是每一次批准都演不出终态行。
    decrementPendingCount()
    stub({ items: [gate(1)], owned_total: 1, unowned_items: [], unowned_total: 0 })

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={[{ pathname: '/pending', state: { justApproved: 'g0' } }]}>
          <PendingGatesPage />
        </MemoryRouter>
      </ToastProvider>,
    )
    await refetchPendingCount()

    expect(await screen.findByText(/已批准，AI 正在执行/)).toBeInTheDocument()
    expect(screen.getByText('发送给家长 · 王梓涵(高一(1)班)')).toBeInTheDocument()
  })

  it('全空：一句「全部处理完」的情绪回执 + 回工作台的次级链接（D5/D2）', async () => {
    stub({ items: [], owned_total: 0, unowned_items: [], unowned_total: 0 })
    mount()

    expect(await screen.findByText('全部处理完')).toBeInTheDocument()
    expect(screen.getByText('没有在等你的事')).toBeInTheDocument()
  })

  it('刷新失败但旧列表还在：留着那几行，顶部明说它是多久前的（D6 的第二种失败）', async () => {
    const payload = { items: [gate(0), gate(1), gate(2)], owned_total: 3,
                      unowned_items: [], unowned_total: 0 }
    stub(payload)
    await refetchPendingCount()
    mount()
    await screen.findByText(/你有 3 件事在等你的决定/)

    // 页面开着的时候网络抖一下
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'Internal Server Error' }), { status: 500 },
    )))
    await refetchPendingCount(true)

    const banner = await screen.findByRole('status')
    // 文案里"前，"后面有一个 JSX 折行带来的真空格，别把它写死成"前，可能"
    expect(banner.textContent).toMatch(/列表更新于.*可能不是最新/)
    expect(banner).toHaveTextContent('重试')
    // 老师正要点的那三行一行都不能少
    expect(document.querySelectorAll('tbody tr')).toHaveLength(3)

    // 重试真的再拉一次，拉回来了就不再谎报"可能不是最新"
    stub(payload)
    fireEvent.click(within(banner).getByRole('button', { name: '重试' }))
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
    expect(document.querySelectorAll('tbody tr')).toHaveLength(3)
  })

  it('首屏失败：整页错误 + 重试，不出现堆栈与状态码（D6）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ detail: 'Internal Server Error' }), { status: 500 },
    )))
    mount()

    expect(await screen.findByText('没能取到待确认列表')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
  })

  it('本页不拿 --color-text-muted 承载信息（D13 的本页禁令）', async () => {
    stub({ items: [gate(0)], owned_total: 1, unowned_items: [], unowned_total: 0 })
    const { container } = mount()
    await screen.findByText(/你有 1 件事在等你的决定/)
    expect(container.innerHTML).not.toContain('text-ink-muted')
  })

  it('刚批准的那一行以行内终态留着，且「查看执行 →」还指着原会话（D9）', async () => {
    // 真实时序：聊天页跳转前 store 里已经有那一行（g9 / t-9），跳回来重拉后
    // 服务端不再返回它（门已 executed）→ 那一行不该消失，而是变成终态行。
    stub({
      items: [{ ...gate(9), gate_id: 'g9', conversation_id: 't-9' }],
      owned_total: 1, unowned_items: [], unowned_total: 0,
    })
    await refetchPendingCount()
    stub({ items: [], owned_total: 0, unowned_items: [], unowned_total: 0 })

    render(
      <ToastProvider>
        <MemoryRouter initialEntries={[{ pathname: '/pending', state: { justApproved: 'g9' } }]}>
          <PendingGatesPage />
        </MemoryRouter>
      </ToastProvider>,
    )
    // 进本页的那次重拉由 Shell 的路由切换效应负责（R19 第①点，页面自己不发第二份）
    await refetchPendingCount()

    const link = await screen.findByText(/已批准，AI 正在执行/)
    expect(link.getAttribute('href')).toBe('#/chat?thread=t-9')
    // 计数已不含它：终态行只占一个位置，不再是一件事
    expect(await screen.findByText(/你有 0 件事在等你的决定/)).toBeInTheDocument()
    expect(screen.queryByText('没有在等你的事')).not.toBeInTheDocument()
  })
})

describe('等待时长的显示口径（D1：不到分）', () => {
  const now = Date.UTC(2026, 8, 25, 12, 0, 0)
  it('天 + 小时；不足一天只说小时；不足一小时说分', () => {
    expect(humanWait('2026-09-19T00:00:00', now)).toBe('6天12小时')
    expect(humanWait('2026-09-24T20:00:00', now)).toBe('16小时')
    expect(humanWait('2026-09-25T11:30:00', now)).toBe('30分')
  })

  it('缺时间戳给一个占位而不是空白单元格', () => {
    expect(humanWait(null)).toBe('—')
    expect(humanWait('不是时间')).toBe('—')
  })
})

/* ── 「结果未知」段（gate-lifecycle 2.7 第 4 步）─────────────────────────
 *
 * 这一态来自**服务重启**：门的许可在执行前就占掉了，正常由收口放掉；重启时还停在
 * 「执行中」的门，说明执行它的进程已经不在了，跑没跑成无从判断。所以它是一张卡，
 * 不是一个自动动作 —— 系统猜不出来，老师核对真实世界猜得出来。
 *
 * 这里钉三件事：这两个按钮**真的打到那个端点**（不是假控件）、「重试」会把那一扇
 * 放回待确认列表（它回到 `open` 了，老师得能在那儿看见它）、空列表不出这一段。
 */
describe('「结果未知」段：两个动作打到端点、重试放回队列', () => {
  const doubt = {
    gate_id: 'g-in-doubt',
    conversation_id: 't-9',
    kind: 'boolean',
    action: '',
    tool_name: 'assign_adaptive_practice',
    action_label: '',
    subject_label: '布置针对性练习',
    message: '此操作需要老师确认后执行',
    last_opened_at: '2026-09-20T00:00:00',
  }
  const empty = { items: [], owned_total: 0, unowned_items: [], unowned_total: 0 }

  it('有「结果未知」的门时才出这一段，行里两个按钮都在', async () => {
    stub(empty, (id) => `会话 ${id}`, [doubt])
    mount()
    await waitFor(() => expect(screen.getByText(/结果无法确认/)).toBeInTheDocument())
    expect(screen.getByRole('button', { name: '确认已执行' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument()
    expect(screen.getByText('布置针对性练习')).toBeInTheDocument()
  })

  it('没有这一类门时整段不出现（不给页面添一张空卡）', async () => {
    stub(empty)
    mount()
    await waitFor(() => expect(peekPendingStore().loaded).toBe(true))
    expect(screen.queryByText(/结果无法确认/)).not.toBeInTheDocument()
  })

  it('「确认已执行」打 retry=false —— 结案，不动待确认列表', async () => {
    stub(empty, (id) => `会话 ${id}`, [doubt])
    mount()
    await waitFor(() => expect(screen.getByRole('button', { name: '确认已执行' })).toBeInTheDocument())
    const before = COUNT_CALLS.filter((u) => u.includes('/gates/pending')).length
    fireEvent.click(screen.getByRole('button', { name: '确认已执行' }))
    await waitFor(() => expect(POSTS).toHaveLength(1))
    expect(POSTS[0].url).toContain('/api/agent/gates/g-in-doubt/in-doubt')
    expect(JSON.parse(POSTS[0].body)).toEqual({ retry: false })
    // 结案的门回到 open 也仍是"已处理"，没有东西该进待确认列表
    await waitFor(() =>
      expect(COUNT_CALLS.filter((u) => u.includes('/gates/pending')).length).toBe(before))
  })

  it('「重试」打 retry=true，**并把那一扇放回待确认列表**', async () => {
    /* 后半句才是这一条的重点：重试把门退回 `open`，那一扇随即属于待确认队列。
       只发 POST 不重拉，老师刚放回去的门在另一张表上看不见 —— 他会以为没生效，
       再去点一次。 */
    stub(empty, (id) => `会话 ${id}`, [doubt])
    mount()
    await waitFor(() => expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument())
    const before = COUNT_CALLS.filter((u) => u.includes('/gates/pending')).length
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(POSTS).toHaveLength(1))
    expect(JSON.parse(POSTS[0].body)).toEqual({ retry: true })
    await waitFor(() =>
      expect(COUNT_CALLS.filter((u) => u.includes('/gates/pending')).length)
        .toBeGreaterThan(before))
  })
})

describe('挂载必拉最新（2026-10-26 8 步实测：store 无周期轮询，loaded 快照可能停在门行提交之前）', () => {
  it('store 已 loaded 且陈旧时，进页仍强制重拉——不是只看旧快照', async () => {
    // 第一阶段：用旧载荷把 store 灌成 loaded（模拟聊天页早先拉过一次）
    stub({ items: [gate(0, 'draft')], owned_total: 1, unowned_items: [], unowned_total: 0 })
    await refetchPendingCount()
    // 门行提交之后，接口有了新数据
    stub({ items: [gate(0, 'draft'), gate(1, 'boolean')], owned_total: 2, unowned_items: [], unowned_total: 0 })
    mount()
    expect(await screen.findByText(/你有 2 件事在等你的决定/)).toBeInTheDocument()
  })
})
