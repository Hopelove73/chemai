import { useCallback, useEffect, useState } from 'react'
import { Search } from 'lucide-react'
import { api } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Input, Select, Tag, Textarea, useToast,
} from '../../components/ui'

interface Hit {
  chunk_id: string
  source_path?: string
  section_no?: number
  title_path?: string
  body?: string
  score?: number
  from_graph?: boolean
}

interface HistoryRow {
  id: number
  query: string
  top_k?: number | null
  threshold?: number | null
  graph_enabled?: boolean
  mode?: string | null
  hits_count: number
  top1_score?: number | null
  elapsed_ms?: number | null
  created_at?: string | null
}

interface TestResult {
  success: boolean
  mode?: string
  elapsed_ms?: number
  applied_config?: { top_k: number; threshold: number; graph_enabled: boolean }
  default_config?: { top_k: number; threshold: number | null; graph_enabled: boolean }
  hits: Hit[]
}

const EXAMPLES = [
  '电解池的阴阳极怎么判断？',
  '化学平衡移动的判断依据是什么？',
  '乙醇和乙酸怎么鉴别？',
]

/**
 * 召回测试（/admin/rag/retrieval）—— 临时参数下跑与线上同一条检索管线。
 * 参数**不落库**、不写问答日志；结果可沉淀为金标或坏案例。
 */
export function RagRetrievalPage() {
  const toast = useToast()
  const [query, setQuery] = useState('')
  const [topK, setTopK] = useState('6')
  const [threshold, setThreshold] = useState('0.5')
  const [graph, setGraph] = useState(true)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [goldenMode, setGoldenMode] = useState<'any' | 'all'>('any')
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState<HistoryRow[]>([])

  const loadHistory = useCallback(async () => {
    try {
      const r = await api.get<{ success: boolean; tests: HistoryRow[] }>(
        '/api/rag/retrieval-tests?limit=10')
      setHistory(r.tests ?? [])
    } catch {
      // 历史读不到不影响测试本身
    }
  }, [])

  useEffect(() => { void loadHistory() }, [loadHistory])

  async function run(qOverride?: string) {
    const q = (qOverride ?? query).trim()
    if (!q) { toast.error('先输入一个问题'); return }
    setLoading(true)
    setResult(null)
    setSelected([])
    try {
      const r = await api.post<TestResult>('/api/rag/retrieval-test', {
        query: q,
        top_k: Number(topK) || 6,
        threshold: Number(threshold) || 0.5,
        graph_enabled: graph,
      })
      setResult(r)
      await loadHistory()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '检索失败')
    } finally {
      setLoading(false)
    }
  }

  async function saveGolden() {
    if (!result) return
    if (selected.length === 0) { toast.error('勾选至少一个期望命中的块'); return }
    setSaving(true)
    try {
      const chunks = result.hits
        .filter((h) => selected.includes(h.chunk_id))
        .map((h) => ({ chunk_id: h.chunk_id, source_path: h.source_path, section_no: h.section_no }))
      const r = await api.post<{ success: boolean; question?: { id: number } }>(
        '/api/rag/golden-questions',
        { question: query.trim(), expected_chunks: chunks, match_mode: goldenMode, status: 'draft' })
      if (!r.success) { toast.error('保存失败'); return }
      toast.success(`已存为金标问题（#${r.question?.id}），去「评测回归」启用`)
    } finally {
      setSaving(false)
    }
  }

  async function markBad() {
    if (!result) return
    await api.post<{ success: boolean }>('/api/rag/retrieval-test/bad-case', {
      query: query.trim(),
      hits: result.hits.map((h) => ({ chunk_id: h.chunk_id, score: h.score })),
      reason: result.hits.length === 0 ? '缺知识' : '检索错',
    })
    toast.success('已记为坏案例（可在问答日志按「坏」筛选）')
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">召回测试</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          用与线上相同的检索管线试参数；只影响本次测试，不改默认配置
        </p>
      </header>

      <Card>
        <div className="space-y-3">
          <Field label="问题">
            <Textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={2}
              placeholder="如：电解池的阴阳极怎么判断？" />
          </Field>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Top K" className="w-28">
              <Input value={topK} onChange={(e) => setTopK(e.target.value)} />
            </Field>
            <Field label="相似度阈值" className="w-32">
              <Input value={threshold} onChange={(e) => setThreshold(e.target.value)} />
            </Field>
            <label className="flex items-center gap-1.5 text-sub text-ink-secondary pb-2 cursor-pointer">
              <input type="checkbox" checked={graph} onChange={(e) => setGraph(e.target.checked)} />
              启用图增强
            </label>
            <Button onClick={() => { void run() }} disabled={loading}>
              <Search size={14} className="inline mr-1" />{loading ? '检索中…' : '检索'}
            </Button>
          </div>
        </div>
      </Card>

      {history.length > 0 && (
        <Card title="最近测试（点击重跑）">
          <div className="space-y-1">
            {history.map((h) => (
              <button
                key={h.id}
                onClick={() => { setQuery(h.query); void run(h.query) }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-[var(--radius-sm)] text-left text-sub hover:bg-surface-muted transition-colors cursor-pointer"
              >
                <Tag tone={h.mode === 'ok' ? 'green' : h.mode === 'weak' ? 'amber' : 'gray'}>
                  {h.mode === 'ok' ? '命中' : h.mode === 'weak' ? '弱命中' : '未命中'}
                </Tag>
                <span className="min-w-0 flex-1 truncate text-ink">{h.query}</span>
                <span className="font-mono text-caption text-ink-muted">
                  {h.hits_count} 块 · Top{h.top_k ?? '?'} · {h.elapsed_ms ?? '—'}ms
                </span>
                <span className="text-caption text-ink-muted">
                  {h.created_at?.replace('T', ' ').slice(5, 16) ?? ''}
                </span>
              </button>
            ))}
          </div>
          <p className="text-caption text-ink-muted mt-2 mb-0">
            历史只记测试参数与结果摘要，不写入问答指标（问答指标只统计真实问答）。
          </p>
        </Card>
      )}

      {!result && !loading && history.length === 0 && (
        <Card>
          <EmptyState
            title="输入一个问题，看知识库能召回什么"
            description="检索与线上完全同一条管线（真嵌入 + 真语料）；点下面的示例可以直接跑一次。"
            action={
              <div className="flex flex-wrap gap-2 justify-center">
                {EXAMPLES.map((q) => (
                  <Button key={q} variant="secondary" onClick={() => { setQuery(q); void run(q) }}>
                    {q}
                  </Button>
                ))}
              </div>
            }
          />
        </Card>
      )}

      {result && (
        <>
          <Card>
            <div className="flex flex-wrap items-center gap-3 text-sub">
              <Tag tone={result.mode === 'ok' ? 'green' : result.mode === 'weak' ? 'amber' : 'gray'}>
                {result.mode === 'ok' ? '命中' : result.mode === 'weak' ? '弱命中' : '未命中'}
              </Tag>
              <span className="text-ink-secondary">耗时 {result.elapsed_ms} ms</span>
              <span className="text-caption text-ink-muted">
                本次参数：TopK {result.applied_config?.top_k} · 阈值 {result.applied_config?.threshold} ·
                图增强 {result.applied_config?.graph_enabled ? '开' : '关'}
                （默认：TopK {result.default_config?.top_k}）
              </span>
            </div>
          </Card>

          {result.hits.length === 0 ? (
            <EmptyState
              title="没有召回任何分段"
              description="换一种问法、提高 TopK 或降低阈值；如果仍然为空，说明语料没覆盖这个知识点。"
              action={<Button onClick={() => { void markBad() }}>记为坏案例</Button>}
            />
          ) : (
            <Card title={`命中 ${result.hits.length} 块（勾选可存为金标）`}>
              <div className="space-y-2">
                {result.hits.map((h, i) => (
                  <label key={h.chunk_id}
                    className="flex items-start gap-2 rounded-[var(--radius-sm)] border border-border px-3 py-2 cursor-pointer hover:border-primary/40">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={selected.includes(h.chunk_id)}
                      onChange={(e) => setSelected((prev) => e.target.checked
                        ? [...prev, h.chunk_id]
                        : prev.filter((x) => x !== h.chunk_id))}
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-caption text-ink-muted">
                        <Tag tone="blue">#{i + 1}</Tag>
                        <span className="font-mono">{h.score?.toFixed(2)}</span>
                        <span>{h.source_path}{h.section_no ? ` · §${h.section_no}` : ''}</span>
                        {h.from_graph && <Tag tone="teal">图扩展</Tag>}
                      </div>
                      <div className="text-sub text-ink-secondary mt-1 line-clamp-3">{h.body}</div>
                    </div>
                  </label>
                ))}
              </div>
              <div className="flex flex-wrap items-end gap-3 mt-3">
                <Field label="匹配规则" className="w-36">
                  <Select value={goldenMode} onChange={(e) => setGoldenMode(e.target.value as 'any' | 'all')}>
                    <option value="any">任一命中</option>
                    <option value="all">全部命中</option>
                  </Select>
                </Field>
                <Button onClick={() => { void saveGolden() }} disabled={saving || selected.length === 0}>
                  保存为金标（{selected.length}）
                </Button>
                <Button variant="ghost" onClick={() => { void markBad() }}>记为坏案例</Button>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  )
}
