import { useCallback, useEffect, useState } from 'react'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { api } from '../../lib/api'
import {
  Badge, Button, Card, EmptyState, Field, Modal, Select, SkeletonCard, StatCard,
  Tag, Textarea, useToast,
} from '../../components/ui'

interface Metrics {
  total: number
  hit_rate: number | null
  zero_hit_rate: number | null
  refusal_rate: number | null
  negative_feedback_rate: number | null
  low_score_rate: number | null
  low_score_threshold: number
}

interface TraceRow {
  trace_id: string
  user_id?: string | null
  question: string
  hits_count: number
  zero_hit: boolean
  top1_score?: number | null
  refusal_flag: boolean
  latency_ms?: number | null
  model_name?: string | null
  feedback: string
  ops_label?: string | null
  bad_reason?: string | null
  created_at?: string | null
}

interface TraceDetail extends TraceRow {
  answer?: string | null
  retrieval_config?: Record<string, unknown> | null
  kb_index_version?: string | null
  hits: Array<{ chunk_id: string; source_path?: string; section_no?: number; score?: number; body_preview?: string }>
}

const BAD_REASONS = ['检索错', 'chunk差', '缺知识', '幻觉', '不完整', '超纲', '误拒']

function pct(v: number | null | undefined) {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

/**
 * 问答日志（/admin/rag/traces）—— RAG 运营的核心页。
 * 七段证据链：问题 / 检索配置 / 命中块 / 回答 / 模型与延迟 / 反馈 / 标注。
 */
export function RagTracesPage() {
  const toast = useToast()
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [rows, setRows] = useState<TraceRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [days, setDays] = useState('7')
  const [refusal, setRefusal] = useState('')
  const [zeroHit, setZeroHit] = useState('')
  const [lowScore, setLowScore] = useState('')
  const [feedback, setFeedback] = useState('')
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [labeling, setLabeling] = useState<{ trace: TraceRow; label: 'good' | 'bad' } | null>(null)
  const [badReason, setBadReason] = useState(BAD_REASONS[0])
  const [corrected, setCorrected] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const qs = new URLSearchParams({ days })
      if (refusal) qs.set('refusal', refusal)
      if (zeroHit) qs.set('zero_hit', zeroHit)
      if (lowScore) qs.set('low_score', lowScore)
      if (feedback) qs.set('feedback', feedback)
      const [m, t] = await Promise.all([
        api.get<Metrics & { success: boolean }>(`/api/rag/metrics?days=${days}`),
        api.get<{ success: boolean; traces: TraceRow[] }>(`/api/rag/traces?${qs}`),
      ])
      setMetrics(m)
      setRows(t.traces ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [days, refusal, zeroHit, lowScore, feedback])

  useEffect(() => { void load() }, [load])

  const openDetail = async (row: TraceRow) => {
    const r = await api.get<{ success: boolean; trace: TraceDetail }>(`/api/rag/traces/${row.trace_id}`)
    setDetail(r.trace)
  }

  async function submitLabel() {
    if (!labeling) return
    const body = labeling.label === 'good'
      ? { label: 'good' }
      : { label: 'bad', bad_reason: badReason, corrected_answer: corrected || undefined }
    const r = await api.post<{ success: boolean }>(`/api/rag/traces/${labeling.trace.trace_id}/label`, body)
    if (!r.success) { toast.error('标注失败'); return }
    toast.success(labeling.label === 'good' ? '已标为好' : '已标为坏')
    setLabeling(null)
    setCorrected('')
    await load()
  }

  const columns: TableColumnsType<TraceRow> = [
    {
      title: '时间', dataIndex: 'created_at', key: 'created_at', width: 150,
      render: (v) => <span className="font-mono text-[12px]">{v ? v.replace('T', ' ').slice(5, 16) : '—'}</span>,
    },
    { title: '学生', dataIndex: 'user_id', key: 'user_id', width: 100 },
    { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true },
    {
      title: '命中', dataIndex: 'hits_count', key: 'hits_count', width: 70,
      render: (v: number) => (v === 0 ? <Tag tone="red">0</Tag> : v),
    },
    {
      title: 'Top1', dataIndex: 'top1_score', key: 'top1_score', width: 80,
      render: (v?: number | null) => v == null ? '—' : (
        <span className={`font-mono ${v < 0.5 ? 'text-warning' : ''}`}>{v.toFixed(2)}</span>
      ),
    },
    {
      title: '状态', key: 'state', width: 150,
      render: (_, r) => (
        <span className="flex flex-wrap gap-1">
          {r.refusal_flag && <Tag tone="amber">拒答</Tag>}
          {r.zero_hit && <Tag tone="red">零召回</Tag>}
          {r.feedback === 'down' && <Tag tone="purple">点踩</Tag>}
          {r.ops_label === 'bad' && <Tag tone="red">坏·{r.bad_reason}</Tag>}
          {r.ops_label === 'good' && <Tag tone="green">好</Tag>}
        </span>
      ),
    },
    {
      title: '操作', key: 'op', width: 160,
      render: (_, r) => (
        <span className="flex items-center gap-1 whitespace-nowrap">
          <Button variant="ghost" onClick={() => { void openDetail(r) }}>查看</Button>
          <Button variant="ghost" onClick={() => { setLabeling({ trace: r, label: 'bad' }); setBadReason(BAD_REASONS[0]); setCorrected('') }}>标坏</Button>
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">问答日志</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          每一次 AI 问答的证据链：问题 → 命中了什么 → 回答 → 反馈
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="时间窗" className="w-32">
          <Select value={days} onChange={(e) => setDays(e.target.value)}>
            <option value="1">今天</option>
            <option value="7">近 7 天</option>
            <option value="30">近 30 天</option>
          </Select>
        </Field>
        <Field label="拒答" className="w-28">
          <Select value={refusal} onChange={(e) => setRefusal(e.target.value)}>
            <option value="">全部</option>
            <option value="yes">仅拒答</option>
            <option value="no">非拒答</option>
          </Select>
        </Field>
        <Field label="零召回" className="w-28">
          <Select value={zeroHit} onChange={(e) => setZeroHit(e.target.value)}>
            <option value="">全部</option>
            <option value="yes">仅零召回</option>
          </Select>
        </Field>
        <Field label="低分命中" className="w-28">
          <Select value={lowScore} onChange={(e) => setLowScore(e.target.value)}>
            <option value="">全部</option>
            <option value="yes">仅低分</option>
          </Select>
        </Field>
        <Field label="反馈" className="w-28">
          <Select value={feedback} onChange={(e) => setFeedback(e.target.value)}>
            <option value="">全部</option>
            <option value="down">点踩</option>
            <option value="up">点赞</option>
          </Select>
        </Field>
      </div>

      {metrics && (
        <div className="grid grid-cols-5 gap-3 max-[1099px]:grid-cols-2">
          <StatCard label="问答量" value={metrics.total} />
          <StatCard label="检索命中率" value={pct(metrics.hit_rate)} tone="ok" />
          <StatCard label="零召回率" value={pct(metrics.zero_hit_rate)}
            tone={(metrics.zero_hit_rate ?? 0) > 0.1 ? 'warn' : 'normal'} />
          <StatCard label="拒答率" value={pct(metrics.refusal_rate)} />
          <StatCard label="负反馈率" value={pct(metrics.negative_feedback_rate)}
            tone={(metrics.negative_feedback_rate ?? 0) > 0.05 ? 'danger' : 'normal'} />
        </div>
      )}

      {loading ? <SkeletonCard /> : error ? (
        <EmptyState title="日志加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="这个时间窗没有问答记录"
          description="问答日志从埋点开启后开始累计；先让学生问一个问题，再看这里。"
        />
      ) : (
        <Card flush>
          <AntTable rowKey="trace_id" columns={columns} dataSource={rows}
            size="middle" pagination={{ pageSize: 15, showSizeChanger: false }}
            onRow={(r) => ({ onClick: () => { void openDetail(r) }, className: 'cursor-pointer' })} />
        </Card>
      )}

      {/* 七段证据链 */}
      <Modal
        open={detail !== null}
        title="问答详情"
        onClose={() => setDetail(null)}
        hideFooter
        wide
        top
      >
        {detail && (
          <div className="space-y-4 max-h-[65vh] overflow-y-auto">
            <section>
              <div className="text-caption text-ink-muted mb-1">① 问题</div>
              <div className="text-sub text-ink">{detail.question}</div>
            </section>
            <section>
              <div className="text-caption text-ink-muted mb-1">② 检索配置</div>
              <pre className="text-caption font-mono bg-surface-muted rounded-[var(--radius-sm)] p-2 m-0 break-all">
                {JSON.stringify(detail.retrieval_config ?? {}, null, 0)}
              </pre>
            </section>
            <section>
              <div className="text-caption text-ink-muted mb-1">③ 命中块（{detail.hits.length}）</div>
              {detail.hits.length === 0 ? (
                <div className="text-sub text-warning">零召回 —— 知识库没有覆盖这个问法。</div>
              ) : (
                <ul className="space-y-1.5 m-0 p-0 list-none">
                  {detail.hits.map((h) => (
                    <li key={h.chunk_id} className="rounded-[var(--radius-sm)] border border-border px-3 py-2">
                      <div className="flex items-center gap-2 text-caption text-ink-muted">
                        <Tag tone="blue">{h.score?.toFixed(2) ?? '—'}</Tag>
                        <span className="font-mono">{h.source_path}{h.section_no ? ` · §${h.section_no}` : ''}</span>
                        <span className="font-mono ml-auto">{h.chunk_id}</span>
                      </div>
                      <div className="text-sub text-ink-secondary mt-1">{h.body_preview}</div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section>
              <div className="text-caption text-ink-muted mb-1">④ 回答</div>
              <div className="text-sub whitespace-pre-wrap">{detail.answer || '—'}</div>
            </section>
            <section>
              <div className="text-caption text-ink-muted mb-1">⑤ 模型与延迟</div>
              <div className="text-caption text-ink-secondary">
                {detail.model_name ?? '—'} · {detail.latency_ms ?? '—'} ms · 索引 {detail.kb_index_version ?? '—'}
              </div>
            </section>
            <section className="flex items-center gap-3">
              <div>
                <div className="text-caption text-ink-muted mb-1">⑥ 学生反馈</div>
                <Badge status={detail.feedback === 'down' ? 'warning' : detail.feedback === 'up' ? 'pass' : 'idle'}
                  label={detail.feedback === 'down' ? '没帮助' : detail.feedback === 'up' ? '有帮助' : '无'} />
              </div>
              <div>
                <div className="text-caption text-ink-muted mb-1">⑦ 运营标注</div>
                <span className="text-sub">{detail.ops_label === 'bad' ? `坏 · ${detail.bad_reason}` : detail.ops_label === 'good' ? '好' : '未标注'}</span>
              </div>
            </section>
            <div className="flex items-center gap-2 pt-2 border-t border-border">
              <Button onClick={() => { setLabeling({ trace: detail, label: 'bad' }); setDetail(null) }}>标为坏</Button>
              <Button variant="ghost" onClick={async () => {
                const r = await api.post<{ success: boolean; golden_id?: number }>(`/api/rag/traces/${detail.trace_id}/to-golden`)
                if (!r.success) { toast.error('回流失败'); return }
                toast.success(`已加入金标集（#${r.golden_id}），去「评测回归」启用`)
              }}>加入金标集</Button>
            </div>
          </div>
        )}
      </Modal>

      {/* 标注坏案例 */}
      <Modal
        open={labeling !== null}
        title="标记为坏案例"
        onClose={() => setLabeling(null)}
        onConfirm={() => { void submitLabel() }}
        confirmText="保存标注"
        danger
      >
        <div className="space-y-3">
          <Field label="原因">
            <Select value={badReason} onChange={(e) => setBadReason(e.target.value)}>
              {BAD_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </Select>
          </Field>
          <Field label="修正后的答案（可选，会覆盖原回答）">
            <Textarea value={corrected} onChange={(e) => setCorrected(e.target.value)} rows={4} />
          </Field>
        </div>
      </Modal>
    </div>
  )
}
