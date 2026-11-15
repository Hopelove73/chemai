import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { CheckCircle2, XCircle } from 'lucide-react'
import { ChemMd } from '../../components/ChemMd'
import { api, can } from '../../lib/api'
import {
  Badge, Button, Card, EmptyState, Field, Input, Modal, Select, SkeletonCard,
  StatCard, Tabs, Tag, Textarea, useToast,
} from '../../components/ui'
import { ActionButton, useAsyncAction } from '../../components/ui/ActionButton'

interface KbModule {
  module: string
  docs: number
  proposals: number
  has_dictionary?: boolean | null
}

interface KbStatus {
  success: boolean
  modules: KbModule[]
  dictionaries: Record<string, number>
  kb_chunks?: number | null
  active_collection?: string | null
}

interface KbJobRow {
  job_id: string
  module: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted' | string
  progress?: Record<string, number> | null
  error?: string | null
  created_by?: string | null
  created_at?: string | null
  finished_at?: string | null
}

interface ReviewPackage {
  path: string
  module: string
  status: 'pending' | 'approved' | 'rejected' | string
  parse_error?: string | null
  content_changed?: boolean
  kp?: string
  doc?: string
  edge_total?: number
  prerequisite?: number
  relates_to?: number
  reviewer_id?: string | null
  reviewed_at?: string | null
  reason?: string | null
}

interface PackageDetail extends ReviewPackage {
  fingerprint?: string
  edges?: Array<{
    edge_type?: string
    from_kp?: string
    to_kp?: string
    kp?: string
    substance?: string
    formula?: string
    reason?: string
    confidence?: number
  }>
}

function StatusBadge({ pkg }: { pkg: ReviewPackage }) {
  if (pkg.parse_error) return <Tag tone="red">解析失败</Tag>
  if (pkg.status === 'approved') return <Badge status={pkg.content_changed ? 'warning' : 'pass'} label={pkg.content_changed ? '已批准·文件已变' : '已批准'} />
  if (pkg.status === 'rejected') return <Badge status="idle" label="已驳回" />
  return <Badge status="running" label="待审" />
}

/* ── 状态卡 ─────────────────────────────────────────── */

function StatusSection({ status }: { status: KbStatus }) {
  const totalDocs = status.modules.reduce((s, m) => s + m.docs, 0)
  const totalProposals = status.modules.reduce((s, m) => s + m.proposals, 0)
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-4 max-[860px]:grid-cols-1">
        <StatCard label="语料文档" value={totalDocs} suffix="篇" />
        <StatCard label="待生成/已生成提议包" value={totalProposals} suffix="个" tone={totalProposals > 0 ? 'warn' : 'ok'} />
        <StatCard label="向量库 kb_chunks" value={status.kb_chunks ?? 0} suffix="条"
          hint={status.kb_chunks == null ? 'chroma 不可用，存量未知' : undefined} />
      </div>
      <Card title="各册规模">
        <div className="grid grid-cols-2 gap-2 max-[860px]:grid-cols-1">
          {status.modules.map((m) => (
            <div key={m.module} className="flex items-center gap-3 text-sub rounded-[var(--radius-sm)] bg-surface-muted px-3 py-2">
              <span className="font-medium">{m.module}</span>
              <span className="text-ink-secondary">{m.docs} 篇文档</span>
              <span className="text-ink-muted">{m.proposals} 个提议包</span>
            </div>
          ))}
        </div>
      </Card>
      <Card title="词典覆盖">
        <div className="flex flex-wrap gap-2">
          {Object.entries(status.dictionaries).map(([name, count]) => (
            <Tag key={name} tone="blue">{name.replace('kp_dictionary_', '').replace('.json', '')} · {count} 条</Tag>
          ))}
        </div>
      </Card>
    </div>
  )
}

/* ── 审校队列 ───────────────────────────────────────── */

function ReviewQueue({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast()
  const [rows, setRows] = useState<ReviewPackage[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<PackageDetail | null>(null)
  const [rejecting, setRejecting] = useState<ReviewPackage | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  /* R-I5：批准（不可逆方向）行内两段式 */
  const [confirmingPath, setConfirmingPath] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; packages: ReviewPackage[] }>('/api/kb/review-packages')
      setRows(r.packages ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const openDetail = async (pkg: ReviewPackage) => {
    const r = await api.get<{ success: boolean; package: PackageDetail }>(
      `/api/kb/review-packages/detail?path=${encodeURIComponent(pkg.path)}`)
    setDetail(r.package)
  }

  const reject = useAsyncAction(async () => {
    if (!rejecting) return
    if (!rejectReason.trim()) { toast.error('驳回必须附理由'); return }
    const r = await api.post<{ success: boolean; message?: string }>(
      '/api/kb/review-packages/reject', { path: rejecting.path, reason: rejectReason.trim() })
    if (!r.success) { toast.error(r.message || '驳回失败'); return }
    toast.success('已驳回')
    setRejecting(null)
    setRejectReason('')
    await load()
    onChanged?.()
  })

  const pending = rows.filter((r) => r.status === 'pending' && !r.parse_error)
  const columns: TableColumnsType<ReviewPackage> = [
    { title: '审校包', dataIndex: 'path', key: 'path', ellipsis: true, render: (v) => <span className="font-mono text-[12px]">{v}</span> },
    { title: '知识点', dataIndex: 'kp', key: 'kp', width: 140 },
    { title: '提议边', key: 'edges', width: 130, render: (_, r) =>
      r.parse_error ? '—' : `${r.edge_total ?? 0}（先修 ${r.prerequisite ?? 0} / 关联 ${r.relates_to ?? 0}）` },
    { title: '状态', key: 'status', width: 130, render: (_, r) => <StatusBadge pkg={r} /> },
    {
      title: '操作', key: 'actions', width: 250,
      render: (_, row) => {
        if (row.parse_error || row.status !== 'pending') {
          return <Button variant="ghost" onClick={() => { void openDetail(row) }}>查看</Button>
        }
        return confirmingPath === row.path ? (
          <span className="inline-flex items-center gap-1.5">
            <ActionButton
              onAction={async () => {
                const r = await api.post<{ success: boolean; message?: string }>(
                  '/api/kb/review-packages/approve', { path: row.path })
                if (!r.success) { toast.error(r.message || '批准失败'); return }
                toast.success('已批准并落库')
                setConfirmingPath(null)
                await load()
                onChanged?.()
              }}
              busyLabel="落库中…"
              className="!px-2 !py-1 !text-[12px]"
            >确认落库</ActionButton>
            <Button variant="ghost" onClick={() => setConfirmingPath(null)}>取消</Button>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <Button variant="ghost" onClick={() => { void openDetail(row) }}>查看</Button>
            <Button onClick={() => setConfirmingPath(row.path)}>批准</Button>
            <Button variant="ghost" onClick={() => { setRejecting(row); setRejectReason('') }}>驳回</Button>
          </span>
        )
      },
    },
  ]

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState title="审校包加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }
  if (rows.length === 0) {
    return <EmptyState title="暂无审校包" description="语料起草流程生成 *.proposals.json 后会出现在这里。" />
  }

  return (
    <div className="space-y-3">
      <StatCard label="待审" value={pending.length} suffix="个" tone={pending.length > 0 ? 'warn' : 'ok'} />
      <Card flush>
        <AntTable rowKey="path" columns={columns} dataSource={rows}
          pagination={{ pageSize: 10, showSizeChanger: false }} size="middle" />
      </Card>

      {/* diff 明细抽屉式弹窗 */}
      <Modal
        open={detail !== null} wide top
        title={`审校包明细：${detail?.path ?? ''}`}
        onClose={() => setDetail(null)} hideFooter
      >
        {detail && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sub">
              <StatusBadge pkg={detail} />
              <span className="text-ink-secondary">知识点 {detail.kp}</span>
              {detail.fingerprint && <span className="font-mono text-caption text-ink-muted">指纹 {detail.fingerprint.slice(0, 12)}…</span>}
            </div>
            {detail.parse_error ? (
              <p className="text-sub text-error m-0">解析失败：{detail.parse_error}</p>
            ) : (
              <ul className="space-y-1.5 m-0 p-0 list-none max-h-[50vh] overflow-y-auto">
                {(detail.edges ?? []).map((e, i) => (
                  <li key={i} className="text-sub font-mono text-[12px] rounded-[var(--radius-sm)] bg-surface-muted px-2.5 py-1.5">
                    {e.edge_type === 'prerequisite'
                      ? <span><CheckCircle2 size={12} className="inline text-primary mr-1" />{e.from_kp} → {e.to_kp}</span>
                      : <span><XCircle size={12} className="inline text-teal mr-1" />{e.kp} ↔ {e.substance || e.formula}</span>}
                    {e.reason && <span className="text-ink-muted ml-2">{e.reason}</span>}
                  </li>
                ))}
              </ul>
            )}
            {detail.status === 'approved' && detail.content_changed && (
              <p className="text-caption text-warning m-0">⚠️ 批准后文件已被修改 —— 请重新评估（指纹不一致）。</p>
            )}
          </div>
        )}
      </Modal>

      {/* 驳回（必附理由，R-I2/R-I5） */}
      <Modal
        open={rejecting !== null} title={`驳回 ${rejecting?.path ?? ''}`}
        onClose={() => setRejecting(null)} onConfirm={() => { void reject.run() }}
        confirmText={reject.state === 'busy' ? '提交中…' : '确认驳回'} danger
      >
        <Field label="驳回理由（必填）">
          <Textarea value={rejectReason} onChange={(e) => setRejectReason(e.target.value)}
            placeholder="说明驳回原因，将随记录留痕" rows={3} />
        </Field>
      </Modal>
    </div>
  )
}

/* ── 摄取任务面板（kb-ingest-ui）───────────────────────── */

const JOB_STATUS_LABEL: Record<string, string> = {
  queued: '排队中', running: '执行中', succeeded: '成功', failed: '失败', interrupted: '服务重启中断',
}

function JobStatusTag({ status }: { status: string }) {
  const tone = status === 'succeeded' ? 'green' : status === 'failed' ? 'red' : status === 'running' || status === 'queued' ? 'amber' : 'gray'
  return <Tag tone={tone}>{JOB_STATUS_LABEL[status] ?? status}</Tag>
}

function IngestPanel({ onChanged }: { onChanged?: () => void }) {
  const toast = useToast()
  const [modules, setModules] = useState<string[]>([])
  const [jobs, setJobs] = useState<KbJobRow[]>([])
  const [confirming, setConfirming] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [syncing, setSyncing] = useState(false)

  const load = useCallback(async () => {
    // 任务历史：status 端点没有 job 列表端点——从最近一次轮询拿不到列表，
    // 所以这里消费 /api/kb/status 的 modules + 最近任务的轮询由触发后跟踪。
    // 历史可见性依赖触发后的轮询与状态卡，避免再造一个列表端点。
  }, [])
  void load

  useEffect(() => {
    const r = api.get<{ success: boolean; modules: KbModule[] }>('/api/kb/status')
    void r.then((s) => setModules((s.modules ?? []).map((m) => m.module)))
  }, [])

  /* 触发后的轮询：跟踪最近一次 job 到终态 */
  const poll = useCallback(async (jobId: string) => {
    for (;;) {
      const r = await api.get<{ success: boolean; job: KbJobRow }>(`/api/kb/jobs/${jobId}`)
      setJobs((prev) => [r.job, ...prev.filter((j) => j.job_id !== jobId)])
      if (['succeeded', 'failed', 'interrupted'].includes(r.job.status)) {
        if (r.job.status === 'succeeded') {
          toast.success(`「${r.job.module}」摄取完成：${r.job.progress?.chunks ?? 0} 块入库`)
        } else {
          toast.error(`「${r.job.module}」摄取失败：${r.job.error ?? '未知错误'}`)
        }
        onChanged?.()
        return
      }
      await new Promise((res) => setTimeout(res, 1500))
    }
  }, [toast, onChanged])

  const submit = useAsyncAction(async () => {
    if (!confirming) return
    setSubmitting(true)
    try {
      const r = await api.post<{ success: boolean; job_id?: string; message?: string }>(
        '/api/kb/jobs', { module: confirming })
      if (!r.success || !r.job_id) { toast.error(r.message || '触发失败'); return }
      toast.success(`任务已提交（${r.job_id.slice(-8)}）`)
      setConfirming(null)
      void poll(r.job_id)
    } finally {
      setSubmitting(false)
    }
  })

  /* useAsyncAction 的 fn 不收参 —— 同步种类走 ref（同一 handler 内先写后跑） */
  const kindRef = useRef<'dictionary-sync' | 'tag-map-sync'>('dictionary-sync')
  const runSync = useAsyncAction(async () => {
    setSyncing(true)
    try {
      const kind = kindRef.current
      const r = await api.post<{ success: boolean; message?: string }>(`/api/kb/${kind}`)
      if (!r.success) { toast.error(r.message || '同步失败'); return }
      toast.success(kind === 'dictionary-sync' ? '词典已同步' : '标签映射已导入')
      onChanged?.()
    } finally {
      setSyncing(false)
    }
  })

  return (
    <Card title="摄取与同步">
      <div className="space-y-3">
        <p className="text-caption text-ink-secondary m-0">
          摄取在后台线程构建影子索引，成功才切换检索指向；失败自动清理、旧索引不受影响。
          同册已有进行中的任务时不能重复触发。
        </p>
        <div className="flex flex-wrap gap-2 items-center">
          {modules.map((m) => (
            <span key={m} className="inline-flex items-center gap-1.5">
              <Button variant="secondary" onClick={() => setConfirming(m)}>摄取 {m}</Button>
            </span>
          ))}
          <span className="flex-1" />
          <Button variant="ghost" disabled={syncing} onClick={() => { kindRef.current = 'dictionary-sync'; void runSync.run() }}>
            同步词典
          </Button>
          <Button variant="ghost" disabled={syncing} onClick={() => { kindRef.current = 'tag-map-sync'; void runSync.run() }}>
            导入标签映射
          </Button>
        </div>

        {/* 触发确认（R-I2 确认卡；摄取为长耗时动作） */}
        <Modal
          open={confirming !== null}
          title={`摄取「${confirming ?? ''}」`}
          onClose={() => setConfirming(null)}
          onConfirm={() => { void submit.run() }}
          confirmText={submitting ? '提交中…' : '开始摄取'}
        >
          <p className="text-sub m-0">
            将在后台重新摄取该册全部文档（嵌入计算，可能持续数分钟）。期间检索仍走旧索引；
            成功后自动切换并清理旧集合。该操作将写入操作日志。
          </p>
        </Modal>

        {/* 本会话触发的任务跟踪 */}
        {jobs.length > 0 && (
          <ul className="space-y-1.5 m-0 p-0 list-none">
            {jobs.map((j) => (
              <li key={j.job_id} className="text-sub flex items-center gap-2 rounded-[var(--radius-sm)] bg-surface-muted px-2.5 py-1.5">
                <span className="font-mono text-[12px]">{j.module}</span>
                <JobStatusTag status={j.status} />
                {j.progress?.chunks != null && <span className="text-ink-muted">{j.progress.chunks} 块</span>}
                {j.error && <span className="text-caption text-error truncate" title={j.error}>{j.error}</span>}
                <span className="ml-auto font-mono text-caption text-ink-muted">{j.job_id.slice(-8)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}


/* ── 文档与分块（球球式集中管理）───────────────────────── */

interface KbDocRow {
  source_path: string
  title: string
  module: string
  theme: string
  doc_type: string
  chunks: number
  disabled_chunks: number
}

interface KbChunkRow {
  chunk_id: string
  chunk_no: number | null
  section_no: number | null
  section_title: string
  title_path: string
  kp_ids: string
  body: string
  disabled: boolean
}

function DocsPanel() {
  const toast = useToast()
  const [docs, setDocs] = useState<KbDocRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [module, setModule] = useState('')
  const [q, setQ] = useState('')
  const [openDoc, setOpenDoc] = useState<KbDocRow | null>(null)
  const [chunks, setChunks] = useState<KbChunkRow[]>([])
  const [chunksLoading, setChunksLoading] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  /* 正文编辑（md 是 git 管理的真源：保存只写文件，重新摄取后才进索引） */
  const [editDoc, setEditDoc] = useState<{ path: string; module: string } | null>(null)
  const [editText, setEditText] = useState('')
  const [editLoading, setEditLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; documents: KbDocRow[] }>('/api/kb/documents')
      setDocs(r.documents ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const openChunks = async (doc: KbDocRow) => {
    setOpenDoc(doc)
    setChunksLoading(true)
    setChunks([])
    try {
      const r = await api.get<{ success: boolean; chunks: KbChunkRow[] }>(
        `/api/kb/documents/chunks?path=${encodeURIComponent(doc.source_path)}`)
      setChunks(r.chunks ?? [])
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '分块加载失败')
    } finally {
      setChunksLoading(false)
    }
  }

  const openEditor = async (doc: KbDocRow) => {
    setEditDoc({ path: doc.source_path, module: doc.module })
    setEditLoading(true)
    setEditText('')
    try {
      const r = await api.get<{ success: boolean; content: string }>(
        `/api/kb/documents/content?path=${encodeURIComponent(doc.source_path)}`
        + `&module=${encodeURIComponent(doc.module)}`)
      setEditText(r.content ?? '')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '正文加载失败')
      setEditDoc(null)
    } finally {
      setEditLoading(false)
    }
  }

  const modules = useMemo(
    () => [...new Set(docs.map((d) => d.module).filter(Boolean))].sort(),
    [docs],
  )
  const filtered = useMemo(() => docs.filter((d) => {
    if (module && d.module !== module) return false
    if (q.trim()) {
      const t = `${d.title} ${d.source_path} ${d.theme}`.toLowerCase()
      if (!t.includes(q.trim().toLowerCase())) return false
    }
    return true
  }), [docs, module, q])

  const columns: TableColumnsType<KbDocRow> = [
    {
      title: '文档', key: 'doc', ellipsis: true,
      render: (_, r) => (
        <span className="flex flex-col leading-tight">
          <span className="text-sub">{r.title}</span>
          <span className="font-mono text-caption text-ink-muted">{r.source_path}</span>
        </span>
      ),
    },
    { title: '模块', dataIndex: 'module', key: 'module', width: 130 },
    { title: '主题', dataIndex: 'theme', key: 'theme', width: 150, ellipsis: true },
    { title: '分块', dataIndex: 'chunks', key: 'chunks', width: 70 },
    {
      title: '已禁用', dataIndex: 'disabled_chunks', key: 'disabled_chunks', width: 90,
      render: (v: number) => (v > 0 ? <Tag tone="amber">{v} 块</Tag> : '—'),
    },
    {
      title: '操作', key: 'op', width: 190,
      render: (_, r) => (
        <span className="flex items-center gap-1">
          <Button variant="ghost" onClick={() => { void openChunks(r) }}>查看分块</Button>
          {can('knowledge:update') && (
            <Button variant="ghost" onClick={() => { void openEditor(r) }}>编辑正文</Button>
          )}
        </span>
      ),
    },
  ]

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState title="文档列表加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="模块" className="w-40">
          <Select value={module} onChange={(e) => setModule(e.target.value)}>
            <option value="">全部模块</option>
            {modules.map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
        </Field>
        <Field label="搜索" className="w-52">
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="文档名 / 主题 / 路径" />
        </Field>
        <span className="text-caption text-ink-muted pb-2">
          共 {filtered.length} 份文档 · 分块由「摄取」生成，正文修订请编辑 content/kb 下的 md 后重新摄取
        </span>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="没有匹配的文档" description="换个模块或关键词；若知识库还没摄取过，先到「状态与审校」触发摄取。" />
      ) : (
        <Card flush>
          <AntTable rowKey="source_path" columns={columns} dataSource={filtered}
            size="middle" pagination={{ pageSize: 15, showSizeChanger: false }} />
        </Card>
      )}

      <Modal
        open={openDoc !== null}
        title={openDoc ? `分块：${openDoc.title}` : ''}
        onClose={() => setOpenDoc(null)}
        hideFooter
        wide
        top
      >
        {chunksLoading ? <SkeletonCard /> : (
          <div className="space-y-2 max-h-[60vh] overflow-y-auto">
            <p className="text-caption text-ink-muted m-0">
              禁用后该块不再被检索命中（立即生效）；正文修订请改 md 后重新摄取。
            </p>
            {chunks.map((c) => (
              <div key={c.chunk_id} className={`rounded-[var(--radius-sm)] border px-3 py-2 ${c.disabled ? 'border-warning/30 bg-warning-soft/30' : 'border-border'}`}>
                <div className="flex items-center gap-2 text-caption text-ink-muted">
                  <Tag tone="blue">#{c.chunk_no}</Tag>
                  <span>{c.section_title || c.title_path}</span>
                  <span className="font-mono">{c.kp_ids}</span>
                  <span className="ml-auto flex items-center gap-2">
                    {c.disabled && <Tag tone="amber">已禁用</Tag>}
                    <ActionButton
                      variant="ghost"
                      busyLabel="…"
                      className="!px-2 !py-0.5 !text-[12px]"
                      onAction={async () => {
                        const r = await api.post<{ success: boolean }>(
                          `/api/kb/chunks/${c.chunk_id}/status`, { disabled: !c.disabled })
                        if (!r.success) { toast.error('操作失败'); throw new Error('failed') }
                        toast.success(!c.disabled ? '已禁用该块' : '已恢复该块')
                        if (openDoc) await openChunks(openDoc)
                        await load()
                      }}
                    >
                      {c.disabled ? '恢复' : '禁用'}
                    </ActionButton>
                  </span>
                </div>
                <div className={`mt-1 text-sub ${expanded === c.chunk_id ? '' : 'max-h-24 overflow-hidden'}`}>
                  <ChemMd text={c.body} />
                </div>
                <button
                  className="mt-1 text-caption text-primary cursor-pointer"
                  onClick={() => setExpanded(expanded === c.chunk_id ? null : c.chunk_id)}
                >
                  {expanded === c.chunk_id ? '收起' : '展开全文'}
                </button>
              </div>
            ))}
          </div>
        )}
      </Modal>

      {/* 正文编辑：保存只写 md；随后提示重新摄取 */}
      <Modal
        open={editDoc !== null}
        title={editDoc ? `编辑正文：${editDoc.path}` : ''}
        onClose={() => setEditDoc(null)}
        hideFooter
        wide
        top
      >
        {editLoading ? <SkeletonCard /> : (
          <div className="space-y-3">
            <p className="text-caption text-ink-muted m-0">
              正文的真源是 content/kb 下的 md 文件。保存后需要「重新摄取该册」才会进入检索索引。
            </p>
            <textarea
              className="w-full h-[45vh] rounded-[var(--radius-sm)] border border-border bg-surface p-3 font-mono text-[12px] leading-relaxed focus-visible:outline-2 focus-visible:outline-primary/40"
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
            />
            <div className="flex items-center gap-2">
              <ActionButton
                busyLabel="保存中…"
                onAction={async () => {
                  if (!editDoc) return
                  if (!editText.trim()) { toast.error('正文不能为空'); throw new Error('empty') }
                  const r = await api.put<{ success: boolean; module: string }>(
                    '/api/kb/documents/content',
                    { path: editDoc.path, content: editText, module: editDoc.module })
                  if (!r.success) { toast.error('保存失败'); throw new Error('failed') }
                  toast.success('正文已保存')
                  await load()
                }}
              >
                保存正文
              </ActionButton>
              <ActionButton
                variant="secondary"
                busyLabel="提交中…"
                onAction={async () => {
                  if (!editDoc?.module) { toast.error('无法确定所属册，请从「状态与审校」手动触发摄取'); throw new Error('no module') }
                  const r = await api.post<{ success: boolean; job_id?: string; message?: string }>(
                    '/api/kb/jobs', { module: editDoc.module })
                  if (!r.success || !r.job_id) { toast.error(r.message || '触发失败'); throw new Error('failed') }
                  toast.success(`已提交摄取任务（${editDoc.module}），完成后新正文才会被检索`)
                }}
              >
                重新摄取「{editDoc?.module}」
              </ActionButton>
              <Button variant="ghost" onClick={() => setEditDoc(null)}>关闭</Button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

/* ── 页面 ───────────────────────────────────────────── */

export function AdminKbPage() {
  const [tab, setTab] = useState('status')
  const [status, setStatus] = useState<KbStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setStatus(await api.get<KbStatus>('/api/kb/status'))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">KB 内容治理</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">语料状态总览、审校批准落库与摄取任务</p>
      </header>

      <Tabs
        items={[
          { key: 'status', label: '状态与审校' },
          { key: 'docs', label: '文档与分块' },
        ]}
        active={tab}
        onChange={setTab}
      />

      {tab === 'docs' ? <DocsPanel /> : loading ? <SkeletonCard /> : error ? (
        <EmptyState title="状态加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : status && (
        <>
          <StatusSection status={status} />
          <IngestPanel onChanged={() => { void load() }} />
          <ReviewQueue onChanged={() => { void load() }} />
        </>
      )}
    </div>
  )
}
