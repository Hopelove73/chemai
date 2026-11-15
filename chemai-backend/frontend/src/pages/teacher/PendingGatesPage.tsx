/* 待确认队列页（4a）—— 计划 D1/D3/D5/D6/D9/D12/D13/D14/D19，任务 T9。
 *
 * 这一页只做一件事：把散在各会话里、等老师决定的门摊平成一张按"等了多久"排序的队列。
 * 三处口径必须记住：
 *   · 决定不在这里做。行是跳转入口，批准/打回只有聊天页那一条通路（D1）；
 *     行内放批准按钮会做出一个假控件。
 *   · 侧栏胶囊读 store 里**未筛选**的 `owned_total`；本页副标题与尾行读**当前视图**的
 *     同名字段（D14 的筛选在服务端 `LIMIT` 之前，四字段一起收窄）。
 *   · 「打回次数」列本期不渲染：它要按 `gate_id` 聚合决定日志（第 1 项），
 *     未落地前整列不出现，而不是渲染一列 0 骗人（D1）。
 *
 * **D1 的一处例外：「结果未知」段（见 `InDoubtSection`）行内有按钮。**
 * D1 说「决定不在这里做」，理由是批准的**唯一**通路必须能叫醒 agent 续接
 * （只有 `/resume` 的重放做得到，卡片上的批准按钮在这里点会让任务停在原地）。
 * 那张卡不同：它的两个动作**不执行任何动作、也不续接**，只是把门的状态对齐到事实
 * （`resolve_in_doubt`），压根没有"聊天页那条通路"可用。所以按钮在这里是真的。
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Button, Card, EmptyState, Table, type Column } from '../../components/ui'
import { api } from '../../lib/api'
import { formatChemicalText } from '../../lib/chem-format'
import { useInDoubt } from '../../lib/use-in-doubt'
import {
  EMPTY_PENDING,
  fetchPendingFiltered,
  refetchPendingCount,
  usePendingCount,
  type PendingItem,
  type PendingPayload,
} from '../../lib/use-pending-count'

/** 表头必须显式覆盖 Table 默认的 `text-ink-muted`：那个 token 白底实测 2.63:1，
 *  列名是信息不是装饰（D13 的本页禁令）。 */
const TH_CLASS =
  'text-left text-xs font-medium text-ink-secondary px-4 py-3 border-b border-border whitespace-nowrap'

/** 会话名列与「打开 →」列在 860px 以下砍掉（D12）：会话名退到链接的 title 里，
 *  跳转仍用 thread_id；「打开 →」只是第一列那个真链接的可见提示，留着会把表格撑成
 *  横向滚动，而 D12 禁止用横滚代替砍列。
 *  断点写成 `[@media(min-width:860px)]` —— Tailwind v4 不把 `[@860px]` 当媒体变体，
 *  那样写只会留下一条永不生效的 `.hidden`（浏览器实测踩过）。
 *  D12 的 1100px 那一档本期没有列可砍（「打回」列按 D1 不渲染），第 1 项落地时补。 */
const HIDE_NARROW = 'hidden [@media(min-width:860px)]:table-cell'
const TH_CONV_CLASS = `${TH_CLASS} ${HIDE_NARROW}`
const TD_CONV_CLASS = `px-4 py-3 text-sm text-ink-secondary ${HIDE_NARROW}`

/** 行跳转的唯一地址形状（T21）：链接的 href 与整行点击共用它，两处不许各拼一遍。 */
function threadHash(conversationId: string): string {
  return `/chat?thread=${encodeURIComponent(conversationId)}`
}

/** 第一列的正文：后端算好的两段人话，布尔门的动作词是空串（它含在对象名里）。 */
function rowLabel(row: PendingItem): string {
  return row.action_label ? `${row.action_label} · ${row.subject_label}` : row.subject_label
}

/** 把一段毫秒说成人话：`6天4小时` / `19小时` / `30分` / `45秒`。
 *  NEVER 细到"分"用于等待列 —— 分钟级会让那一列每分钟跳，反而读不出久等（D1）。 */
export function formatAgo(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const mins = Math.floor((total % 3600) / 60)
  if (days > 0) return `${days}天${hours}小时`
  if (hours > 0) return `${hours}小时`
  if (mins > 0) return `${mins}分`
  return `${total}秒`
}

/** 解析后端给的 naive UTC ISO 串；拿不到返回 null（显示成 `—`，不留空白格）。 */
function parseUtc(iso: string | null): number | null {
  if (!iso) return null
  const at = Date.parse(iso.endsWith('Z') ? iso : `${iso}Z`)
  return Number.isNaN(at) ? null : at
}

/** 「已等待」列与副标题那句「最久的已等」：从 `last_opened_at` 起算，不是 `created_at`。 */
export function humanWait(iso: string | null, now = Date.now()): string {
  const at = parseUtc(iso)
  return at === null ? '—' : formatAgo(now - at)
}

/**
 * 「结果未知」段（gate-lifecycle 2.7 第 4 步）。
 *
 * 这一态怎么来的：门的许可在执行**之前**就占掉了（防同一扇门批两次），正常情况下
 * 动作跑完立刻收口。两者永远在同一次请求、同一个进程里 —— 所以**服务重启时**还停在
 * 「执行中」的门，就是执行它的那个进程已经不在了：动作可能跑过、也可能没跑，
 * 系统无从判断。不设超时去猜（阈值得大于最慢的工具，定短了正在跑的门会冒卡吓人），
 * 于是落成一张卡交给老师：他核对真实世界（学生到底收到没有），比系统猜得准。
 *
 * 两个动作对应两种现实：**确认已执行** → 结案；**重试** → 退回待确认，老师再批一次
 * 就走既有的重放通路（不为它另造一条执行路径）。
 *
 * 这是**另一份数据**，不并进 `usePendingCount` 那份 store：那张表问的是「哪些门在等
 * 你决定」，这一份问的是「哪些门需要你裁定」，两个问题的答案不能混成一个数字 ——
 * 侧栏那颗胶囊数的是前者。
 */
function InDoubtSection() {
  /* 走共享 store，不自己拉一份：顶栏那颗提示数的是同一个数，各拉各的会数出两个数。 */
  const { data, loaded, loading, error: storeError, refetch } = useInDoubt()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState('')

  useEffect(() => {
    if (!loaded && !loading) void refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const resolve = (gateId: string, retry: boolean) => {
    setBusy(gateId)
    api
      .post(`/api/agent/gates/${gateId}/in-doubt`, { retry })
      .then(() => {
        void refetch(true)
        /* 「重试」把它退回 `open` —— 那一扇随即进入待确认列表，得让那份快照知道，
           否则老师刚放回去的门在另一张表上看不见。 */
        if (retry) void refetchPendingCount(true)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : '操作失败')
      })
      .finally(() => setBusy(''))
  }

  const columns = useMemo<Column<PendingItem>[]>(
    () => [
      {
        key: 'subject',
        title: '动作与对象',
        thProps: { className: TH_CLASS },
        render: (row) => (
          <a
            href={`#${threadHash(row.conversation_id)}`}
            className="text-sm text-ink font-medium hover:text-primary focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)] rounded-[var(--radius-sm)]"
            title={`会话 ${row.conversation_id}`}
          >
            {rowLabel(row)}
          </a>
        ),
      },
      {
        key: 'resolve',
        title: '这两个动作只差一个事实：那次操作到底跑成了没有',
        thProps: { className: TH_CLASS },
        render: (row) => (
          <span className="flex items-center gap-2">
            <Button
              variant="secondary"
              disabled={busy === row.gate_id}
              onClick={() => resolve(row.gate_id, false)}
            >
              确认已执行
            </Button>
            <Button
              variant="secondary"
              disabled={busy === row.gate_id}
              onClick={() => resolve(row.gate_id, true)}
            >
              重试
            </Button>
          </span>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [busy],
  )

  /* 取不到时不拿它当「没有」：清空比报错更糟 —— 老师会以为这一类事已经处理完了。
     所以取数失败也要把这一段露出来，让他知道有东西没读到。 */
  const shownError = error || storeError
  const items = data.items
  if (!shownError && items.length === 0) return null

  return (
    <Card flush>
      <div className="px-4 py-3 border-b border-border bg-warning-soft/40">
        <p className="text-sub text-ink m-0 font-medium">
          有 {items.length} 件事的结果无法确认
        </p>
        <p className="mt-1 text-[12px] text-ink-secondary m-0">
          这些操作在服务重启时还停在「执行中」，跑成了没有无从判断。
          请核对实际情况后处理：确实完成了选「确认已执行」，没有完成选「重试」。
        </p>
        {shownError && <p className="mt-1 text-[12px] text-warning m-0">{shownError}</p>}
      </div>
      <Table
        columns={columns}
        rows={items}
        rowKey={(row) => row.gate_id}
        empty={<span className="text-sub text-ink-secondary">这一段已经清空了。</span>}
      />
    </Card>
  )
}


export function PendingGatesPage() {
  const location = useLocation()
  const justApproved = (location.state as { justApproved?: string } | null)?.justApproved ?? ''
  const { data, loaded, loading, error, fetchedAt, truncated, refetch } = usePendingCount()

  const [kind, setKind] = useState('')
  const [filtered, setFiltered] = useState<PendingPayload | null>(null)
  const [filterError, setFilterError] = useState('')
  /** null = 老师还没碰过它，此时按 D5 的规则取默认值（名下空 → 默认展开）。 */
  const [expanded, setExpanded] = useState<boolean | null>(null)
  /** thread_id → 会话标题。标题不在门表里，取聊天页那份现成的会话列表（同源同口径）。 */
  const [titles, setTitles] = useState<Record<string, string>>({})

  /* 批准前的那份快照里留着刚批准的那一行 —— 用来显示 D9 的行内终态。
     `complete` 记下当时那份列表有没有被 50 行上限截断（store 直接告诉我们，
     不用 `items.length === owned_total` 去猜：批准时乐观减一会先动 `owned_total`，
     一比就成了"被截断"，每一次批准都会把这一行终态吃掉）：只有完整时
     "新结果里没有它"才等于"它已经生效"；否则我们并不知道它还在不在队列里，
     把一条仍然待确认的门演成「已批准」就是假话。 */
  const preApproval = useRef<{ row: PendingItem; complete: boolean } | null>(null)
  if (!preApproval.current && justApproved && loaded) {
    const row = data.items.find((i) => i.gate_id === justApproved) ?? null
    if (row) preApproval.current = { row, complete: !truncated }
  }

  useEffect(() => {
    /* 挂载必拉一份新的（2026-10-26 8 步实测）：store 没有周期轮询，最后一份
       快照可能停在「门行还没提交」之前——老师从聊天页顶栏点进来时角标/列表
       显示旧数，只有 F5 才对。原先 `if (!loaded && !loading)` 恰好把这个场景
       放过去（loaded=true 就跳过）。force：若入口效应已抢发一发（inflight），
       在它落定后再补一发，拿到的一定是挂载之后的状态。 */
    void refetch(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    let alive = true
    api
      .get<{ conversations: { thread_id: string; title: string }[] }>(
        '/api/agent/chat/conversations?prefix=t-,c',
      )
      .then((d) => {
        if (!alive) return
        const map: Record<string, string> = {}
        for (const c of d.conversations || []) map[c.thread_id] = c.title || ''
        setTitles(map)
      })
      .catch(() => {
        /* 标题取不到不影响队列：第二列退回 thread_id */
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    if (!kind) {
      setFiltered(null)
      setFilterError('')
      return
    }
    let alive = true
    fetchPendingFiltered(kind)
      .then((p) => {
        if (!alive) return
        setFiltered(p)
        setFilterError('')
      })
      .catch((err: unknown) => {
        if (alive) setFilterError(err instanceof Error ? err.message : '没能取到筛选结果')
      })
    return () => {
      alive = false
    }
  }, [kind])

  const view: PendingPayload = kind ? (filtered ?? EMPTY_PENDING) : data
  /* 筛选结果还没回来时不要把"空"当结论 —— 否则老师会看到一句「你有 0 件事」。 */
  const filterPending = !!kind && filtered === null && !filterError
  const tail = Math.max(0, view.owned_total - view.items.length)
  const oldest = view.items[0] ?? null
  /* 刚批准的那一行（D9）：服务端仍返回它 = 重放失败、门还开着 → 原样回到队列；
     没有它 = 已经生效 → 以行内终态显示，且不进计数。筛选态下不演这一出。 */
  const terminalRow =
    justApproved && loaded && !kind && !filterPending
    && preApproval.current?.complete && !view.items.some((i) => i.gate_id === justApproved)
      ? preApproval.current.row
      : null
  const rows = terminalRow ? [terminalRow, ...view.items] : view.items
  const unownedTruncated = view.unowned_total > view.unowned_items.length

  const ownedEmpty = view.owned_total === 0 && view.unowned_total > 0
  const allClear =
    loaded && !kind && !filterPending && view.owned_total === 0
    && view.unowned_total === 0 && !terminalRow
  /* D5 的第二种空：名下没有在等的门、但老会话段非空 → 折叠段默认展开。
     那一存在的理由就是"说明这里为什么有别人的旧门"，收起来等于让老师自己找。 */
  const unownedOpen = expanded ?? ownedEmpty
  /* 两种失败形状不同（D6）：首屏什么都没拿到 → 整页错误 + 重试；
     已经有旧列表、只是这次刷新失败 → 旧列表原样留着，顶部明说它可能不是最新。
     把老师正要点第 3 行的队列换成一句「出错了」，等于让一次网络抖动比不报错更糟。 */
  const fatal = !loaded && !!error
  const staleBanner = loaded && (!!error || !!filterError)
  const firstLoad = loading && !loaded && !error

  const columns = useMemo<Column<PendingItem>[]>(
    () => [
      {
        key: 'subject',
        title: '动作与对象',
        thProps: { className: TH_CLASS },
        render: (row) =>
          row === terminalRow ? (
            <span className="text-sm text-ink-secondary">{rowLabel(row)}</span>
          ) : (
            <a
              href={`#${threadHash(row.conversation_id)}`}
              className="text-sm text-ink font-medium hover:text-primary focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)] rounded-[var(--radius-sm)]"
              title={`会话 ${row.conversation_id}`}
            >
              {rowLabel(row)}
            </a>
          ),
      },
      {
        key: 'conversation',
        title: '来自会话',
        thProps: { className: TH_CONV_CLASS },
        tdProps: { className: TD_CONV_CLASS },
        render: (row) => (
          <span className="text-sub text-ink-secondary" title={row.conversation_id}>
            {formatChemicalText(titles[row.conversation_id] || row.conversation_id)}
          </span>
        ),
      },
      {
        key: 'waiting',
        title: '已等待',
        width: '120px',
        thProps: { className: TH_CLASS },
        /* D9 的图里终态那一行的「已等待」是 — ：它不再在等，报一个还在走的数是假话 */
        render: (row) => (
          <span className="chem-mono text-sub text-ink-secondary">
            {row === terminalRow ? '—' : humanWait(row.last_opened_at)}
          </span>
        ),
      },
      {
        key: 'open',
        title: '',
        width: '150px',
        thProps: { className: TH_CONV_CLASS },
        tdProps: { className: TD_CONV_CLASS },
        render: (row) =>
          row === terminalRow ? (
            <a
              href={`#${threadHash(row.conversation_id)}`}
              className="text-sub text-primary font-medium whitespace-nowrap focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)] rounded-[var(--radius-sm)]"
            >
              已批准，AI 正在执行 · 查看执行 →
            </a>
          ) : (
            <span className="text-sub text-ink-secondary whitespace-nowrap">打开 →</span>
          ),
      },
    ],
    [terminalRow, titles],
  )

  const openRow = (row: PendingItem) => {
    window.location.hash = threadHash(row.conversation_id)
  }

  return (
    <div className="space-y-4">
      {/* 返回工作台：本页是从顶栏那颗「待确认」点进来的独立页，自身没有回退入口——
          队列清空后尤其像死路（一行都没有，也没地方可点）。 */}
      <Link
        to="/chat"
        className="inline-flex items-center gap-1.5 text-[13px] text-ink-secondary hover:text-primary rounded-[var(--radius-sm)] focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)]"
      >
        ← 返回工作台
      </Link>
      <div>
        <h1 className="flex items-center gap-2 text-[22px] font-semibold text-ink m-0">
          待确认
          {allClear && (
            <span className="text-[12px] font-medium px-2 py-0.5 rounded-full bg-success-soft text-success">
              全部处理完
            </span>
          )}
        </h1>
        {firstLoad || filterPending ? (
          <div className="mt-1.5 h-4 w-72 rounded-[var(--radius-sm)] bg-surface-muted animate-pulse" />
        ) : (
          <p className="mt-1.5 text-[12px] text-ink-secondary">
            {ownedEmpty ? (
              <>你名下没有在等的门。下面 {view.unowned_total} 条来自归属记录上线前的老会话。</>
            ) : (
              <>
                你有 {view.owned_total} 件事在等你的决定
                {oldest && <> · 最久的已等 {humanWait(oldest.last_opened_at)}</>}
                {kind && <> · 已按类型筛选</>}
              </>
            )}
          </p>
        )}
      </div>

      {staleBanner && (
        /* 不是 Toast：Toast 3 秒自动消失，消失后老师会以为屏上这份快照是新的（D6）。 */
        <div
          role="status"
          className="px-3 py-2 rounded-[var(--radius-md)] bg-warning-soft border border-warning/25 text-sub text-warning flex items-center gap-3"
        >
          <span>
            列表更新于 {fetchedAt ? formatAgo(Date.now() - fetchedAt) : '—'}前，
            可能不是最新。（{error || filterError}）
          </span>
          <button
            onClick={() => void refetch()}
            className="ml-auto shrink-0 underline font-medium cursor-pointer focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)] rounded-[var(--radius-sm)]"
          >
            重试
          </button>
        </div>
      )}

      {fatal ? (
        /* 整页错误：描述 + 重试。这是全页唯一允许重试按钮的地方（D6）；
           文案说人话，不出现堆栈与状态码。 */
        <Card>
          <div className="text-center py-8">
            <p className="text-[14px] text-ink m-0">没能取到待确认列表</p>
            <p className="mt-1.5 text-sub text-ink-secondary">{error}</p>
            <div className="mt-4 flex justify-center">
              <Button variant="secondary" onClick={() => void refetch()}>
                重试
              </Button>
            </div>
          </div>
        </Card>
      ) : firstLoad ? (
        <Card flush>
          <div className="p-4 space-y-2">
            {[0, 1, 2, 3, 4].map((i) => (
              <div key={i} className="h-11 rounded-[var(--radius-md)] bg-surface-muted animate-pulse" />
            ))}
          </div>
        </Card>
      ) : allClear ? (
        <Card>
          <EmptyState
            title="没有在等你的事"
            description="AI 提出需要你看一眼的动作时，它会出现在这一页。"
            action={
              <Link
                to="/chat"
                className="text-sub text-primary font-medium underline focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)] rounded-[var(--radius-sm)]"
              >
                回 Agent 工作台
              </Link>
            }
          />
        </Card>
      ) : null}

      <InDoubtSection />

      {loaded && !fatal && !firstLoad && (rows.length > 0 || !!kind) && (
        <Card flush>
          <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
            <label className="text-[12px] text-ink-secondary" htmlFor="gate-kind">
              类型
            </label>
            <select
              id="gate-kind"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              className="text-sub text-ink bg-surface border border-border rounded-[var(--radius-sm)] px-2 py-1 focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)]"
            >
              <option value="">全部</option>
              <option value="draft">发送类</option>
              <option value="boolean">操作类</option>
            </select>
          </div>
          <Table
            columns={columns}
            rows={rows}
            rowKey={(row) => (row === terminalRow ? `terminal-${row.gate_id}` : row.gate_id)}
            empty={<span className="text-sub text-ink-secondary">这个类型下没有待确认的事。</span>}
            onRowClick={(row) => {
              if (row !== terminalRow) openRow(row)
            }}
          />
          {tail > 0 && (
            <div className="px-4 py-3 border-t border-border text-[12px] text-ink-secondary">
              另有 {tail} 件未显示 ·{' '}
              <button
                onClick={() => document.getElementById('gate-kind')?.focus()}
                className="underline cursor-pointer text-ink-secondary focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)] rounded-[var(--radius-sm)]"
              >
                按类型筛选
              </button>
            </div>
          )}
        </Card>
      )}

      {loaded && view.unowned_total > 0 && !fatal && (
        <Card flush>
          <button
            onClick={() => setExpanded(!unownedOpen)}
            aria-expanded={unownedOpen}
            aria-controls="unowned-gates"
            className="w-full flex items-center gap-2 px-4 py-3 text-left text-sub text-ink-secondary hover:bg-surface-muted/60 cursor-pointer rounded-[var(--radius-lg)] focus-visible:outline-2 focus-visible:outline-[var(--color-accent-interactive)]"
          >
            <span aria-hidden className="font-mono text-micro">
              {unownedOpen ? '▾' : '▸'}
            </span>
            另有 {view.unowned_total} 条来自归属记录上线前的老会话（所有教师可见）
            {unownedTruncated && `（按等待时长显示最久的 ${view.unowned_items.length} 条）`}
          </button>
          {unownedOpen && (
            <div id="unowned-gates">
              <Table
                columns={columns}
                rows={view.unowned_items}
                rowKey={(row) => row.gate_id}
                empty={<span className="text-sub text-ink-secondary">这一段已经清空了。</span>}
                onRowClick={openRow}
              />
            </div>
          )}
        </Card>
      )}
    </div>
  )
}
