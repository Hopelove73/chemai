/* use-review-due.ts — 学生端「复习到期数」单一真源（S1 / REQ-RCS-004）。
 * 同 use-pending-count 的形状：模块级状态 + 订阅 + useSyncExternalStore。
 * 底栏「复习」徽章与复习中心页读同一份，避免各页各拉各的。 */

import { useSyncExternalStore } from 'react'
import { api } from './api'

interface DueState {
  count: number
  loaded: boolean
  studentId: string
  fetchedAt: number
}

let state: DueState = { count: 0, loaded: false, studentId: '', fetchedAt: 0 }
const listeners = new Set<() => void>()

function set(next: Partial<DueState>) {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => {
    listeners.delete(fn)
  }
}

const getSnapshot = () => state

let inflight: Promise<void> | null = null
let inflightSid = ''

function startFetch(studentId: string): Promise<void> {
  const p = api
    .get<{ due_count?: number }>(`/api/review/student/${encodeURIComponent(studentId)}/count`)
    .then((d) => {
      set({ count: Number(d.due_count ?? 0), loaded: true, studentId, fetchedAt: Date.now() })
    })
    .catch(() => {
      /* 静默：徽章是增强信息，拉不到不弹错 */
    })
    .finally(() => {
      if (inflight === p) inflight = null
    })
  inflight = p
  inflightSid = studentId
  return p
}

/** 拉到期数；并发同学生会话合并成一份。复习提交后调 force 重拉。 */
export function refetchReviewDue(studentId: string, force = false): Promise<void> {
  if (!studentId) return Promise.resolve()
  if (inflight && inflightSid === studentId) return force ? inflight.then(() => startFetch(studentId)) : inflight
  return startFetch(studentId)
}

/** 底栏徽章与复习中心共用的读接口。 */
export function useReviewDue(studentId?: string): number {
  const s = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
  if (studentId && (s.studentId !== studentId || !s.loaded)) {
    // 触发式拉取（渲染期发请求与 useEffect 等价；并发由 inflight 合并）
    void refetchReviewDue(studentId)
  }
  return s.studentId === studentId ? s.count : 0
}

export function __resetReviewDueForTests() {
  state = { count: 0, loaded: false, studentId: '', fetchedAt: 0 }
  inflight = null
  listeners.clear()
}
