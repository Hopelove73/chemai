/* 相似题检索降级 → 老师能读懂的一句话。
 *
 * 后端在 /api/question/generate 的响应里带 `retrieval`（用了哪一层、有没有降级、为什么）。
 * 这一层没干活时题目照样生成，只是不再锚在历年真题上 —— 两种情况在页面上长得一模一样，
 * 所以区别必须由这句话说出来（工单 VS1）。
 */

export type Retrieval = {
  used?: string
  degraded?: boolean
  reason?: string | null
  available?: boolean | null
  vector_count?: number | null
  vector_hits?: number | null
}

/** 降级时返回那句提示；正常（或这一层根本没参与，如蓝本变种题）返回 null。 */
export function retrievalNotice(retrieval?: Retrieval | null): string | null {
  if (!retrieval?.degraded) return null
  const reason = (retrieval.reason || '').trim()
  return reason
    ? `相似题检索暂时不可用：${reason}（本次生成的题目没锚定历年真题）`
    : '相似题检索暂时不可用（本次生成的题目没锚定历年真题）'
}
