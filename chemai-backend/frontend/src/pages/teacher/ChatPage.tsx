import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { MessageSquarePlus, MessagesSquare, Paperclip, Send as SendIcon, Trash2, X, FileStack, FolderOpen, FolderPlus, FolderUp, PanelRightOpen } from 'lucide-react'
import { Input as AntInput, Modal as AntModal } from 'antd'
import { api, ApiError, getTeacherId } from '../../lib/api'
import { postStream, streamAgent, type AgentEvent, type ContextEvent } from '../../lib/agent-sse'
import { createStreamState, reduceAgentEvent, seedNewTurn, updateApprovalStatus, normalizePlanStepStatus, syncPlanToolSteps, type StreamState, type PlanStepStatus, type ApprovalStatus } from '../../lib/agent-stream-reducer'
import { ArtifactPanel, dedupeArtifacts, type PanelFocus, type ArtifactRef, type Attachment } from '../../components/chat/ArtifactPanel'
import { parseSlashInput, activeAtQuery, applyRef, filterRefs, type RefItem } from '../../lib/teaching-skills'
import type { GateDecision } from '../../components/chat/ApprovalCard'
import type { OrbState } from 'thinking-orbs'
import { formatChemicalText } from '../../lib/chem-format'
import { useLiveTimer } from '../../lib/use-live-timer'
import { Button, useToast } from '../../components/ui'
import { MessageList, type ChatItem } from '../../components/chat/MessageList'
import type { ToolCardData } from '../../components/chat/ToolCard'
import { SuggestionGroup } from '../../components/chat/SuggestionGroup'
import { ContextMeter } from '../../components/chat/ContextMeter'
import { estimateContextFromHistory } from '../../lib/context-meter'
import { gateChipLabel } from '../../lib/gate-labels'
import {
  decrementPendingCount,
  peekPendingStore,
  refetchPendingCount,
  usePendingCount,
} from '../../lib/use-pending-count'
import { refetchInDoubt, useInDoubt } from '../../lib/use-in-doubt'

interface Conversation {
  thread_id: string
  title: string
  preview?: string
  last_at?: string
  message_count?: number
}

let uid = 0
const nid = () => `m${Date.now()}_${uid++}`

/** Agent 工作台（原对话工作台）· Phase 1 迁移（原 index.html + js/agent.js） */
/** 账本里的决定 → 卡片终态。`superseded` / `expired` 对老师都是「这张卡不必再看了」，
 *  统一落到 cancelled 的静态态上，不新增第五种说法。 */
const CLOSED_GATE_STATUS: Record<string, ApprovalStatus> = {
  approved: 'approved',
  rejected: 'rejected',
  cancelled: 'cancelled',
  superseded: 'cancelled',
  expired: 'cancelled',
}

/* 终态小票标签收拢到 lib/gate-labels（与 ApprovalCard 活卡翻面同一份）：
 * closedGateLabel(cg) === gateChipLabel(cg.message, cg.tool_name)。 */

/** 流式期间按下的门决定（等本轮结束再发）。同一时刻只留一条：后来的覆盖先前。 */
type QueuedDecision = {
  decision: GateDecision
  feedback?: string
  draftId?: string
  gateId?: string
}

/** 历史快照比 live 流旧时，别把屏幕上已经渲染出来的待批卡抹掉。
 *  `/history` 是整体替换（`applyMessages(items)` + `streamRef.state = createStreamState()`），
 *  而那次快照可能是门行提交之前拍的（实测：门建好 9.01s 后才出现在页面上，只有 F5 才回来）。
 *  规则：**历史为准**，只补回历史里完全没有的 pending 卡（按 gate_id 判，老布尔门退用 draft_id）；
 *  历史已经给出终态（closed_gates / 终态卡）的门一律以历史为准 —— 批过的门不许再画回可点。 */
function keepLivePendingGates(historyItems: ChatItem[], liveItems: ChatItem[]): ChatItem[] {
  const key = (a: { gate_id?: string; draft_id?: string }) => a.gate_id || a.draft_id || ''
  const known = new Set(
    historyItems.flatMap((it) => (it.kind === 'approval' ? [key(it.approval)] : [])),
  )
  const rescued = liveItems.filter(
    (it) =>
      it.kind === 'approval' &&
      it.approval.status === 'pending' &&
      !known.has(key(it.approval)),
  )
  return rescued.length ? [...historyItems, ...rescued] : historyItems
}

/** 给待批卡挂一句可见的「已记录「X」」（不动 status：门仍是 pending，流结束那次决定
 *  照旧经 updateApprovalStatus 落到 approved / rejected / abandoned）。
 *  后来的决定覆盖先前那条、卡片文字跟着换 —— 老师永远看得见将要执行的是哪一个，
 *  不会出现「点了但屏幕上什么都没变」。 */
function markDecisionQueued(
  items: ChatItem[],
  decision: GateDecision,
  gate: { draftId?: string; gateId?: string },
): ChatItem[] {
  const hit = (it: ChatItem) => {
    if (it.kind !== 'approval') return false
    if (it.approval.status !== 'pending') return false
    if (!gate.gateId && !gate.draftId) return true
    if (gate.gateId && it.approval.gate_id === gate.gateId) return true
    if (!gate.gateId && gate.draftId && it.approval.draft_id === gate.draftId) return true
    return false
  }
  return items.map((it) =>
    it.kind === 'approval' && hit(it) ? { ...it, approval: { ...it.approval, queued: decision } } : it,
  )
}

export function ChatPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const toast = useToast()
  /** 顶栏「待确认」按钮上的那个数字（与队列页共用同一份 store，不各拉各的）。 */
  const { count: pendingCount } = usePendingCount()
  /* 「结果未知」是**另一个数**，所以另给一颗不同颜色的提示，不并进上面那颗胶囊：
     那颗数的是「在等你决定的门」，这颗数的是「在等你裁定的门」。并成一个的话，
     老师看见「3」点进去可能只找到 1 扇待确认的（另 2 扇是待裁定的）。
     它尤其需要这点提示 —— 成因是进程崩在重放中途，老师不会主动去找它。 */
  const { count: inDoubtCount } = useInDoubt()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  /* 带 `?thread=` 进来时（待确认队列的「打开」与「查看执行 →」都走这条，T21），
     取不到历史**不**回退到列表第一条，而是记下这个 id 让人话空态接管：
     老师点的是王梓涵那扇门，落到不相干的对话里比报错更难查。 */
  const [unreachable, setUnreachable] = useState('')
  /** 会话列表到手了没 —— 地址栏效应要等它，否则"这条会话不在了"会在列表还没回来时误判。 */
  const [listLoaded, setListLoaded] = useState(false)
  const [messages, setMessages] = useState<ChatItem[]>([])
  const [streaming, setStreaming] = useState(false)
  const [phase, setPhase] = useState('')
  // thinking-orbs 动画状态（thinking→breathing / 执行中→working / reply→composing）
  const [orb, setOrb] = useState<OrbState | ''>('')
  const [input, setInput] = useState('')
  // 上下文占用(SSE context 事件更新;历史加载用启发式估算占位)
  const [ctxInfo, setCtxInfo] = useState<ContextEvent | null>(null)
  const [fileAccess, setFileAccess] = useState<'off' | 'workspace' | 'full'>('workspace')
  const [workspacePath, setWorkspacePath] = useState(
    () => localStorage.getItem('chemai_workspace') || '',
  )
  // 工作区选择器（服务器端目录浏览）
  const [wsPickerOpen, setWsPickerOpen] = useState(false)
  const [wsCurrent, setWsCurrent] = useState('')
  const [wsParent, setWsParent] = useState<string | null>(null)
  const [wsDirs, setWsDirs] = useState<string[]>([])
  const [wsNewFolder, setWsNewFolder] = useState('')
  const [wsDefault, setWsDefault] = useState('')
  const [convOpen, setConvOpen] = useState(true)  // 对话列表默认展开，可折叠
  /* 工件面板（批次 A2）：右侧可收缩三栏之一；focus 驱动联动聚焦
   * （消息流工件卡/上传卡点击 → 对应 tab），artifacts/attachments 由
   * SSE 工件事件与上传动作实时追加，切会话即清（面板是会话内视野）。 */
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelFocus, setPanelFocus] = useState<PanelFocus | null>(null)
  const [panelArtifacts, setPanelArtifacts] = useState<ArtifactRef[]>([])
  const [panelAttachments, setPanelAttachments] = useState<Attachment[]>([])
  /* @ 引用（批次 A4）：光标前有进行中的 @ 片段时弹层列出本会话的附件/工件，
   * 选中插入结构化 token（[upload_id=…]/[artifact_id=…]，替换裸拼接）。 */
  const [caret, setCaret] = useState(0)
  const atQuery = activeAtQuery(input, caret)
  const refItems: RefItem[] = [
    ...panelAttachments.map((a) => ({
      id: a.upload_id, kind: 'attachment' as const, label: a.file_name,
      token: `[upload_id=${a.upload_id}]`,
    })),
    ...dedupeArtifacts(panelArtifacts).map((a) => ({
      id: a.artifact_id, kind: 'artifact' as const, label: a.title,
      token: `[artifact_id=${a.artifact_id}]`,
    })),
  ]
  const atMatches = atQuery === null ? [] : filterRefs(atQuery, refItems)

  function trackCaret(el: HTMLTextAreaElement) {
    setCaret(el.selectionStart ?? el.value.length)
  }

  function insertRef(item: RefItem) {
    const r = applyRef(input, caret, item.token)
    setInput(r.text)
    setPanelFocus({ tab: item.kind === 'attachment' ? 'attachments' : 'artifact', targetId: item.id })
    requestAnimationFrame(() => {
      inputRef.current?.focus()
      inputRef.current?.setSelectionRange(r.caret, r.caret)
      setCaret(r.caret)
    })
  }
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // 旧版 live-timer：流式期间每 100ms 跳动（1 位小数秒），显示在气泡思考状态内
  const elapsed = useLiveTimer(streaming)

  // 消息列表同步引用（发送消息时需读取当前完整列表种入 reducer 状态，避免旧消息丢失）
  const messagesRef = useRef<ChatItem[]>([])
  const applyMessages = useCallback((items: ChatItem[]) => {
    messagesRef.current = items
    setMessages(items)
  }, [])
  const appendMessage = useCallback((item: ChatItem) => {
    messagesRef.current = [...messagesRef.current, item]
    setMessages(messagesRef.current)
  }, [])

  // 流式会话状态（避免闭包过期）：SSE 事件经纯函数归约器（agent-stream-reducer）处理
  const streamRef = useRef<{
    state: StreamState
    history: { role: string; content: string }[]
  }>({ state: createStreamState(), history: [] })

  const streamingRef = useRef(false)
  streamingRef.current = streaming
  /* 流式期间按下的决定不丢弃，记在这里等本轮结束再发（出口见下方 flush effect）。
   * 架构候选 C：队列同步落 sessionStorage（按会话键控）——流式中排队后 F5/切页
   * 队列不丢，那句「已记录「X」：本轮结束后自动确认」的承诺不因刷新失效。 */
  const queuedDecisionRef = useRef<QueuedDecision | null>(null)
  const queueKey = activeId ? `chemai:queued-decision:${activeId}` : ''

  function persistQueue(q: QueuedDecision | null) {
    if (!queueKey) return
    try {
      if (q) sessionStorage.setItem(queueKey, JSON.stringify(q))
      else sessionStorage.removeItem(queueKey)
    } catch { /* 隐私模式等存不进去：队列退化为仅内存（原有行为） */ }
  }
  function restoreQueue(): QueuedDecision | null {
    if (!queueKey) return null
    try {
      const raw = sessionStorage.getItem(queueKey)
      return raw ? (JSON.parse(raw) as QueuedDecision) : null
    } catch { return null }
  }

  /* ── 对话列表 ── */
  const loadConversations = useCallback(async (target = '', autoSelect = false) => {
    try {
      const d = await api.get<{ conversations: Conversation[] }>(
        '/api/agent/chat/conversations?prefix=t-,c',
      )
      const list = d.conversations || []
      setConversations(list)
      setListLoaded(true)
      if (target) {
        // 指定的会话可能不在列表里（列表只取 t-/c 前缀），按 thread_id 直接进；
        // 列表一起交给 loadHistory 判"这条还在不在"。
        setActiveId(target)
        await loadHistory(target, list)
      } else if (list.length && (autoSelect || !activeId)) {
        // 带 `?thread=` 进来时**不**自动选第一条（T21）：那一行由上面的分支负责。
        // 要不要自动选由调用方说，不看 `activeId` —— 调用方常常在同一个 tick 里刚
        // `setActiveId(null)`，闭包读到的还是旧值，"回到我的对话"就会停在空页上。
        const best = list[0]
        setActiveId(best.thread_id)
        loadHistory(best.thread_id)
      }
    } catch {
      /* 服务端不可用：静默 */
    }
  }, [activeId])

  /* 历史加载的序号：地址栏在流式回合还没结束时也会触发加载（从待确认队列再跳一行、
     浏览器后退），两次 await 谁后完成谁说了算 —— 圆环于是会挂着另一条会话的百分比，
     还会把流式那次的精确锚点降级成启发式估算。只认最后一次发起的那份响应。 */
  const historySeq = useRef(0)

  const loadHistory = useCallback(async (threadId: string, knownList?: Conversation[]) => {
    const seq = ++historySeq.current
    setUnreachable('')
    setCtxInfo(null)  // 加载中不保留上一条会话的占用数
    try {
      const d = await api.get<{
        messages: ({ type?: string; role?: string; name?: string; status?: string; content?: string; data?: unknown; raw?: string })[]
        run?: { run_id: string; goal: string; steps: { step: number; skill: string; description: string; status: PlanStepStatus }[] } | null
        gate?: { draft_id: string; student_id: string; version: number; action: string; message: string; gate_id?: string } | null
        /* 已处置的门（已批/已弃）：终态卡的数据来源。取账本，与上面那张待批卡同源不同态。 */
        closed_gates?: {
          gate_id: string; decision: string; actor_user_id?: string; kind?: string
          tool_name?: string; action?: string; message?: string; decided_at?: string | null
        }[]
        context_window?: number
      }>(`/api/agent/chat/history/${threadId}`)
      // 2026-08 记录恢复：历史含完整结构（user/ai/tool），重开对话后
      // 工具卡与结果（图表/文件内容）完整还原，不再只剩文本。
      let items: ChatItem[] = (d.messages || []).flatMap((m) => {
        if (m.type === 'tool') {
          const card: ToolCardData = {
            tool: m.name || '',
            status: m.status === 'error' ? 'error' : 'done',
            data: (m.data as ToolCardData['data']) ?? (m.raw ? { raw: m.raw } : undefined),
          }
          const out: ChatItem[] = [{ id: nid(), kind: 'tool', card } as ChatItem]
          // 工件引用随 tool 结果入库（批次 A1）：刷新后工件卡照在——
          // 「上次那个动画找不到了」正是工件库要治的痛，历史恢复不能掉队
          const data = m.data as { artifact?: { artifact_id?: string; kind?: string; title?: string; version_seq?: number; url?: string } } | undefined
          if (data && typeof data === 'object' && data.artifact && data.artifact.artifact_id) {
            const a = data.artifact
            out.push({
              id: nid(), kind: 'artifact',
              artifact_id: a.artifact_id || '', artKind: a.kind || '',
              title: a.title || '', versionSeq: a.version_seq || 1,
              url: a.url || '',
            } as ChatItem)
          }
          return out
        }
        // 旧协议 {role, content} 兼容
        const role = m.type ?? (m.role === 'user' || m.role === 'human' ? 'user' : 'ai')
        const text = m.content || ''
        if (role === 'user') return [{ id: nid(), kind: 'user', text } as ChatItem]
        if (!text.trim()) return [] // 空气泡不复现
        return [{ id: nid(), kind: 'ai', text, streaming: false } as ChatItem]
      })
      // 2026-08 DSH 式：工具调用前的 AI 文本在历史里同样折叠为思考行
      for (let i = 0; i < items.length - 1; i++) {
        if (items[i].kind === 'ai' && items[i + 1].kind === 'tool') {
          items[i] = { ...items[i], kind: 'think' } as ChatItem
        }
      }
      // ADR-0013 重同步：刷新/重开后 todolist 与待确认门不丢
      if (d.run && Array.isArray(d.run.steps) && d.run.steps.length > 0) {
        const plan = {
          goal: d.run.goal,
          total_steps: d.run.steps.length,
          steps: d.run.steps.map((s) => ({ ...s, status: normalizePlanStepStatus(s.status) })),
        }
        items = syncPlanToolSteps(items, plan)
        items.push({
          id: nid(),
          kind: 'plan',
          plan,
        } as ChatItem)
      }
      /* 终态卡（已批/已弃）：刷新后要看得见之前那几扇门被怎么处置的。
         与下面那张待批卡同源（都来自后端的门投影），只是状态已经是终态；
         渲染复用 ApprovalCard 的静态态，不另造一套卡。 */
      for (const cg of d.closed_gates || []) {
        items.push({
          id: nid(),
          kind: 'approval',
          approval: {
            /* 带上「批/弃的是什么」：账本倒序摞在对话尾部，只渲染固定短语的话
               三次批准就是三条一模一样的「已批准执行」，老师分不出哪条对应哪次
               （2026-10-26 三端实测用户当场指出）。草稿门从 message 提取
               「学习计划（第 1 版）」式短标签；布尔门 message 是通用文案，
               按 tool_name 查短标签表。 */
            text: gateChipLabel(cg.message, cg.tool_name),
            status: CLOSED_GATE_STATUS[cg.decision] || 'cancelled',
            action: cg.action || '',
            gate_id: cg.gate_id,
          },
        } as ChatItem)
      }
      /* 历史恢复认两种身份，任一即可（`gate_id` 才是决定与重放的绑定键）：
         只认 `draft_id` 会把布尔门（执行命令 / 读写文件）全丢掉 —— 待确认队列把
         它们列进来了、行首还写着「打开 →」，点进来却没有控件，那扇门就成了
         只能永远 open 的死路（实测：`/history` 明明下发 gate_id + message）。 */
      if (d.gate && (d.gate.draft_id || d.gate.gate_id)) {
        items.push({
          id: nid(),
          kind: 'approval',
          approval: {
            text: d.gate.message || '请确认操作',
            status: 'pending',
            // ADR-0015：按钮文案由门自己的数据渲染——历史恢复不得默认 send_student
            //（曾把家长门渲染成「发送给学生」）
            action: d.gate.action || '',
            draft_id: d.gate.draft_id,
            version: d.gate.version,
            gate_id: d.gate.gate_id,
          },
        } as ChatItem)
      }
      /* 比这一份响应更晚的那次加载已经发起：这一份的结论（消息、圆环、流式基线）全部作废，
         不然两条会话会叠在同一个页面上。 */
      if (seq !== historySeq.current) return
      /* 与屏幕上的 live 卡对账后再落盘：这次快照早于门行提交时，不能把刚渲出来的卡覆盖掉。 */
      items = keepLivePendingGates(items, messagesRef.current)
      applyMessages(items)
      /* 「这条会话不在了」的判定要两个条件一起看：历史是空的，而且它不在本人的会话列表里。
         只看 HTTP 状态不行 —— `/history` 对不存在的 `t-` 会话返回 200 空数组
         （`require_owned` 的第三条会把库里还没有的会话当场认领给调用者），
         而一条刚新建、还没说过话的会话同样是空历史，那是正常态。 */
      if (!items.length && knownList && !knownList.some((c) => c.thread_id === threadId)) {
        setUnreachable(threadId)
      }
      /* 窗口只认后端下发的数：估算的是"占了多少"，窗口是"总共多少"，后者后端才有权威。
         拿不到就不显示圆环 —— 显示一个按猜的窗口算出来的百分比，比不显示更糟。 */
      setCtxInfo(d.context_window ? estimateContextFromHistory(items, d.context_window) : null)
      streamRef.current.history = items.flatMap((it): { role: string; content: string }[] =>
        it.kind === 'user'
          ? [{ role: 'user', content: it.text }]
          : it.kind === 'ai'
            ? [{ role: 'assistant', content: it.text }]
            : [],
      )
      streamRef.current.state = { ...createStreamState(), items }  // 与屏幕一致，否则下一个事件会照旧覆盖
    } catch (err) {
      // 403（归属被拒）与 404（会话已删）要说人话；网络故障不该假装会话不存在，
      // 保持原样静默，老师自己会重试。
      if (err instanceof ApiError && (err.status === 403 || err.status === 404)) {
        setUnreachable(threadId)
        applyMessages([])
      }
    }
  }, [applyMessages])

  useEffect(() => {
    const target = searchParams.get('thread') || ''
    loadConversations(target, !target)
    /* 待确认计数的重拉点在这里（D7 第②点）：入口是本页顶栏那颗按钮，
       老师进工作台的那一刻就是该看最新数字的那一刻。侧栏已经不挂这个数了。 */
    void refetchPendingCount()
    /* 「结果未知」独立于待确认列表：本页顶栏那颗提示自己取数，
       不能等老师先造访过待确认页（否则罕见态永远不露头）。 */
    void refetchInDoubt()
    // 上传卡操作事件
    const onSend = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail
      if (detail) sendMessage(detail)
    }
    window.addEventListener('chemai:send', onSend)
    return () => window.removeEventListener('chemai:send', onSend)
  }, [])

  /* 地址栏是唯一事实来源（T21）：浏览器前进/后退、或从待确认队列再点一行进来，
     都跟着切会话。`selectConversation` 写 URL 时 target === activeId，不会绕回来。
     首次带参数的那一次由 `loadConversations` 负责（它手上有列表，能判"这条还在不在"），
     这里只处理之后的变化 —— 所以列表没到手前必须跳过。 */
  useEffect(() => {
    const target = searchParams.get('thread') || ''
    if (!listLoaded || !target || target === activeId) return
    setActiveId(target)
    resetPanel()  // 浏览器后退/待确认队列直跳也是切会话，面板一并清（批次 A2）
    loadHistory(target, conversations)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, listLoaded])

  /** 会话与地址栏同步：`#/chat?thread=` 就是「当前在哪条对话」的可分享、可刷新形状。
      replace 而不是 push：老师在一串会话间跳着看门，不该靠后退键往回爬。 */
  function goThread(id: string) {
    setSearchParams(id ? { thread: id } : {}, { replace: true })
  }

  /** 「回到我的对话」：地址栏清空后允许走回列表第一条 —— 这是老师要的，不是系统猜的。
      自动选第一条必须由这里明说，不能靠 `loadConversations` 读 `activeId`：同一个 tick 里
      闭包拿到的还是刚被清掉的那个 id，结果是谁都不选（实测踩过，屏幕停在空页）。 */
  /* 面板是会话内视野（批次 A2）：所有离开当前会话的路径都清空面板状态。
   * 折叠收起只动 panelOpen，不清内容——收起来再展开还要看得见。 */
  const resetPanel = useCallback(() => {
    setPanelArtifacts([])
    setPanelAttachments([])
    setPanelFocus(null)
  }, [])

  function backToMyConversations() {
    setUnreachable('')
    setActiveId(null)
    applyMessages([])
    setCtxInfo(null)  // 只清消息会留下上一条会话的占用百分比，那也是个会被照着决策的数
    resetPanel()
    goThread('')
    loadConversations('', true)
  }

  async function selectConversation(id: string) {
    if (streamingRef.current) return
    setActiveId(id)
    goThread(id)
    setConvOpen(false)
    resetPanel()
    await loadHistory(id)
  }

  async function newConversation() {
    if (streamingRef.current) return
    try {
      const d = await api.post<{ thread_id: string }>('/api/agent/chat/new?prefix=t-')
      const id = d.thread_id
      setActiveId(id)
      goThread(id)
      setUnreachable('')
      applyMessages([])
      setCtxInfo(null)
      resetPanel()
      streamRef.current.history = []
      streamRef.current.state = createStreamState()
      setConversations((prev) => [
        { thread_id: id, title: '新对话', last_at: new Date().toISOString() },
        ...prev.filter((c) => c.thread_id !== id),
      ])
    } catch (e) {
      toast.error(`新建对话失败: ${e instanceof Error ? e.message : ''}`)
    }
  }

  async function deleteConversation(id: string) {
    if (streamingRef.current) return
    try {
      await api.del(`/api/agent/chat/conversations/${id}`)
    } catch {
      /* 后端删除失败也本地移除 */
    }
    const rest = conversations.filter((c) => c.thread_id !== id)
    setConversations(rest)
    if (activeId === id) {
      setActiveId(null)
      goThread('')
      setUnreachable('')
      applyMessages([])
      setCtxInfo(null)
      resetPanel()
      streamRef.current.history = []
    }
  }

  /* ── 流式对话（事件经纯函数归约器处理，行为由 evals 回归测试锁定） ── */
  const handleEvent = useCallback(
    (evt: AgentEvent) => {
      const s = streamRef.current
      // 上下文占用是独立 UI 状态,不进消息 reducer
      if (evt.type === 'context') {
        setCtxInfo(evt)
        return
      }
      // 工件事件（批次 A2）：卡片照常进消息流，同时收进面板并自动聚焦
      if (evt.type === 'artifact') {
        const ref: ArtifactRef = {
          artifact_id: evt.artifact_id, kind: evt.kind,
          title: evt.title, version_seq: evt.version_seq, url: evt.url,
        }
        setPanelArtifacts((prev) =>
          prev.some((a) => a.artifact_id === ref.artifact_id && a.version_seq === ref.version_seq)
            ? prev : [...prev, ref])
        setPanelOpen(true)
        setPanelFocus({ tab: 'artifact', targetId: ref.artifact_id })
      }
      const next = reduceAgentEvent(s.state, evt, {
        newId: nid,
        onNavigate: (path, params) => {
          // 旧版行为：navigate 参数（focus 学号 / action）经 sessionStorage 交给目标页
          // 消费（如学生页自动打开李世睿抽屉并生成学习方案）
          if (params && Object.keys(params).length > 0) {
            sessionStorage.setItem('chemai_navigate_params', JSON.stringify(params))
          }
          navigate(path)
        },
      })
      s.state = next
      applyMessages(next.items)
      setPhase(next.phase)
      setOrb(next.orb)
    },
    [navigate, applyMessages],
  )

  async function runStream(resp: Response) {
    await streamAgent(resp, {
      onEvent: handleEvent,
      onDone: () => {
        setStreaming(false)
        setPhase('')
        setOrb('')
        const s = streamRef.current
        s.state = {
          ...s.state,
          aiId: null,
          // 2026-08：流结束兜底清除空 AI 气泡（白块不复现），
          // 思考行与工具卡保留
          items: s.state.items
            .map((it) =>
              it.kind === 'ai' && it.streaming ? { ...it, streaming: false } : it,
            )
            .filter((it) => !(it.kind === 'ai' && !it.text.trim())),
        }
        applyMessages(s.state.items)
        /* 2026-10-20：本轮**没拿到门卡**时，回库里问一次有没有待批的门。
           「正常完成 = SSE 已经把该给的都给了」对门不成立：门的真源是库里的门行
           （`agent_gates.status='open'`），`gate_open` 只是实时加速。事件丢了、或者门在
           上一轮就开着，前端手里就没有卡 = 老师点不到 = 死路（A5）。
           条件不能去掉：无条件重拉会在每一轮都拉一次整份历史并重建整个列表
           （长会话是几十KB + 全列表重挂载），代价落在最常见的路径上。缺卡才是异常态，
           而这个分支恰好只在缺卡时进。onAbnormalEnd 那条管断流，这条管「流读完但结论在库里」 */
        const hasPendingGate = s.state.items.some(
          (it) => it.kind === 'approval' && it.approval.status === 'pending',
        )
        if (activeId && !hasPendingGate) loadHistory(activeId).catch(() => {})
        loadConversations()
      },
      onError: (m) => {
        setStreaming(false)
        setPhase('')
        setOrb('')
        toast.error(m)
      },
      onAbnormalEnd: () => {
        // ADR-0016：流被掐断 ≠ 正常完成——重同步历史（run/门/步状态以 DB 为准）
        setStreaming(false)
        setPhase('')
        setOrb('')
        if (activeId) loadHistory(activeId).catch(() => {})
        toast.info('连接中断，已重新同步会话状态')
      },
    })
  }

  async function sendMessage(text: string) {
    let msg = text.trim()
    if (!msg || streamingRef.current) return

    // 斜杠指令（批次 A4）：/动画 盐类水解 → 调技能的确定性指令；非斜杠原样
    const slash = parseSlashInput(msg)
    if (slash) msg = slash.prompt

    let convId: string = activeId ?? ''
    if (!convId) {
      try {
        const d = await api.post<{ thread_id: string }>('/api/agent/chat/new?prefix=t-')
        convId = d.thread_id
        setActiveId(convId)
        goThread(convId)
        setConversations((prev) => [
          { thread_id: convId, title: '新对话', last_at: new Date().toISOString() },
          ...prev,
        ])
      } catch {
        toast.error('无法创建对话，请检查服务是否启动')
        return
      }
    }

    const s = streamRef.current
    // ADR-0013 确认门通道：待确认卡存在时，老师打字 = 打回+反馈
    // （进确定性修订循环），不再当作全新消息、更不会假批准旧卡
    const pendingGate = messagesRef.current.find(
      (it) => it.kind === 'approval' && it.approval.status === 'pending',
    )
    if (pendingGate && pendingGate.kind === 'approval') {
      const gateDraft = pendingGate.approval.draft_id
      if (gateDraft) {
        applyMessages(updateApprovalStatus(messagesRef.current, 'rejected', { draftId: gateDraft }))
        const seeded2 = seedNewTurn(messagesRef.current, msg, nid, s.state.activePlanId)
        s.state = seeded2.state
        applyMessages(seeded2.items)
        setInput('')
        setStreaming(true)
        setPhase('按意见修订中…')
        setOrb('working')
        try {
          const resp = await postStream('/api/agent/chat/langgraph/resume', {
            message: msg,
            conversationId: convId,
            resume: true,
            file_access: fileAccess,
            workspace: workspacePath,
            decision: { action: 'reject', feedback: msg, draft_id: gateDraft },
          })
          await runStream(resp)
        } catch (e) {
          toast.error(`操作失败: ${e instanceof Error ? e.message : e}`)
          setStreaming(false)
        }
        return
      }
      // 布尔门（无修订能力）：打字 = 作废这次待批，新消息照常流转——
      // 本地翻卡**只翻这一扇**（架构候选 C：无门目标的全翻是两门并开误伤源）
      const voidGate = pendingGate.approval.gate_id
        ? { gateId: pendingGate.approval.gate_id }
        : (pendingGate.approval.draft_id ? { draftId: pendingGate.approval.draft_id } : undefined)
      applyMessages(updateApprovalStatus(messagesRef.current, 'cancelled', voidGate))
    }
    // 以当前完整消息列表（含旧对话）种入 reducer 状态，避免首事件到达时覆盖丢失
    const seeded = seedNewTurn(messagesRef.current, msg, nid, s.state.activePlanId)
    s.state = seeded.state
    applyMessages(seeded.items)

    setInput('')
    const history = s.history.map((h) => ({ role: h.role, content: h.content }))
    s.history.push({ role: 'user', content: msg })

    setStreaming(true)
    setPhase('分析中…')
    setOrb('breathing')

    try {
      const resp = await postStream('/api/agent/chat/langgraph/stream', {
        message: msg,
        conversationId: convId,
        history,
        file_access: fileAccess,
        workspace: workspacePath,
      })
      await runStream(resp)
    } catch (e) {
      setStreaming(false)
      setPhase('')
      const s2 = streamRef.current
      if (s2.state.aiId) {
        const aid = s2.state.aiId
        applyMessages(
          s2.state.items.map((it) =>
            it.kind === 'ai' && it.id === aid ? { ...it, text: `⚠ 请求失败：${e instanceof Error ? e.message : e}`, streaming: false } : it,
          ),
        )
      }
      s2.state = { ...s2.state, aiId: null }
    }
  }

  /** ADR-0013 确认门三值决定：批准（发送+继续）/ 打回（带反馈，修订后再确认）/ 放弃（跳过继续）。
   * ADR-0015：decision 必带 gate_id（一决定一动作）；流式期间禁止重复提交（409 前端防线）。 */
  async function handleDecision(decision: GateDecision, feedback?: string, draftId?: string, gateId?: string) {
    if (streamingRef.current) {
      /* 流式期间按下的决定**不能丢**（2026-10 实测：只弹一句 toast 就 return，后端 0 条
         /resume，老师以为点了没反应，这一轮后续步骤被标 failed、门继续 open）。记进队列，
         本轮结束后自动发出去（见下面的 flush effect）。卡片上那句「已记录「X」」就是回执：
         换个决定再按一次，文字跟着换 —— 屏幕上永远看得见将执行的是哪一个。 */
      queuedDecisionRef.current = { decision, feedback, draftId, gateId }
      persistQueue(queuedDecisionRef.current)
      const s = streamRef.current
      s.state = { ...s.state, items: markDecisionQueued(s.state.items, decision, { draftId, gateId }) }
      applyMessages(markDecisionQueued(messagesRef.current, decision, { draftId, gateId }))
      return
    }
    const statusMap: Record<GateDecision, ApprovalStatus> = {
      approve: 'approved',
      reject: 'rejected',
      abandon: 'abandoned',
    }
    /* 只动这一扇门（gate_id 优先，退而用 draft_id）。两张卡并开时，前一张的决定
       不能把后一张顺手做成终态 —— 那会让卡还在、按钮没了（实测哑门）。 */
    const target = { draftId, gateId }
    const s = streamRef.current
    s.state = { ...s.state, items: updateApprovalStatus(s.state.items, statusMap[decision], target) }
    applyMessages(updateApprovalStatus(messagesRef.current, statusMap[decision], target))
    if (!activeId) return
    if (decision === 'reject') {
      // 打回反馈以用户消息形式入列（老师能看到自己说了什么）
      const seeded = seedNewTurn(messagesRef.current, feedback || '', nid, s.state.activePlanId)
      s.state = seeded.state
      applyMessages(seeded.items)
    }
    setStreaming(true)
    setPhase(decision === 'approve' ? '已批准，继续执行…' : decision === 'reject' ? '按意见修订中…' : '继续执行…')
    setOrb(decision === 'approve' ? 'working' : decision === 'reject' ? 'working' : 'breathing')
    try {
      const resp = await postStream('/api/agent/chat/langgraph/resume', {
        message: decision === 'approve' ? 'approved' : feedback || '',
        conversationId: activeId,
        resume: true,
        file_access: fileAccess,
        workspace: workspacePath,
        decision: { action: decision, feedback, draft_id: draftId, gate_id: gateId },
      })
      /* 减一的前提是那一扇真的被胶囊数过：折叠段（归属记录上线前的老会话）里的门从来
         不在 `owned_total` 里，从它上面批准再减一会把胶囊减成比真实值少 1。
         必须在跳转前读快照 —— 跳回队列后侧栏的路由切换效应会把它覆盖掉。 */
      const wasCounted = !!gateId
        && peekPendingStore().snapshot.items.some((i) => i.gate_id === gateId)
      if (decision === 'approve') {
        /* 2026-10 修正：批准后**留在对话里**，不再跳待确认队列。
           原实现（D8/D9）为了让「我做成了什么」由队列那一行补回来，直接把老师从
           对话弹到队列——方向是反的：从队列点开某行跳进对话才是正常流向，
           在对话里点确认却被弹走，老师既看不到执行结果，也接不上刚说的话。
           现在：原地等这一轮流读完（下面 `await runStream(resp)` 本来就读完），
           角标照旧先减 1 再以服务端为准。队列那边的「打开 → 跳对话」不变。 */
        if (wasCounted) decrementPendingCount()
        /* 不加 toast：执行结果下一段就会出现在这条对话里（runStream 照常读完），
           多弹一个提示反而挡视线；也不要在 happy path 里动全局 message。 */
      } else if (decision === 'abandon' && wasCounted) {
        decrementPendingCount()
      }
      await runStream(resp)
      /* 三种决定都会动待确认数（批准消费掉、放弃取消掉、打回重开下一版），流读完再
         force 拉一次以服务端为准。force 是必要的：跳回队列那一刻侧栏已经抢发了一发，
         那一发反映的还是决定之前的状态，不 force 就会合并到它、把旧数字当新结果。 */
      void refetchPendingCount(true)
    } catch (e) {
      toast.error(`操作失败: ${e instanceof Error ? e.message : e}`)
      setStreaming(false)
    }
  }

  /* 队列出口：流结束（正常 done / 出错 / 断连三条收尾路径都会 setStreaming(false)）
     把记下的决定发出去，恰好一次 —— 先清队列再发；发送自己会把 streaming 置回 true，
     但那时队列已空，effect 不会二次触发。 */
  const handleDecisionRef = useRef(handleDecision)
  handleDecisionRef.current = handleDecision
  /* 挂载/切会话时先捞一次持久化队列（F5 丢队列是「回执撒谎」的病灶）。
   * 非流式状态下捞到的队列立刻经同一 effect 发出 —— 老师刷新回来，点过的
   * 决定接着生效，而不是无声消失。 */
  useEffect(() => {
    if (!streaming && !queuedDecisionRef.current) {
      const q = restoreQueue()
      if (q) queuedDecisionRef.current = q
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queueKey])

  useEffect(() => {
    if (streaming) return
    const q = queuedDecisionRef.current
    if (!q) return
    queuedDecisionRef.current = null
    persistQueue(null)
    void handleDecisionRef.current(q.decision, q.feedback, q.draftId, q.gateId)
  }, [streaming])

  /* ── 上传 ── */
  async function handleFileUpload(file: File) {
    if (file.size > 10 * 1024 * 1024) {
      toast.warning('文件不能超过 10MB')
      return
    }
    const fd = new FormData()
    fd.append('file', file)
    appendMessage({ id: nid(), kind: 'user', text: `上传文件: ${file.name}` })
    try {
      const d = await api.post<{
        upload_id: string
        file_name: string
        preview_text?: string
        image_url?: string | null
        actions: { id: string; label: string }[]
      }>('/api/agent/upload', fd)
      // 图片上传同步收进工件面板「附件」tab（批次 A2），原图可回看
      if (d.image_url) {
        setPanelAttachments((prev) => [...prev, {
          upload_id: d.upload_id, file_name: d.file_name || file.name, image_url: d.image_url ?? null,
        }])
        setPanelOpen(true)
        setPanelFocus({ tab: 'attachments', targetId: d.upload_id })
      }
      appendMessage({
        id: nid(),
        kind: 'upload',
        file_name: d.file_name || file.name,
        preview: d.preview_text || '',
        upload_id: d.upload_id,
        actions: d.actions || [],
      })
    } catch (e) {
      appendMessage({ id: nid(), kind: 'system', text: `⚠ 上传失败: ${e instanceof Error ? e.message : ''}` })
    }
  }

  /* ── 批量上传答题卡 ── */
  async function handleBatchUpload(files: File[]) {
    if (!files.length) return
    if (files.length > 10) {
      toast.warning('一次最多上传 10 张答题卡')
      return
    }
    for (const f of files) {
      if (f.size > 10 * 1024 * 1024) {
        toast.warning('单张答题卡不能超过 10MB')
        return
      }
    }
    const fd = new FormData()
    fd.append('teacher_id', getTeacherId())
    files.forEach((f) => fd.append('files', f))
    appendMessage({ id: nid(), kind: 'user', text: `已选择 ${files.length} 张答题卡，正在提交识别…` })
    try {
      const d = await api.post<{ success: boolean; batch_id: string; count: number; detail?: string }>(
        '/api/ocr/tasks/batch',
        fd,
      )
      if (!d.success || !d.batch_id) {
        appendMessage({ id: nid(), kind: 'system', text: `⚠ 批量上传失败: ${d.detail || '未知错误'}` })
        return
      }
      await sendMessage(
        `我已上传 ${d.count} 张答题卡，批次号 ${d.batch_id}（teacher_id=${getTeacherId()}）。请识别这批答题卡并批改。`,
      )
    } catch (e) {
      appendMessage({ id: nid(), kind: 'system', text: `⚠ 批量上传失败: ${e instanceof Error ? e.message : ''}` })
    }
  }

  /* ── 输入区 ── */
  const chemPreview = useMemo(() => {
    const raw = input.trim()
    if (!raw) return ''
    const formatted = formatChemicalText(raw)
    return formatted === raw ? '' : formatted
  }, [input])

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage(input)
    }
  }

  /* ── 工作区选择器（服务器端目录浏览，支持新建文件夹） ── */
  async function loadWsDirs(path: string) {
    try {
      const d = await api.get<{ current: string; parent: string | null; dirs: string[]; default_ws: string }>(
        `/api/workspace/dirs?path=${encodeURIComponent(path)}`,
      )
      setWsCurrent(d.current)
      setWsParent(d.parent)
      setWsDirs(d.dirs)
      setWsDefault(d.default_ws)
    } catch (e) {
      toast.error(`目录读取失败: ${e instanceof Error ? e.message : ''}`)
    }
  }

  function openWsPicker() {
    setWsPickerOpen(true)
    loadWsDirs(workspacePath)
  }

  async function enterWsDir(name: string) {
    const sep = wsCurrent.includes('\\') ? '\\' : '/'
    const joined = wsCurrent
      ? `${wsCurrent}${wsCurrent.endsWith('/') || wsCurrent.endsWith('\\') ? '' : sep}${name}`
      : name
    await loadWsDirs(joined)
  }

  async function wsGoUp() {
    if (wsParent) await loadWsDirs(wsParent)
  }

  async function wsCreateFolder() {
    if (!wsNewFolder.trim()) return
    try {
      await api.post('/api/workspace/mkdir', { path: wsCurrent, name: wsNewFolder.trim() })
      setWsNewFolder('')
      await loadWsDirs(wsCurrent)
    } catch (e) {
      toast.error(`新建失败: ${e instanceof Error ? e.message : ''}`)
    }
  }

  function wsConfirmPick() {
    const picked = wsCurrent || wsDefault
    setWorkspacePath(picked)
    localStorage.setItem('chemai_workspace', picked)
    setWsPickerOpen(false)
  }

  async function wsUseDefault() {
    try {
      const d = await api.get<{ path: string }>('/api/workspace/default')
      setWorkspacePath(d.path)
      localStorage.setItem('chemai_workspace', d.path)
      setWsPickerOpen(false)
    } catch (e) {
      toast.error(`获取默认工作区失败: ${e instanceof Error ? e.message : ''}`)
    }
  }

  return (
    /* AI 工作台（v2.1）：氛围底图上的浅色玻璃舱——磨砂卡 + 品牌青 AI 气泡 + 渐变主按钮 */
      <div className="h-full flex gap-4 min-h-0 animate-fade-in-up">
      {/* 会话列表面板（可折叠） */}
      <div
        className={`${
          convOpen ? 'flex' : 'hidden'
        } flex-col w-[240px] shrink-0 bg-white/70 backdrop-blur-md border border-white/90 rounded-[var(--radius-lg)] shadow-[var(--shadow-sm)] overflow-hidden`}
      >
        <div className="p-3 border-b border-border">
          <Button size="sm" className="w-full justify-center" onClick={newConversation}>
            <MessageSquarePlus size={14} />
            新建对话
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {conversations.length === 0 ? (
            <div className="text-center text-xs text-ink-muted py-8 px-4">暂无对话记录</div>
          ) : (
            conversations.map((c) => (
              <div
                key={c.thread_id}
                onClick={() => selectConversation(c.thread_id)}
                className={`group flex items-center gap-2 px-2.5 py-2 rounded-[var(--radius-md)] cursor-pointer transition-colors ${
                  activeId === c.thread_id ? 'bg-primary-soft' : 'hover:bg-surface-muted'
                }`}
              >
                <MessagesSquare size={14} className="text-ink-muted shrink-0" />
                <span
                  className={`flex-1 text-sub truncate ${activeId === c.thread_id ? 'text-primary font-medium' : 'text-ink-secondary'}`}
                >
                  {c.title || '新对话'}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    deleteConversation(c.thread_id)
                  }}
                  className="opacity-0 group-hover:opacity-100 text-ink-muted hover:text-error transition-all cursor-pointer"
                  title="删除对话"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 主聊天区 */}
      <div className="flex-1 min-w-0 bg-white/75 backdrop-blur-md border border-white/90 rounded-[var(--radius-lg)] shadow-[var(--shadow-sm)] flex flex-col overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
          <button
            className="p-1.5 rounded-[var(--radius-sm)] text-ink-secondary hover:bg-surface-muted cursor-pointer"
            onClick={() => setConvOpen((v) => !v)}
            title="折叠/展开对话列表"
          >
            <MessagesSquare size={16} />
          </button>
          <span className="text-sm font-medium text-ink min-w-0 truncate">CHEMAI · 教研助手</span>
          {/* 待确认队列的入口（4a）。门产生在这个工作台里，入口就跟着放在这里，
              不再与「Agent 工作台」在侧栏平级。数量口径读那份共享 store：
              0 时只写「待确认」不涂数字（有一扇门在等不是出错，但不能空喊有）。 */}
          <button
            onClick={() => navigate('/pending')}
            aria-label={
              pendingCount > 0
                ? `待确认，${pendingCount >= 100 ? '99 项以上' : `${pendingCount} 项`}待处理`
                : '待确认'
            }
            className="ml-auto shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-sub text-ink-secondary hover:bg-surface-muted cursor-pointer focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)]"
          >
            待确认
            {pendingCount > 0 && (
              <span
                aria-hidden
                className="chem-mono inline-flex items-center justify-center h-[18px] min-w-[18px] px-[var(--space-1)] rounded-full bg-primary text-white text-[12px] font-semibold"
              >
                {pendingCount >= 100 ? '99+' : pendingCount}
              </span>
            )}
          </button>
          {/* 「结果未知」的入口（gate-lifecycle 2.7）。**只在有货时出现**：
              这是罕见态（进程崩在重放中途才会留下），平时不占地方；但正因为它
              罕见，老师不会主动去找 —— 没有这点提示，那张卡就等于不存在。
              颜色刻意与上面那颗蓝胶囊不同：两类事不是一件事，别让老师以为
              点进去能看到 3 扇待确认的门。落在待确认页顶部那一段。 */}
          {inDoubtCount > 0 && (
            <button
              onClick={() => navigate('/pending')}
              aria-label={`结果待核实，${inDoubtCount >= 100 ? '99 项以上' : `${inDoubtCount} 项`}`}
              className="shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-sub text-warning hover:bg-warning-soft cursor-pointer focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)]"
            >
              结果待核实
              <span
                aria-hidden
                className="chem-mono inline-flex items-center justify-center h-[18px] min-w-[18px] px-[var(--space-1)] rounded-full bg-warning text-white text-[12px] font-semibold"
              >
                {inDoubtCount >= 100 ? '99+' : inDoubtCount}
              </span>
            </button>
          )}
          {/* 我的工件（批次 A4）：跨会话工件库，与会话内面板互补 */}
          <button
            onClick={() => navigate('/artifacts')}
            className="shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-[var(--radius-sm)] text-sub text-ink-secondary hover:bg-surface-muted cursor-pointer"
            title="跨会话的工件库"
          >
            我的工件
          </button>
        </div>

        <MessageList items={messages} phase={phase} orb={orb} streaming={streaming} elapsed={elapsed} onApproval={handleDecision}
          onOpenArtifact={(artifactId) => {
            // 工件卡 → 右栏工件 tab 聚焦（面板关着就顺手展开）
            setPanelOpen(true)
            setPanelFocus({ tab: 'artifact', targetId: artifactId })
          }}
          onOpenAttachment={(uploadId) => {
            setPanelOpen(true)
            setPanelFocus({ tab: 'attachments', targetId: uploadId })
          }}
        />

        {/* 快捷建议 + 化学式预览 + 输入区 */}
        <div className="border-t border-border px-4 pt-3 pb-4">
          {unreachable ? (
            /* 带 `?thread=` 进来但取不到历史（会话已删 / 归属被拒）。NEVER 偷偷换成
               列表第一条：老师点的是某一扇具体的门，落到不相干的对话里比报错更难查。 */
            <div
              role="status"
              className="mb-3 px-3 py-2.5 rounded-[var(--radius-md)] bg-warning-soft border border-warning/25 text-sub text-warning flex items-center gap-2"
            >
              <span>这条会话已经不在了（被删除，或不属于你的账号）。</span>
              <button
                onClick={backToMyConversations}
                className="ml-auto shrink-0 underline font-medium cursor-pointer"
              >
                回到我的对话
              </button>
            </div>
          ) : null}
          {!unreachable && messages.length === 0 && (
            <div className="mb-3">
              <SuggestionGroup
                disabled={streaming}
                onPick={(prompt) => {
                  // 建议卡是**半句模板**（「请帮我配平化学方程式：」）—— 填进输入框
                  // 让老师补完再手动发；直接发送等于把残句丢给 agent。
                  setInput(prompt)
                  inputRef.current?.focus()
                }}
              />
            </div>
          )}
          {chemPreview && (
            <div className="mb-2 px-3 py-2 rounded-[var(--radius-md)] bg-teal-soft/60 border border-teal/20 chem-mono text-[14px] text-teal">
              {chemPreview}
            </div>
          )}
          {/* 输入框独占一行；@ 唤起引用弹层（批次 A4） */}
          <div className="relative">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => { setInput(e.target.value); trackCaret(e.target) }}
              onClick={(e) => trackCaret(e.currentTarget)}
              onKeyUp={(e) => trackCaret(e.currentTarget)}
              onKeyDown={onKeyDown}
              placeholder="输入化学教学问题，@ 引用附件/工件，/动画 /讲评 快捷生成，Enter 发送…"
              rows={2}
              className="w-full resize-none border border-border rounded-[var(--radius-md)] px-4 py-3 text-[15px] text-ink placeholder:text-ink-muted outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 transition-colors min-h-[64px]"
              style={{ maxHeight: 180 }}
              disabled={streaming}
            />
            {atQuery !== null && atMatches.length > 0 && (
              <div
                data-testid="ref-popup"
                className="absolute bottom-full left-0 right-0 mb-1 bg-white border border-border
                           rounded-[var(--radius-md)] shadow-[var(--shadow-md)] max-h-56 overflow-y-auto z-20"
              >
                <div className="px-3 py-1.5 text-micro text-ink-muted border-b border-border/60">
                  引用本会话的附件与工件（token 会带进提示词）
                </div>
                {atMatches.map((item) => (
                  <button
                    key={`${item.kind}-${item.id}`}
                    onClick={() => insertRef(item)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-sub text-ink
                               hover:bg-primary-soft cursor-pointer text-left"
                  >
                    <span className={`text-micro px-1.5 py-0.5 rounded ${item.kind === 'attachment' ? 'bg-teal-soft text-teal' : 'bg-primary-soft text-primary'}`}>
                      {item.kind === 'attachment' ? '附件' : '工件'}
                    </span>
                    <span className="truncate flex-1">{item.label}</span>
                    <span className="text-micro text-ink-muted chem-mono">{item.token}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* 工具栏（WorkBuddy 式：工作区开关/选择、新对话、上传全部收在输入区下方） */}
          <div className="mt-2.5 flex items-center gap-2 flex-wrap">
            <button
              className="flex items-center gap-1.5 h-8 px-2.5 rounded-[var(--radius-md)] text-xs text-ink-secondary hover:bg-surface-muted transition-colors cursor-pointer shrink-0"
              onClick={newConversation}
              title="新建对话"
            >
              <MessageSquarePlus size={14} />
              新对话
            </button>
            <select
              value={fileAccess}
              onChange={(e) => setFileAccess(e.target.value as 'off' | 'workspace' | 'full')}
              title="文件访问档位：工作区=只在老师工作文件夹内自由读写，区外需你批准（推荐）；完全访问=全路径放行，区外不再弹卡（破坏性操作仍会请你确认）；关闭=禁用全部文件与命令"
              className="h-8 shrink-0 border border-border rounded-[var(--radius-md)] px-2 text-xs text-ink outline-none focus:border-primary bg-surface"
            >
              <option value="workspace">工作区（默认）</option>
              <option value="full">完全访问</option>
              <option value="off">关闭</option>
            </select>
            <button
              className="flex items-center gap-1.5 h-8 px-2.5 rounded-[var(--radius-md)] text-xs text-ink-secondary hover:bg-surface-muted transition-colors cursor-pointer max-w-[280px] shrink-0"
              onClick={openWsPicker}
              title={`打开目录选择工作区（可新建文件夹）${workspacePath ? ` · 当前：${workspacePath}` : ''}`}
            >
              <FolderOpen size={14} />
              <span className="truncate">
                {workspacePath ? workspacePath.split(/[\\/]/).filter(Boolean).pop() : '默认'}
              </span>
            </button>
            <label
              className="flex items-center justify-center w-8 h-8 rounded-[var(--radius-md)] text-ink-muted hover:bg-surface-muted hover:text-teal transition-colors cursor-pointer shrink-0"
              title="上传文件"
            >
              <Paperclip size={16} />
              <input
                type="file"
                accept="image/*,.pdf,.doc,.docx"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) handleFileUpload(f)
                  e.target.value = ''
                }}
              />
            </label>
            <label
              className="flex items-center justify-center w-8 h-8 rounded-[var(--radius-md)] text-ink-muted hover:bg-surface-muted hover:text-teal transition-colors cursor-pointer shrink-0"
              title="批量上传答题卡（≤10 张）"
            >
              <FileStack size={16} />
              <input
                type="file"
                accept="image/*"
                multiple
                className="hidden"
                onChange={(e) => {
                  const files = Array.from(e.target.files || [])
                  handleBatchUpload(files)
                  e.target.value = ''
                }}
              />
            </label>

            {/* 右侧:上下文占用环 + 发送(与工具栏对齐) */}
            <div className="ml-auto flex items-center gap-2 shrink-0">
              <ContextMeter info={ctxInfo} />
              {streaming ? (
                <Button variant="secondary" size="md" disabled>
                  <X size={15} />
                  生成中
                </Button>
              ) : (
                <Button size="md" onClick={() => sendMessage(input)} disabled={!input.trim()}>
                  <SendIcon size={15} />
                  发送
                </Button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 工件面板（批次 A2）：附件原图 / 工件预览 / 版本历史三 tab。
          折叠时顶栏留展开钮——面板默认隐藏不抢聊天空间。 */}
      {panelOpen ? (
        <ArtifactPanel
          open={panelOpen}
          onToggle={() => setPanelOpen(false)}
          focus={panelFocus}
          artifacts={panelArtifacts}
          attachments={panelAttachments}
        />
      ) : (
        <div className="shrink-0 flex flex-col items-center gap-2 bg-white/70 backdrop-blur-md border border-white/90 rounded-[var(--radius-lg)] shadow-[var(--shadow-sm)] px-2 py-3 self-start">
          <button
            onClick={() => setPanelOpen(true)}
            aria-label="展开工件面板"
            title="工件面板（附件 / 工件 / 版本）"
            className="p-1.5 rounded-[var(--radius-sm)] text-ink-secondary hover:bg-surface-muted cursor-pointer"
          >
            <PanelRightOpen size={16} />
          </button>
        </div>
      )}

      {/* 工作区选择器（服务器端目录浏览 + 新建文件夹） */}
      <AntModal
        open={wsPickerOpen}
        onCancel={() => setWsPickerOpen(false)}
        onOk={wsConfirmPick}
        okText="选择此目录"
        cancelText="取消"
        title="选择工作区文件夹"
        width={520}
      >
        <div className="mb-2 text-xs text-ink-secondary break-all">
          当前：<span className="text-ink font-medium">{wsCurrent || '（磁盘根）'}</span>
        </div>
        <div className="flex items-center gap-2 mb-2">
          <button
            className="flex items-center gap-1 h-7 px-2 rounded-[var(--radius-md)] text-xs text-ink-secondary hover:bg-surface-muted cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            onClick={wsGoUp}
            disabled={!wsParent}
          >
            <FolderUp size={14} />
            上一级
          </button>
          <button
            className="flex items-center gap-1 h-7 px-2 rounded-[var(--radius-md)] text-xs text-ink-secondary hover:bg-surface-muted cursor-pointer"
            onClick={wsUseDefault}
          >
            使用默认（~/ChemAI-Workspace）
          </button>
        </div>
        <div className="border border-border rounded-[var(--radius-md)] max-h-64 overflow-y-auto mb-3">
          {wsDirs.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-ink-muted">此目录下没有子文件夹</div>
          ) : (
            wsDirs.map((d) => (
              <div
                key={d}
                onClick={() => enterWsDir(d)}
                className="flex items-center gap-2 px-3 py-2 text-sub text-ink cursor-pointer hover:bg-surface-muted border-b border-border/50 last:border-b-0"
              >
                <FolderPlus size={14} className="text-ink-muted" />
                {d}
              </div>
            ))
          )}
        </div>
        <div className="flex items-center gap-2">
          <AntInput
            value={wsNewFolder}
            onChange={(e) => setWsNewFolder(e.target.value)}
            onPressEnter={wsCreateFolder}
            placeholder="新文件夹名（在此目录下创建）"
            size="small"
          />
          <button
            className="h-7 px-3 rounded-[var(--radius-md)] bg-primary text-white text-xs hover:opacity-90 cursor-pointer shrink-0"
            onClick={wsCreateFolder}
          >
            新建文件夹
          </button>
        </div>
      </AntModal>
      </div>
  )
}
