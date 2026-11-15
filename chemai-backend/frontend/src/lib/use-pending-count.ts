/* 待确认计数的唯一载体（计划 eng 二轮 R19，D24=A）—— 侧栏胶囊与待确认页读同一份。
 *
 * 为什么要有这个文件：那一串门既出现在侧栏（一个数字），也出现在待确认页（一张表）。
 * 各拉各的会出两种病：①同一次进页面发两份 `/gates/pending`；②胶囊和页面数到不同的
 * 数（一份带筛选、一份不带）。所以计数只有一份真源：模块级状态 + 订阅 + 一个 hook，
 * 组件经 `useSyncExternalStore` 读 —— 取快照必须返回稳定引用，每次给新对象会让
 * React 判定"一直变了"，直接循环重渲染。
 *
 * 口径（D3/D14）：胶囊读 `owned_total`（你名下的全部条数，**不含**归属记录上线前的
 * 老会话段），NEVER 用 `items.length`（上限 50 会截断它），也 NEVER 用"含无主的合计"。
 */
import { useSyncExternalStore } from 'react'
import { api } from './api'

export interface PendingItem {
  gate_id: string
  conversation_id: string
  kind: string
  action: string
  tool_name: string
  /** 「发送给家长」这类动作词，后端算好下发（D18） */
  action_label: string
  /** 「王梓涵(高一(2)班)」这类对象名，同上 */
  subject_label: string
  message: string
  /** naive UTC 的 ISO 串；起算等待时长用（D1/发9） */
  last_opened_at: string | null
}

export interface PendingPayload {
  items: PendingItem[]
  owned_total: number
  unowned_items: PendingItem[]
  unowned_total: number
}

/** 空载荷。页面在"筛选结果还没回来"时也要一个形状正确的对象，别自己重拼字面量。 */
export const EMPTY_PENDING: PendingPayload = {
  items: [], owned_total: 0, unowned_items: [], unowned_total: 0,
}

interface StoreState {
  snapshot: PendingPayload
  loaded: boolean
  loading: boolean
  error: string
  fetchedAt: number
  /** 当前快照是否被 50 行上限截断过（`owned_total > items.length`）。
   *  单独存一份而不是让页面去比 `items.length === owned_total`：乐观减一会先把
   *  `owned_total` 调小、`items` 里那一行还留着，一比就"被截断了"，
   *  于是每次批准都演不出 D9 的那一行终态。 */
  truncated: boolean
}

/* `useSyncExternalStore` 取的必须是**稳定引用**：每次 getSnapshot 返回新对象会让
   React 判定"一直变了"，直接循环重渲染。所以下面所有写操作都是整体换一个 state 对象。 */
let state: StoreState = {
  snapshot: EMPTY_PENDING, loaded: false, loading: false, error: '', fetchedAt: 0,
  truncated: false,
}
const listeners = new Set<() => void>()

function set(next: Partial<StoreState>) {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

const getSnapshot = () => state

/** 不走 hook 读一份当前状态：测试、以及在事件回调里想知道"现在是几"的场合用。 */
export const peekPendingStore = () => state

/** 拉一份未筛选的列表：更新快照并通知订阅者。并发调用只发一份请求。
 *
 * `force` 给"刚做完一件事，要看到结果"的场合用（批准/放弃之后）。不 force 时并发会
 * 合并成一份 —— 那是好事，除了一个例外：跳回队列时侧栏的路由切换效应会先抢一发，
 * 那一发反映的还是决定之前的状态。等它落定再拉第二发，才不会把旧数字当成新结果，
 * 让角标出现 7→6→7 的来回弹。
 */
let inflight: Promise<void> | null = null

function startFetch(): Promise<void> {
  set({ loading: true })
  const p = api
    .get<PendingPayload>('/api/agent/gates/pending')
    .then((data) => {
      const snapshot = { ...EMPTY_PENDING, ...data }
      set({
        snapshot,
        loaded: true,
        error: '',
        fetchedAt: Date.now(),
        loading: false,
        truncated: snapshot.items.length < snapshot.owned_total,
      })
    })
    .catch((err: unknown) => {
      // 刷新失败**不**抽走旧快照（D6）：把老师正要点的那一行换成一句"出错了"，
      // 比一次网络抖动更糟。留着旧数据 + 明说它旧，才是这一步的正确形状。
      set({
        error: err instanceof Error ? err.message : '没能取到待确认列表',
        loading: false,
      })
    })
    .finally(() => {
      if (inflight === p) inflight = null
    })
  inflight = p
  return p
}

export function refetchPendingCount(force = false): Promise<void> {
  if (inflight) return force ? inflight.then(() => startFetch()) : inflight
  return startFetch()
}

/** 批准后的乐观减一（D7）：老师要立刻看到反馈，随后重拉以服务端为准。
 *  `items` 与 `truncated` 原样留着 —— 那一行还要给待确认页演终态（D9）。 */
export function decrementPendingCount() {
  set({
    snapshot: { ...state.snapshot, owned_total: Math.max(0, state.snapshot.owned_total - 1) },
  })
}

/**
 * 带类型筛选的那一份（D14 的唯一筛选维度）。
 *
 * 它**不动快照**：胶囊的口径始终是未筛选的 `owned_total`。筛选发生在服务端 SQL 的
 * `LIMIT` 之前（T8），所以前端 `slice` 不是可替代实现。
 */
export function fetchPendingFiltered(kind: string): Promise<PendingPayload> {
  return api
    .get<PendingPayload>(`/api/agent/gates/pending?kind=${encodeURIComponent(kind)}`)
    .then((data) => ({ ...EMPTY_PENDING, ...data }))
}

/** 侧栏胶囊与待确认页共用的读接口。 */
export function usePendingCount() {
  const s = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return {
    data: s.snapshot,
    loaded: s.loaded,
    loading: s.loading,
    error: s.error,
    fetchedAt: s.fetchedAt,
    count: s.snapshot.owned_total,
    truncated: s.truncated,
    refetch: refetchPendingCount,
  }
}

/** 测试用：把模块级状态清回初始值，避免用例之间串味。 */
export function __resetPendingCountForTests() {
  state = {
    snapshot: EMPTY_PENDING, loaded: false, loading: false, error: '', fetchedAt: 0,
    truncated: false,
  }
  inflight = null
  listeners.clear()
}
