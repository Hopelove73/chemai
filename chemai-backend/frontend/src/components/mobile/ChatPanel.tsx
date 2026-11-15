/* ChatPanel.tsx — 移动端流式对话面板（学生 AI 助教 / 家长 AI 助手共用）。
 * 协议：POST /api/agent/chat/langgraph/stream（persona 参数化）+ streamAgent 解析。
 * 气泡（P-01）：用户 = 主色实底胶囊（on-primary 深字）；AI = surface 底 + 品牌青描边。
 * R-I1：发送后输入框清空并禁用至回合结束。
 * S4（student-daily-loop / REQ-MSC-001..004）：
 *  - 与教师端共享 agent-stream-reducer 纯函数归约器（删除内联 reducer），事件全消费
 *  - artifact → 工件卡（跳 /m/artifact/:id 只读预览）；gate_open → 只读提示卡（学生
 *    对话不产生门，防御性渲染）；plan_summary/plan_progress → 折叠进度卡
 *  - navigate 走 lib/mobile-routes 白名单（仅 /m/*），populate/action/context 忽略
 *  - 断流（onAbnormalEnd）按 ADR-0016 语义重拉 history（以 DB 为准），不自动 resume */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Send, Film, Presentation, FileText, Image as ImageIcon, Table2, Sparkles,
  ShieldAlert, ListChecks, ChevronDown, ChevronUp,
} from 'lucide-react'
import { postStream, streamAgent, type AgentEvent } from '../../lib/agent-sse'
import { api } from '../../lib/api'
import { ChemMd } from '../ChemMd'
import { toolLabel } from '../chat/ToolCard'
import { allowedMobileNavigate } from '../../lib/mobile-routes'
import { historyToItems, type MobileHistoryGate, type MobileHistoryMessage } from '../../lib/mobile-chat-history'
import {
  createStreamState,
  reduceAgentEvent,
  seedNewTurn,
  updateApprovalStatus,
  type StreamCtx,
  type StreamState,
} from '../../lib/agent-stream-reducer'
import type { ChatItem } from '../chat/MessageList'

let uid = 0
const nid = () => `mm${Date.now()}_${uid++}`

const ARTIFACT_ICONS: Record<string, typeof Film> = {
  animation: Film,
  slides: Presentation,
  doc: FileText,
  structure: ImageIcon,
  table: Table2,
}

export function ChatPanel({
  persona,
  cid,
  studentId,
  intro,
  quickPrompts = [],
  placeholder = '输入问题…',
  loadHistory = true,
  footerPadding = true,
}: {
  persona: 'student' | 'parent'
  cid: string
  studentId?: string
  intro?: React.ReactNode
  quickPrompts?: string[]
  placeholder?: string
  loadHistory?: boolean
  footerPadding?: boolean
}) {
  const [items, setItems] = useState<ChatItem[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [phase, setPhase] = useState('')
  const [error, setError] = useState('')
  const bodyRef = useRef<HTMLDivElement>(null)
  const cidRef = useRef(cid)
  cidRef.current = cid
  const streamRef = useRef<StreamState>(createStreamState())
  const navigate = useNavigate()

  /* 事件归约上下文：mount 即建（家长刷新后历史里就有待决门卡，
   * decideGate 不经过 send()——ctxRef 为 null 会静默丢整个重放流，捉虫修正） */
  const ctxRef = useRef<StreamCtx>({
    newId: nid,
    onNavigate:
      persona === 'student'
        ? (path, state) => {
            const p = allowedMobileNavigate(path)
            // 接棒载荷经 location.state 传目标页（ADR-0029；教师事件 params 走旧链路）
            if (p) navigate(p, state && Object.keys(state).length ? { state } : undefined)
          }
        : undefined,
  })

  const scrollBottom = () => {
    requestAnimationFrame(() => {
      const el = bodyRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  const applyStream = (st: StreamState) => {
    streamRef.current = st
    setItems(st.items)
    setPhase(st.phase)
    scrollBottom()
  }

  /* 切换会话 → 加载历史（工具卡结构化还原 + pending 门卡恢复） */
  const reloadHistory = useCallback(() => {
    if (!cidRef.current) return
    api.get<{ messages?: MobileHistoryMessage[]; gate?: MobileHistoryGate | null }>(
      `/api/agent/chat/history/${cidRef.current}`,
    )
      .then((d) => {
        const restored = historyToItems(d.messages, nid, d.gate)
        applyStream({ ...createStreamState(), items: restored })
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (!loadHistory || !cid) return
    reloadHistory()
  }, [cid, loadHistory, reloadHistory])

  const send = useCallback(
    (raw?: string) => {
      const text = (raw ?? input).trim()
      if (!text || streaming) return
      setInput('')
      setError('')

      /* navigate 白名单：学生端只放行 /m/*；家长端不接导航。
       * 接棒载荷经 location.state 传目标页（ADR-0029）——与 ctxRef 初值同款，
       * 此处是每轮实际生效的 ctx（初值只兜历史重放） */
      const ctx: StreamCtx = {
        newId: nid,
        onNavigate:
          persona === 'student'
            ? (path, state) => {
                const p = allowedMobileNavigate(path)
                if (p) navigate(p, state && Object.keys(state).length ? { state } : undefined)
              }
            : undefined,
      }
      ctxRef.current = ctx

      const seeded = seedNewTurn(streamRef.current.items, text, nid)
      applyStream(seeded.state)
      setStreaming(true)

      postStream('/api/agent/chat/langgraph/stream', {
        message: text,
        conversationId: cidRef.current,
        persona,
        studentId,
      })
        .then((resp) =>
          streamAgent(resp, {
            onEvent: (evt: AgentEvent) => {
              const t = (evt as { type: string }).type
              if (t === 'populate' || t === 'action' || t === 'context') {
                /* REQ-MSC-002：移动端忽略这些事件但留下计数痕迹，不静默 */
                console.debug('[mobile-chat] ignored event:', evt.type)
              }
              applyStream(reduceAgentEvent(streamRef.current, evt, ctx))
            },
            onDone: () => {
              setStreaming(false)
              setPhase('')
            },
            onError: (m) => {
              setError(m)
              setStreaming(false)
            },
            onAbnormalEnd: () => {
              /* ADR-0016：流被掐断 ≠ 正常完成——重拉 history（DB 为准），不自动 resume */
              setStreaming(false)
              setPhase('')
              api
                .get<{ messages?: MobileHistoryMessage[]; gate?: MobileHistoryGate | null }>(`/api/agent/chat/history/${cidRef.current}`)
                .then((d) => {
                  const restored = historyToItems(d.messages, nid, d.gate)
                  applyStream({
                    ...createStreamState(),
                    items: [...restored, { id: nid(), kind: 'system', text: '连接中断，已重新同步会话状态' }],
                  })
                })
                .catch(() => setError('连接中断，请重试'))
            },
          }),
        )
        .catch((e: Error) => {
          setError(e.message || '发送失败，请重试')
          setStreaming(false)
        })
    },
    [input, streaming, persona, studentId, navigate],
  )

  /* 家长三值门决定（REQ-PAT-002）：镜像教师端 handleDecision——decision 走
   * resume 流；学生 persona 永不出现此入口（S4 隔离，approval 只读）。 */
  /* 流式中的决定不丢（架构候选 C，对齐教师端 2026-10 修的静默丢弃形态：
   * 只 return 不记，家长点批准 0 条 /resume，以为没点上再点一次）。 */
  const queuedGateRef = useRef<{ decision: 'approve' | 'reject' | 'abandon'; gateId: string; draftId?: string; feedback: string } | null>(null)

  const decideGate = useCallback((decision: 'approve' | 'reject' | 'abandon', gateId: string, draftId?: string, feedback = '') => {
    if (streaming) {
      queuedGateRef.current = { decision, gateId, draftId, feedback: feedback || '' }
      return
    }
    if (decision === 'reject' && !feedback.trim()) return  // 打回必须附意见（卡内输入）
    /* 本地翻门卡终态（协议无 gate_close 事件；教师端 updateApprovalStatus 同款）——
     * 只翻这一扇（gate_id 就在手上；无门目标的全翻是两门并开误伤源） */
    const statusMap = { approve: 'approved', reject: 'rejected', abandon: 'abandoned' } as const
    applyStream({
      ...streamRef.current,
      items: updateApprovalStatus(streamRef.current.items, statusMap[decision], { gateId, draftId }),
    })
    postStream('/api/agent/chat/langgraph/resume', {
      message: decision === 'approve' ? 'approved' : feedback || '',
      conversationId: cidRef.current,
      persona,
      studentId,
      resume: true,
      decision: { action: decision, feedback: decision === 'reject' ? feedback : undefined, draft_id: draftId, gate_id: gateId },
    })
      .then((resp) =>
        streamAgent(resp, {
          onEvent: (evt: AgentEvent) => {
            if (ctxRef.current) {
              applyStream(reduceAgentEvent(streamRef.current, evt, ctxRef.current))
            }
          },
          onDone: () => {
            setStreaming(false)
            setPhase('')
          },
          onError: (m) => {
            setError(m)
            setStreaming(false)
          },
        }),
      )
      .catch((e: Error) => {
        setError(e.message || '操作失败，请重试')
        setStreaming(false)
      })
  }, [streaming, persona, studentId])

  /* 队列出口：流结束（done/error 两路都会 setStreaming(false)）把记下的决定
   * 发出去，恰好一次 —— 先清再发（对齐教师端 flush effect 的形状）。 */
  const decideGateRef = useRef(decideGate)
  decideGateRef.current = decideGate
  useEffect(() => {
    if (streaming) return
    const q = queuedGateRef.current
    if (!q) return
    queuedGateRef.current = null
    decideGateRef.current(q.decision, q.gateId, q.draftId, q.feedback)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [streaming])

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* 消息区 */}
      <div ref={bodyRef} className="flex-1 min-h-0 overflow-y-auto px-3.5 pt-3 flex flex-col gap-2">
        {items.length === 0 && intro}
        {items.map((m) => (
          <MobileChatItemView key={m.id} item={m} persona={persona} onDecide={decideGate} streaming={streaming}
            onOpenArtifact={(id) => navigate(`/m/artifact/${id}`)} />
        ))}
        {streaming && (
          <div className="self-start flex items-center gap-1.5 px-3.5 py-2.5 bg-surface border border-teal/30 rounded-[20px] rounded-bl-[6px]">
            <span className="w-1.5 h-1.5 rounded-full bg-ink-muted animate-bounce [animation-delay:0ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-ink-muted animate-bounce [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-ink-muted animate-bounce [animation-delay:300ms]" />
            {phase && <span className="text-[12px] text-ink-muted ml-1">{phase}</span>}
          </div>
        )}
        {error && (
          <div className="self-center text-[12px] text-error bg-error-soft rounded-full px-3 py-1">
            {error}
          </div>
        )}
      </div>

      {/* 快捷提示 chips */}
      {quickPrompts.length > 0 && (
        <div className="shrink-0 flex gap-2 px-3.5 py-2 overflow-x-auto [scrollbar-width:none]">
          {quickPrompts.map((q) => (
            <button
              key={q}
              onClick={() => send(q)}
              disabled={streaming}
              className="shrink-0 px-3.5 py-1.5 rounded-full border border-border bg-surface text-[12.5px] text-ink-secondary active:bg-primary-soft active:text-primary active:border-primary transition-colors disabled:opacity-40"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* 输入区 */}
      <div
        className={`shrink-0 flex gap-2 px-3.5 pt-2 bg-bg border-t border-border/60 ${
          footerPadding ? 'pb-[max(10px,env(safe-area-inset-bottom))]' : 'pb-2.5'
        }`}
      >
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') send()
          }}
          placeholder={placeholder}
          disabled={streaming}
          className="flex-1 min-w-0 h-11 px-4 rounded-full border-[1.5px] border-border bg-surface-muted text-[14px] text-ink placeholder:text-ink-muted outline-none focus:border-primary transition-colors disabled:opacity-50"
        />
        <button
          onClick={() => send()}
          disabled={streaming || !input.trim()}
          className="w-11 h-11 shrink-0 rounded-full bg-primary text-on-primary flex items-center justify-center active:scale-90 transition-transform disabled:opacity-40"
          aria-label="发送"
        >
          <Send size={17} />
        </button>
      </div>
    </div>
  )
}

/* ── ChatItem 渲染投影（移动端裁剪层）：工具=胶囊、approval=只读、plan=折叠卡 ── */

function MobileChatItemView({
  item,
  persona = 'student',
  onDecide,
  streaming = false,
  onOpenArtifact,
}: {
  item: ChatItem
  persona?: 'student' | 'parent'
  onDecide?: (decision: 'approve' | 'reject' | 'abandon', gateId: string, draftId?: string) => void
  streaming?: boolean
  onOpenArtifact: (artifactId: string) => void
}) {
  const [planOpen, setPlanOpen] = useState(false)

  switch (item.kind) {
    case 'user':
      return (
        <div className="self-end max-w-[84%] px-3.5 py-2 bg-primary text-on-primary text-[14px] leading-relaxed rounded-[20px] rounded-br-[6px] break-words whitespace-pre-wrap">
          {item.text}
        </div>
      )
    case 'ai':
      return (
        <div className="self-start max-w-[88%] px-3.5 py-2 bg-surface border border-teal/30 text-[14px] leading-relaxed rounded-[20px] rounded-bl-[6px] break-words shadow-[var(--shadow-sm)]">
          <ChemMd text={item.text} className="!text-[color:var(--color-ink)]" />
        </div>
      )
    case 'think':
      return (
        <div className="self-start max-w-[88%] text-[11.5px] text-ink-muted italic truncate px-1">
          思考：{item.text}
        </div>
      )
    case 'tool':
      return (
        <div className="self-center max-w-[90%] flex items-center gap-1.5 text-[11.5px] text-ink-muted bg-surface-muted/70 border border-border/50 rounded-full px-3 py-1">
          <Sparkles size={12} className={item.card.status === 'done' ? 'text-success' : item.card.status === 'error' ? 'text-error' : 'text-teal'} />
          {toolLabel(item.card.tool)}
          {item.card.status === 'running' ? ' · 处理中' : item.card.status === 'paused' ? ' · 已暂停' : item.card.status === 'error' ? ' · 失败' : ' · 完成'}
        </div>
      )
    case 'artifact': {
      const Icon = ARTIFACT_ICONS[item.artKind] || FileText
      return (
        <button
          onClick={() => onOpenArtifact(item.artifact_id)}
          className="self-start max-w-[88%] w-full flex items-center gap-2.5 px-3 py-2.5 bg-surface border border-teal/40 rounded-[14px] active:bg-surface-muted transition-colors text-left"
        >
          <span className="w-8 h-8 rounded-[10px] bg-teal-soft flex items-center justify-center shrink-0">
            <Icon size={15} className="text-teal" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sub font-medium text-ink truncate">{item.title || 'AI 工件'}</span>
            <span className="block text-micro text-ink-muted">老师生成的{labelArtifact(item.artKind)} · 点击查看</span>
          </span>
        </button>
      )
    }
    case 'approval':
      /* 学生对话不产生确认门（学生 persona 无开门工具）——防御性只读提示。
       * 家长对话的 draft 门 = 派任务确认（REQ-PAT-002）：三值决定卡，
       * 与学生两值/教师端ApprovalCard 隔离（门实体挂在各自会话上）。 */
      if (persona === 'parent' && item.approval.status === 'pending' && onDecide) {
        return (
          <ParentGateCard
            text={item.approval.text}
            gateId={item.approval.gate_id || ''}
            draftId={item.approval.draft_id}
            busy={streaming}
            onDecide={onDecide}
          />
        )
      }
      return (
        <div className="self-start max-w-[88%] flex items-start gap-2 px-3 py-2.5 bg-warning-soft border border-warning/40 rounded-[14px]">
          <ShieldAlert size={15} className="text-warning shrink-0 mt-0.5" />
          <div className="text-[12.5px] leading-relaxed text-ink-secondary">
            {item.approval.text || '请确认操作'}
            <span className="block text-micro text-ink-muted mt-0.5">老师正在处理一个确认步骤</span>
          </div>
        </div>
      )
    case 'plan': {
      const done = item.plan.steps.filter((s) => s.status === 'completed').length
      return (
        <div className="self-start max-w-[88%] w-full px-3 py-2.5 bg-surface border border-border/60 rounded-[14px]">
          <button onClick={() => setPlanOpen((v) => !v)} className="w-full flex items-center gap-2 text-left cursor-pointer">
            <ListChecks size={15} className="text-teal shrink-0" />
            <span className="flex-1 min-w-0 text-sub font-medium text-ink truncate">{item.plan.goal || '任务计划'}</span>
            <span className="text-micro text-ink-muted shrink-0">{done}/{item.plan.steps.length || item.plan.total_steps} 步</span>
            {planOpen ? <ChevronUp size={14} className="text-ink-muted" /> : <ChevronDown size={14} className="text-ink-muted" />}
          </button>
          {planOpen && (
            <ul className="mt-2 space-y-1 border-t border-border/50 pt-2">
              {item.plan.steps.map((s) => (
                <li key={s.step} className="flex items-center gap-2 text-[12px] text-ink-secondary">
                  <span className={s.status === 'completed' ? 'text-success' : s.status === 'failed' ? 'text-error' : 'text-ink-muted'}>
                    {s.status === 'completed' ? '✓' : s.status === 'failed' ? '✕' : '·'}
                  </span>
                  <span className="truncate">{s.description || s.skill}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )
    }
    case 'panel': {
      /* 教师 inline panels 不进移动端；component 事件里带 report/summary 的降级为文本行 */
      const p = (item.params || {}) as { report?: unknown; summary?: string }
      const msg = p.report ? '已生成学习报告' : p.summary || ''
      if (!msg) return null
      return (
        <div className="self-start max-w-[88%] text-[12.5px] text-ink-muted px-1">{msg}</div>
      )
    }
    case 'system':
      return <div className="self-center text-[11.5px] text-ink-muted bg-surface-muted/60 rounded-full px-3 py-1">{item.text}</div>
    default:
      return null
  }
}

function labelArtifact(kind: string): string {
  const map: Record<string, string> = {
    animation: '教学动画',
    slides: '课件',
    doc: '文档',
    structure: '结构图',
    table: '数据表',
  }
  return map[kind] || '工件'
}

/* 家长派任务确认卡（REQ-PAT-002）：三值决定，批准后系统重放发送 */
function ParentGateCard({
  text, gateId, draftId, busy, onDecide,
}: {
  text: string
  gateId: string
  draftId?: string
  busy: boolean
  onDecide: (d: 'approve' | 'reject' | 'abandon', gateId: string, draftId?: string, feedback?: string) => void
}) {
  const [rejecting, setRejecting] = useState(false)
  const [approving, setApproving] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [confirmAbandon, setConfirmAbandon] = useState(false)
  return (
    <div className="self-start max-w-[88%] w-full px-3.5 py-3 bg-surface border-2 border-teal/40 rounded-[14px]">
      <div className="flex items-start gap-2">
        <ShieldAlert size={15} className="text-teal shrink-0 mt-0.5" />
        <div className="text-[12.5px] leading-relaxed text-ink flex-1">{text || '确认给孩子派发任务？'}</div>
      </div>
      {rejecting && (
        <input
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder="打回意见（会转给生成方按意见修订）"
          className="mt-2.5 w-full h-9 px-3 rounded-lg border-[1.5px] border-warning/40 bg-warning-soft/60 text-[12.5px] text-ink outline-none focus:border-warning/50"
        />
      )}
      <div className="mt-2.5 flex gap-2">
        {rejecting ? (
          <button
            disabled={busy || !feedback.trim()}
            onClick={() => onDecide('reject', gateId, draftId, feedback)}
            className="flex-1 py-2 rounded-full bg-warning text-white text-[12.5px] font-bold disabled:opacity-40"
          >
            提交打回
          </button>
        ) : approving ? (
          /* R-I5：批准不可逆 → 行内两段式（原位展开确认） */
          <>
            <button
              disabled={busy}
              onClick={() => { setApproving(false); setConfirmAbandon(false) }}
              className="flex-1 py-2 rounded-full border border-border text-ink-secondary text-[12.5px] font-medium disabled:opacity-40"
            >
              取消
            </button>
            <button
              disabled={busy}
              onClick={() => onDecide('approve', gateId, draftId)}
              className="flex-1 py-2 rounded-full bg-primary text-on-primary text-[12.5px] font-bold disabled:opacity-40"
            >
              确认批准发送
            </button>
          </>
        ) : (
          <button
            disabled={busy}
            onClick={() => setApproving(true)}
            className="flex-1 py-2 rounded-full bg-primary text-on-primary text-[12.5px] font-bold disabled:opacity-40"
          >
            批准
          </button>
        )}
        {!rejecting && (
          <button
            disabled={busy}
            onClick={() => { setRejecting(true); setApproving(false) }}
            className="flex-1 py-2 rounded-full border border-warning/40 bg-warning-soft text-warning text-[12.5px] font-medium disabled:opacity-40"
          >
            打回
          </button>
        )}
        {!rejecting && (
          <button
            disabled={busy}
            onClick={() => {
              /* 放弃不可逆：二次确认（审查修正） */
              if (confirmAbandon) onDecide('abandon', gateId, draftId)
              else { setConfirmAbandon(true); setApproving(false) }
            }}
            className="flex-1 py-2 rounded-full border border-border bg-surface text-ink-muted text-[12.5px] disabled:opacity-40"
          >
            {confirmAbandon ? '确认放弃？' : '放弃'}
          </button>
        )}
      </div>
    </div>
  )
}
