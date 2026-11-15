import { useCallback, useEffect, useRef, useState } from 'react'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { Play, Plus } from 'lucide-react'
import { api } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Modal, Select, SkeletonCard, StatCard,
  Tag, Textarea, useToast,
} from '../../components/ui'

interface GoldenRow {
  id: number
  question: string
  expected_chunks: Array<{ chunk_id: string; source_path?: string; section_no?: number }>
  match_mode: 'any' | 'all'
  tags?: string | null
  status: string
  source: string
  created_at?: string | null
}

interface EvalRun {
  run_id: string
  status: string
  metrics?: {
    total?: number
    passed?: number
    hit_rate?: number
    refusal_total?: number
    refusal_accuracy?: number | null
  } | null
  created_at?: string | null
  error?: string | null
}

interface DiffItem {
  id: number
  question?: string
}

interface CompareResult {
  baseline: { run_id: string; metrics?: { hit_rate?: number } }
  candidate: { run_id: string; metrics?: { hit_rate?: number } }
  diff: {
    new_fail: DiffItem[]
    fixed: DiffItem[]
    still_fail: DiffItem[]
    still_pass_count: number
  }
}

function pct(v?: number | null) {
  return v == null ? '—' : `${Math.round(v * 100)}%`
}

/** 评测与回归（/admin/rag/evals）：金标集 → 跑评测 → baseline 逐题对比。 */
export function RagEvalsPage() {
  const toast = useToast()
  const [golden, setGolden] = useState<GoldenRow[]>([])
  const [runs, setRuns] = useState<EvalRun[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [running, setRunning] = useState<string | null>(null)
  const [baseline, setBaseline] = useState('')
  const [candidate, setCandidate] = useState('')
  const [comparison, setComparison] = useState<CompareResult | null>(null)
  const [creating, setCreating] = useState(false)
  const [newQ, setNewQ] = useState('')
  const [newMode, setNewMode] = useState<'any' | 'all'>('any')
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [g, e] = await Promise.all([
        api.get<{ success: boolean; questions: GoldenRow[] }>('/api/rag/golden-questions'),
        api.get<{ success: boolean; runs: EvalRun[] }>('/api/rag/evals'),
      ])
      setGolden(g.questions ?? [])
      setRuns(e.runs ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])
  useEffect(() => () => { if (timer.current) clearInterval(timer.current) }, [])

  const activeCount = golden.filter((g) => g.status === 'active').length

  async function toggleStatus(g: GoldenRow) {
    const next = g.status === 'active' ? 'paused' : 'active'
    const r = await api.put<{ success: boolean }>(`/api/rag/golden-questions/${g.id}`, {
      question: g.question, expected_chunks: g.expected_chunks,
      match_mode: g.match_mode, tags: g.tags, status: next,
    })
    if (!r.success) { toast.error('更新失败'); return }
    await load()
  }

  async function removeGolden(g: GoldenRow) {
    const r = await api.del<{ success: boolean }>(`/api/rag/golden-questions/${g.id}`)
    if (!r.success) { toast.error('删除失败'); return }
    await load()
  }

  async function createGolden() {
    if (!newQ.trim()) { toast.error('问题不能为空'); return }
    const r = await api.post<{ success: boolean; question?: GoldenRow }>('/api/rag/golden-questions', {
      question: newQ.trim(), expected_chunks: [], match_mode: newMode, status: 'active',
    })
    if (!r.success) { toast.error('创建失败'); return }
    toast.success('已创建（未选期望块时只会统计拒答正确率）')
    setCreating(false)
    setNewQ('')
    await load()
  }

  async function startEval() {
    if (activeCount === 0) { toast.error('先在金标集里启用至少 1 题'); return }
    const r = await api.post<{ success: boolean; run_id?: string; message?: string }>('/api/rag/evals/run')
    if (!r.success || !r.run_id) { toast.error(r.message || '启动失败'); return }
    setRunning(r.run_id)
    toast.success('评测已启动，完成后自动刷新')
    if (timer.current) clearInterval(timer.current)
    timer.current = setInterval(async () => {
      const s = await api.get<{ success: boolean; run: EvalRun }>(`/api/rag/evals/${r.run_id}`)
      if (s.run.status === 'succeeded' || s.run.status === 'failed') {
        if (timer.current) clearInterval(timer.current)
        setRunning(null)
        if (s.run.status === 'failed') toast.error(s.run.error || '评测失败')
        else toast.success('评测完成')
        await load()
      }
    }, 2000)
  }

  async function compare() {
    if (!baseline || !candidate) { toast.error('选择基线版与对比版'); return }
    const r = await api.get<CompareResult & { success: boolean }>(
      `/api/rag/eval-comparison?baseline=${baseline}&candidate=${candidate}`)
    setComparison(r)
  }

  const goldenColumns: TableColumnsType<GoldenRow> = [
    { title: '#', dataIndex: 'id', key: 'id', width: 56 },
    { title: '问题', dataIndex: 'question', key: 'question', ellipsis: true },
    { title: '期望块', key: 'chunks', width: 90,
      render: (_, r) => `${r.expected_chunks?.length ?? 0} 个` },
    { title: '匹配', dataIndex: 'match_mode', key: 'match_mode', width: 90,
      render: (v) => <Tag tone="blue">{v === 'all' ? '全部命中' : '任一命中'}</Tag> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v) => <Tag tone={v === 'active' ? 'green' : v === 'stale' ? 'amber' : 'gray'}>
        {v === 'active' ? '启用' : v === 'paused' ? '暂停' : v === 'draft' ? '草稿' : v}
      </Tag> },
    { title: '来源', dataIndex: 'source', key: 'source', width: 90,
      render: (v) => ({ manual: '手工', trace: '日志回流', retrieval_test: '召回测试' }[v as string] ?? v) },
    {
      title: '操作', key: 'op', width: 140,
      render: (_, r) => (
        <span className="flex items-center gap-1">
          <Button variant="ghost" onClick={() => { void toggleStatus(r) }}>
            {r.status === 'active' ? '暂停' : '启用'}
          </Button>
          <Button variant="ghost" onClick={() => { void removeGolden(r) }}>删除</Button>
        </span>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold text-ink m-0">评测与回归</h1>
          <p className="text-sub text-ink-secondary mt-1 mb-0">
            金标集 {golden.length} 题（启用 {activeCount}）· 改完知识库跑一轮，看「修好了哪些、弄坏了哪些」
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" onClick={() => setCreating(true)}>
            <Plus size={14} className="inline mr-1" />新增金标
          </Button>
          <Button onClick={() => { void startEval() }} disabled={running !== null || activeCount === 0}>
            <Play size={14} className="inline mr-1" />{running ? '评测中…' : '跑评测'}
          </Button>
        </div>
      </header>

      {loading ? <SkeletonCard /> : error ? (
        <EmptyState title="加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : (
        <>
          {golden.length === 0 ? (
            <EmptyState
              title="还没有金标问题"
              description="三个来源：手工新增、问答日志「加入金标集」、召回测试勾选命中块保存。"
              action={<Button onClick={() => setCreating(true)}>新增第一题</Button>}
            />
          ) : (
            <Card flush>
              <AntTable rowKey="id" columns={goldenColumns} dataSource={golden}
                size="middle" pagination={{ pageSize: 10, showSizeChanger: false }} />
            </Card>
          )}

          <Card title="评测历史与对比">
            {runs.length === 0 ? (
              <p className="text-sub text-ink-secondary m-0">还没有跑过评测。</p>
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-end gap-3">
                  <Field label="基线版" className="w-56">
                    <Select value={baseline} onChange={(e) => setBaseline(e.target.value)}>
                      <option value="">选择…</option>
                      {runs.filter((r) => r.status === 'succeeded').map((r) => (
                        <option key={r.run_id} value={r.run_id}>
                          {r.run_id}（{pct(r.metrics?.hit_rate)}）
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Field label="对比版" className="w-56">
                    <Select value={candidate} onChange={(e) => setCandidate(e.target.value)}>
                      <option value="">选择…</option>
                      {runs.filter((r) => r.status === 'succeeded').map((r) => (
                        <option key={r.run_id} value={r.run_id}>
                          {r.run_id}（{pct(r.metrics?.hit_rate)}）
                        </option>
                      ))}
                    </Select>
                  </Field>
                  <Button onClick={() => { void compare() }}>对比</Button>
                </div>

                <table className="w-full text-sub">
                  <thead>
                    <tr className="text-left text-caption text-ink-muted border-b border-border">
                      <th className="py-1.5">运行</th><th>状态</th><th>命中率</th><th>拒答正确率</th><th>时间</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.run_id} className="border-b border-border last:border-0">
                        <td className="py-1.5 font-mono text-caption">{r.run_id}</td>
                        <td><Tag tone={r.status === 'succeeded' ? 'green' : r.status === 'failed' ? 'red' : 'amber'}>
                          {r.status === 'succeeded' ? '成功' : r.status === 'failed' ? '失败' : '进行中'}
                        </Tag></td>
                        <td>{pct(r.metrics?.hit_rate)}（{r.metrics?.passed ?? '—'}/{r.metrics?.total ?? '—'}）</td>
                        <td>{pct(r.metrics?.refusal_accuracy)}</td>
                        <td className="text-caption text-ink-muted">{r.created_at?.replace('T', ' ').slice(5, 16) ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {comparison && (
            <Card title={`对比：${comparison.baseline.run_id} → ${comparison.candidate.run_id}`}>
              <div className="grid grid-cols-4 gap-3 mb-3 max-[1099px]:grid-cols-2">
                <StatCard label="基线命中率" value={pct(comparison.baseline.metrics?.hit_rate)} />
                <StatCard label="对比命中率" value={pct(comparison.candidate.metrics?.hit_rate)} />
                <StatCard label="新增失败" value={comparison.diff.new_fail.length}
                  tone={comparison.diff.new_fail.length ? 'danger' : 'ok'} />
                <StatCard label="修复" value={comparison.diff.fixed.length} tone="ok" />
              </div>
              {comparison.diff.new_fail.length > 0 && (
                <div className="mb-2">
                  <div className="text-caption text-error mb-1">弄坏了（基线过 → 对比挂）</div>
                  <ul className="m-0 pl-5 text-sub">
                    {comparison.diff.new_fail.map((x) => <li key={x.id}>#{x.id} {x.question}</li>)}
                  </ul>
                </div>
              )}
              {comparison.diff.fixed.length > 0 && (
                <div>
                  <div className="text-caption text-success mb-1">修好了（基线挂 → 对比过）</div>
                  <ul className="m-0 pl-5 text-sub">
                    {comparison.diff.fixed.map((x) => <li key={x.id}>#{x.id} {x.question}</li>)}
                  </ul>
                </div>
              )}
              {comparison.diff.new_fail.length === 0 && comparison.diff.fixed.length === 0 && (
                <p className="text-sub text-ink-secondary m-0">
                  逐题结果完全一致（仍通过 {comparison.diff.still_pass_count}，仍失败 {comparison.diff.still_fail.length}）。
                </p>
              )}
            </Card>
          )}
        </>
      )}

      <Modal
        open={creating}
        title="新增金标问题"
        onClose={() => setCreating(false)}
        onConfirm={() => { void createGolden() }}
        confirmText="创建"
      >
        <div className="space-y-3">
          <Field label="问题">
            <Textarea value={newQ} onChange={(e) => setNewQ(e.target.value)} rows={3}
              placeholder="如：电解池的阴阳极怎么判断？" />
          </Field>
          <Field label="匹配规则">
            <Select value={newMode} onChange={(e) => setNewMode(e.target.value as 'any' | 'all')}>
              <option value="any">任一命中</option>
              <option value="all">全部命中</option>
            </Select>
          </Field>
          <p className="text-caption text-ink-muted m-0">
            这里只建问题；期望块请在「召回测试」里检索后勾选保存（会自动带上块 id）。
            没有期望块的题只参与「拒答正确率」统计。
          </p>
        </div>
      </Modal>
    </div>
  )
}
