/* 「结果未知」门数的单一真源（gate-lifecycle 2.7）。
 *
 * 同 `use-pending-count` / `use-review-due` 的形状：模块级状态 + 订阅 +
 * `useSyncExternalStore`（`getSnapshot` 必须返回稳定引用，每次给新对象会让 React
 * 判定"一直变了"，直接循环重渲染）。两处读它：Agent 工作台顶栏那颗提示、
 * 待确认页那一段清单 —— 各拉各的就会数出两个数。
 *
 * **为什么不并进 `use-pending-count`。** 那张胶囊数的是「在等你**决定**的门」，
 * 这一份数的是「在等你**裁定**的门」。两个问题，两个数字：并成一个的话，老师看见
 * 「3」点进去可能只找到 1 扇待确认的门（另 2 扇是待裁定的），反而多一次困惑。
 * 界面上也据此另给一个**不同颜色**的提示，不挤进那颗蓝色胶囊。
 *
 * 这一态怎么来的见 `PendingGatesPage` 的 `InDoubtSection`：服务重启时还停在
 * 「执行中」的门 —— 动作可能跑过、也可能没跑，系统无从判断，只有老师能核对真实世界。
 * 它恰恰是那种"你不会主动去找"的东西（成因是进程崩在重放中途），所以顶栏那点提示
 * 不是锦上添花：没有它，这张卡就等于不存在。
 */
import { useSyncExternalStore } from 'react'
import { api } from './api'
import { EMPTY_PENDING, type PendingPayload } from './use-pending-count'

interface InDoubtState {
  snapshot: PendingPayload
  loaded: boolean
  loading: boolean
  error: string
}

let state: InDoubtState = {
  snapshot: EMPTY_PENDING, loaded: false, loading: false, error: '',
}
const listeners = new Set<() => void>()

function set(next: Partial<InDoubtState>) {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

const getSnapshot = () => state

/** 不走 hook 读一份当前状态（测试与事件回调用）。 */
export const peekInDoubtStore = () => state

let inflight: Promise<void> | null = null

function startFetch(): Promise<void> {
  set({ loading: true })
  const p = api
    .get<PendingPayload>('/api/agent/gates/in-doubt')
    .then((data) => {
      set({ snapshot: { ...EMPTY_PENDING, ...data }, loaded: true,
            error: '', loading: false })
    })
    .catch((err: unknown) => {
      // 刷新失败**不**抽走旧快照（同 D6）：把老师正要点的那一段换成一句"出错了"，
      // 比一次网络抖动更糟。留着旧数据 + 明说它旧。
      set({ error: err instanceof Error ? err.message : '没能取到「结果未知」列表',
            loading: false })
    })
    .finally(() => {
      if (inflight === p) inflight = null
    })
  inflight = p
  return p
}

/** 拉一份「结果未知」清单。并发调用只发一份请求。
 *
 * `force` 给"刚裁定完，要看到结果"的场合用：不 force 时并发会合并成在飞的那一发，
 * 而那一发反映的还是裁定之前的状态。 */
export function refetchInDoubt(force = false): Promise<void> {
  if (inflight) return force ? inflight.then(() => startFetch()) : inflight
  return startFetch()
}

/** 顶栏提示与待确认页那一段共用的读接口。
 *
 * `count` 读**未筛选**的 `owned_total`（你名下的全部条数），NEVER 用
 * `items.length` —— 服务端有上限，会截断它。口径与那张待确认胶囊一致。 */
export function useInDoubt() {
  const s = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  return {
    data: s.snapshot,
    loaded: s.loaded,
    loading: s.loading,
    error: s.error,
    count: s.snapshot.owned_total,
    refetch: refetchInDoubt,
  }
}

/** 测试用：把模块级状态清回初始值，避免用例之间串味。 */
export function __resetInDoubtForTests() {
  state = { snapshot: EMPTY_PENDING, loaded: false, loading: false, error: '' }
  inflight = null
  listeners.clear()
}
