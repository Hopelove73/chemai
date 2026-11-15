import { useState } from 'react'
import { ShieldAlert, ShieldCheck, ShieldX, Undo2, Ban } from 'lucide-react'
import { Button } from '../ui'
import type { ApprovalStatus } from '../../lib/agent-stream-reducer'
import { gateChipLabel } from '../../lib/gate-labels'

export interface ApprovalData {
  text: string
  status: ApprovalStatus
  /** 2026-08 发送确认门：动态批准按钮文案标记（send_student/send_parent） */
  action?: string
  /** ADR-0015 阶段 4：收件人语义（student/parent/system）——与 action 一起渲染按钮，
   *  按钮文案永远来自本门自己的数据（收件人不可能串门） */
  recipient?: 'student' | 'parent' | 'system'
  /** ADR-0013 三值门：门挂在草稿实体上。draft_id 缺失 = 布尔门，打回不可用 */
  draft_id?: string
  version?: number
  /** ADR-0015 强绑定键：决定只作用于这个门（一决定一动作） */
  gate_id?: string
  /** 触发工具名（2026-10-26）：布尔门通用文案没有区分度，终态小票标签靠它 */
  tool_name?: string
  /** 流式期间老师已经按下决定：这条等本轮结束自动执行（卡片上必须看得见，不是 toast）。 */
  queued?: GateDecision | null
}

/** 「已记录」回执里那个决定的中文说法 */
const QUEUED_LABEL: Record<GateDecision, string> = {
  approve: '批准',
  reject: '打回',
  abandon: '放弃',
}

export type GateDecision = 'approve' | 'reject' | 'abandon'

/** 审批确认卡：Agent 请求执行破坏性操作前需要教师确认。
 * ADR-0013 三值门：批准（发送并继续）/ 打回（带反馈，修订后再确认）/ 放弃（跳过继续）。 */
export function ApprovalCard({
  approval,
  onResolve,
}: {
  approval: ApprovalData
  onResolve: (decision: GateDecision, feedback?: string) => void
}) {
  const [feedback, setFeedback] = useState('')
  const [rejecting, setRejecting] = useState(false)
  /* 在途状态自管（2026-10 死锁修复）。原先由父级用 `disabled={streaming}` 锁住，
     实测形态是：门开着 → 模型在同一轮里继续跑（一直是流式）→ 卡片按钮整段时间
     都是灰的 → 老师点不了 → 门永远关不掉 → 后面每一次写操作都被挡，越重试越死。
     现在只锁「本卡这一下」，且 1.2s 自动放开：请求真失败也不会把按钮永久锁死。
     并发那一层由后端会话锁兜底（409 → 父级 toast），不是在 UI 上假装点不动。 */
  const [busy, setBusy] = useState(false)
  const resolve = (decision: GateDecision, fb?: string) => {
    if (busy) return
    setBusy(true)
    onResolve(decision, fb)
    window.setTimeout(() => setBusy(false), 1200)
  }
  const canReject = Boolean(approval.draft_id)
  const approveLabel =
    approval.action === 'send_student' || approval.recipient === 'student'
      ? '发送给学生'
      : approval.action === 'send_parent' || approval.recipient === 'parent'
        ? '同步给家长'
        : '批准'

  /* 终态小票带「批/弃的是什么」：账本恢复的多扇门摞在对话尾部，只渲染固定
     短语分不出哪条对应哪次（2026-10-26 实测用户当场指出）。活卡翻面的 text
     是待批文案，通用话术（「此操作需要老师确认…」）没有区分度——由 gate-labels
     统一截取/回退工具短标签（与历史恢复同一份）。 */
  const _tail = (() => {
    const label = gateChipLabel(approval.text, approval.tool_name)
    return label && label !== '这项操作' ? `：${label}` : ''
  })()

  if (approval.status === 'approved') {
    return (
      <div className="self-start flex items-center gap-2 bg-success-soft text-success rounded-[var(--radius-md)] px-3.5 py-2.5 text-sub">
        <ShieldCheck size={16} />
        已批准执行{_tail}
      </div>
    )
  }
  if (approval.status === 'rejected') {
    return (
      <div className="self-start flex items-center gap-2 bg-warning-soft text-warning rounded-[var(--radius-md)] px-3.5 py-2.5 text-sub border border-warning/40">
        <Undo2 size={16} />
        已打回，按意见修订中{_tail}
      </div>
    )
  }
  if (approval.status === 'abandoned' || approval.status === 'cancelled') {
    return (
      <div className="self-start flex items-center gap-2 bg-surface-muted text-ink-secondary rounded-[var(--radius-md)] px-3.5 py-2.5 text-sub">
        <ShieldX size={16} />
        已放弃{_tail}
      </div>
    )
  }
  return (
    <div className="shrink-0 self-start bg-warning-soft border border-warning/25 rounded-[var(--radius-md)] px-4 py-3 max-w-[92%] animate-fade-in-up">
      <div className="flex items-center gap-2 text-warning text-sub font-medium">
        <ShieldAlert size={16} />
        需要确认{approval.version ? ` · 第 ${approval.version} 版` : ''}
      </div>
      <p className="mt-1.5 text-sub text-ink-secondary m-0">{approval.text}</p>
      {approval.queued && (
        <p className="mt-1.5 text-sub text-warning m-0" data-gate-queued="true">
          已记录「{QUEUED_LABEL[approval.queued]}」：本轮结束后自动确认
        </p>
      )}
      {rejecting && (
        <div className="mt-2.5">
          <textarea
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="写明要修改的地方，如：第二周改成盐类水解专题"
            rows={2}
            className="w-full text-sub px-2.5 py-2 rounded-[var(--radius-sm)] border border-border bg-surface focus:outline-none focus:border-teal resize-y"
            autoFocus
          />
        </div>
      )}
      <div className="flex gap-2 mt-3">
        <Button
          size="sm"
          variant="primary"
          disabled={busy}
          onClick={() => resolve('approve')}
        >
          {approveLabel}
        </Button>
        {canReject && !rejecting && (
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => setRejecting(true)}>
            打回修改
          </Button>
        )}
        {rejecting && (
          <Button
            size="sm"
            variant="secondary"
            disabled={!feedback.trim() || busy}
            onClick={() => resolve('reject', feedback.trim())}
          >
            提交修改意见
          </Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => resolve('abandon')}>
          <Ban size={13} className="mr-1" />
          放弃
        </Button>
      </div>
      {!canReject && (
        <p className="mt-2 text-micro text-ink-muted m-0">该操作不支持修改，只能批准或放弃</p>
      )}
    </div>
  )
}
