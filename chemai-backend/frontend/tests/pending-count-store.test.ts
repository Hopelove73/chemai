/* 待确认计数的唯一载体（eng 二轮 R19，D24=A）—— 形状与口径都在这里钉。
 *
 * 这一份测试防的是两种各自成形的病：
 *   · 侧栏胶囊读 `items.length` → 上限 50 一截断，老师看到「7」而名下其实 57 件；
 *   · 三个组件各拉各的 `/gates/pending` → 同一屏出现两个不同的数。
 * 无 DOM：store 是模块级状态 + 一个 hook，直接驱动它就行（先例见
 * `src/lib/__tests__/agent-stream-reducer.test.ts`）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetPendingCountForTests,
  peekPendingStore,
  decrementPendingCount,
  fetchPendingFiltered,
  refetchPendingCount,
} from '../src/lib/use-pending-count'

const calls: string[] = []

function stubFetch(payload: unknown, opts: { fail?: boolean } = {}) {
  calls.length = 0
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    calls.push(url)
    if (opts.fail) {
      return new Response(JSON.stringify({ detail: '服务不可用' }), { status: 503 })
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  }))
}

const PAYLOAD = {
  items: [
    { gate_id: 'g1', conversation_id: 't-1', kind: 'draft', action: 'send_parent',
      tool_name: '', action_label: '发送给家长', subject_label: '王梓涵(高一(2)班)',
      message: 'm', last_opened_at: '2026-09-20T00:00:00' },
    { gate_id: 'g2', conversation_id: 't-2', kind: 'boolean', action: '',
      tool_name: 'bash', action_label: '', subject_label: '执行命令 · 批卷脚本',
      message: 'm', last_opened_at: '2026-09-21T00:00:00' },
  ],
  owned_total: 57,
  unowned_items: [],
  unowned_total: 3,
}

beforeEach(() => {
  __resetPendingCountForTests()
  localStorage.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('待确认计数 store', () => {
  it('胶囊读 owned_total，不是 items.length', async () => {
    stubFetch(PAYLOAD)
    await refetchPendingCount()
    const s = peekPendingStore()
    expect(s.snapshot.owned_total).toBe(57)
    expect(s.snapshot.items).toHaveLength(2)
    expect(s.error).toBe('')
  })

  it('并发重拉只发一份请求（防三个组件各拉各的）', async () => {
    stubFetch(PAYLOAD)
    await Promise.all([refetchPendingCount(), refetchPendingCount(), refetchPendingCount()])
    expect(calls.filter((u) => u.includes('/api/agent/gates/pending'))).toHaveLength(1)
  })

  it('force 会等完在飞的那一发再拉第二发（决定之后必须看到新状态）', async () => {
    stubFetch(PAYLOAD)
    const first = refetchPendingCount()          // 侧栏路由切换抢发的那一发
    const forced = refetchPendingCount(true)     // 批准流读完后要的那一发
    await Promise.all([first, forced])
    expect(calls.filter((u) => u.includes('/api/agent/gates/pending'))).toHaveLength(2)
  })

  it('乐观减一只动 owned_total，且不会减成负数', async () => {
    stubFetch(PAYLOAD)
    await refetchPendingCount()
    decrementPendingCount()
    expect(peekPendingStore().snapshot.owned_total).toBe(56)
    for (let i = 0; i < 60; i += 1) decrementPendingCount()
    expect(peekPendingStore().snapshot.owned_total).toBe(0)
  })

  it('刷新失败保留旧快照并记下原因（D6：不许把队列换成一句「出错了」）', async () => {
    stubFetch(PAYLOAD)
    await refetchPendingCount()
    stubFetch(null, { fail: true })
    await refetchPendingCount()
    const s = peekPendingStore()
    expect(s.snapshot.owned_total).toBe(57)
    expect(s.loaded).toBe(true)
    expect(s.error).not.toBe('')
  })

  it('筛选那份请求不动快照：胶囊口径始终是未筛选的 owned_total', async () => {
    stubFetch(PAYLOAD)
    await refetchPendingCount()
    stubFetch({ ...PAYLOAD, owned_total: 2, items: PAYLOAD.items.slice(0, 2) })
    const filtered = await fetchPendingFiltered('draft')
    expect(filtered.owned_total).toBe(2)
    expect(peekPendingStore().snapshot.owned_total).toBe(57)
    expect(calls[calls.length - 1]).toContain('kind=draft')
  })
})
