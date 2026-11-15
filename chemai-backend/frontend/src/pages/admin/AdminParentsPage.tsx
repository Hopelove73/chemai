import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { api } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Modal, Select, SkeletonCard,
  Tabs, Tag, useToast,
} from '../../components/ui'
import { ActionButton } from '../../components/ui/ActionButton'

interface ParentSummary {
  bound: number
  activated: number
  active_7d: number | null
  active_7d_available: boolean
}

interface ParentRow {
  parent_id: string
  name: string
  phone?: string | null
  status: string
  account_status: 'active' | 'disabled' | 'none'
  username?: string | null
  children_count: number
  children: Array<{ student_id: string; name: string }>
}

interface BindingRow {
  binding_id: string
  student_id: string
  student_name: string
  class_name: string
  parent_id: string
  parent_name: string
  parent_phone?: string | null
  relation: string
  status: string
  bind_code: string
  flags: string[]
}

const FLAG_LABEL: Record<string, string> = {
  dup: '重复手机号',
  unactivated: '未激活',
  over: '一孩多家长',
}

function AccountTag({ status }: { status: ParentRow['account_status'] }) {
  if (status === 'active') return <Tag tone="green">已激活</Tag>
  if (status === 'disabled') return <Tag tone="gray">已停用</Tag>
  return <Tag tone="amber">未激活</Tag>
}

/**
 * 家长与绑定（/admin/parents）—— 核心是「680 条绑定里多少人还没激活」。
 * 支持从概览漏斗带参进入：?seg=activated|unactivated
 */
export function AdminParentsPage() {
  const toast = useToast()
  const [params, setParams] = useSearchParams()
  const seg = params.get('seg') ?? ''
  const [tab, setTab] = useState<'accounts' | 'bindings'>('accounts')
  const [summary, setSummary] = useState<ParentSummary | null>(null)
  const [parents, setParents] = useState<ParentRow[]>([])
  const [bindings, setBindings] = useState<BindingRow[]>([])
  const [anomalies, setAnomalies] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [issue, setIssue] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [issued, setIssued] = useState<Array<{ name: string; username: string; password: string }> | null>(null)
  const [skipped, setSkipped] = useState<Array<{ name: string; reason: string }>>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const qs = new URLSearchParams()
      if (seg === 'activated') qs.set('activated', 'yes')
      if (seg === 'unactivated') qs.set('activated', 'no')
      const [sum, p, b] = await Promise.all([
        api.get<ParentSummary & { success: boolean }>('/api/account-center/parents/summary'),
        api.get<{ success: boolean; parents: ParentRow[] }>(`/api/account-center/parents?${qs}`),
        api.get<{ success: boolean; bindings: BindingRow[]; anomalies: Record<string, number> }>(
          `/api/account-center/parent-bindings${issue ? `?issue=${issue}` : ''}`),
      ])
      setSummary(sum)
      setParents(p.parents ?? [])
      setBindings(b.bindings ?? [])
      setAnomalies(b.anomalies ?? {})
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [seg, issue])

  useEffect(() => { void load() }, [load])

  const batchCreate = async () => {
    if (selected.length === 0) { toast.error('先勾选家长'); return }
    const r = await api.post<{ success: boolean; created: Array<{ name: string; username: string; password: string }>; skipped: Array<{ name: string; reason: string }> }>(
      '/api/account-center/parents/batch-accounts', { parent_ids: selected })
    if (!r.success) { toast.error('开通失败'); return }
    setIssued(r.created ?? [])
    setSkipped(r.skipped ?? [])
    setSelected([])
    await load()
  }

  const parentColumns: TableColumnsType<ParentRow> = [
    { title: '家长', dataIndex: 'name', key: 'name', width: 120 },
    { title: '手机号', dataIndex: 'phone', key: 'phone', width: 140,
      render: (v) => <span className="font-mono">{v || '—'}</span> },
    { title: '账号', dataIndex: 'account_status', key: 'account_status', width: 100,
      render: (v) => <AccountTag status={v} /> },
    { title: '绑定子女', key: 'children', width: 220,
      render: (_, r) => (
        <span className="text-sub">
          {r.children.length === 0 ? '—'
            : r.children.map((c) => c.name).join('、') + (r.children_count > r.children.length ? ` 等 ${r.children_count} 人` : '')}
        </span>
      ) },
    {
      title: '操作', key: 'op', width: 130,
      render: (_, r) => (
        <ActionButton
          variant="ghost" busyLabel="…" className="!px-2 !py-1 !text-[12px]"
          onAction={async () => {
            const res = await api.post<{ success: boolean; codes: Array<{ student_id: string; bind_code: string }> }>(
              `/api/account-center/parents/${r.parent_id}/reset-bind-code`)
            if (!res.success) { toast.error('重置失败'); throw new Error('failed') }
            toast.success(`已重置 ${res.codes.length} 个绑定码`)
            await load()
          }}
        >
          重置绑定码
        </ActionButton>
      ),
    },
  ]

  const bindingColumns: TableColumnsType<BindingRow> = [
    { title: '学生', key: 'student', width: 180,
      render: (_, r) => <span>{r.student_name}<span className="text-caption text-ink-muted ml-1">{r.class_name}</span></span> },
    { title: '家长', dataIndex: 'parent_name', key: 'parent_name', width: 110 },
    { title: '手机号', dataIndex: 'parent_phone', key: 'parent_phone', width: 130,
      render: (v) => <span className="font-mono">{v || '—'}</span> },
    { title: '关系', dataIndex: 'relation', key: 'relation', width: 80 },
    { title: '绑定码', dataIndex: 'bind_code', key: 'bind_code', width: 110,
      render: (v) => <span className="font-mono text-caption">{v}</span> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v) => <Tag tone={v === 'active' ? 'green' : 'gray'}>{v === 'active' ? '生效' : '已解绑'}</Tag> },
    { title: '异常', key: 'flags', width: 180,
      render: (_, r) => (
        <span className="flex flex-wrap gap-1">
          {r.flags.length === 0 ? '—' : r.flags.map((f) => <Tag key={f} tone="amber">{FLAG_LABEL[f] ?? f}</Tag>)}
        </span>
      ) },
    {
      title: '操作', key: 'op', width: 90,
      render: (_, r) => r.status !== 'active' ? null : (
        <ActionButton
          variant="ghost" busyLabel="…" className="!px-2 !py-1 !text-[12px]"
          onAction={async () => {
            const res = await api.del<{ success: boolean }>(`/api/account-center/parent-bindings/${r.binding_id}`)
            if (!res.success) { toast.error('解绑失败'); throw new Error('failed') }
            toast.success('已解绑（保留历史记录）')
            await load()
          }}
        >
          解绑
        </ActionButton>
      ),
    },
  ]

  const funnelRows = useMemo(() => summary ? [
    { key: 'all', label: '已绑定', value: summary.bound, known: true },
    { key: 'activated', label: '有账号', value: summary.activated, known: true },
    { key: 'active7d', label: '近 7 天活跃', value: summary.active_7d, known: summary.active_7d_available },
  ] : [], [summary])

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">家长与绑定</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          激活漏斗与绑定关系治理（点漏斗任一段可下钻到对应名单）
        </p>
      </header>

      {loading ? <SkeletonCard /> : error ? (
        <EmptyState title="加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : (
        <>
          <div className="grid grid-cols-3 gap-4 max-[1099px]:grid-cols-1">
            {funnelRows.map((f) => (
              <button
                key={f.key}
                onClick={() => {
                  if (f.key === 'active7d' && !f.known) return
                  setParams(f.key === 'activated' ? { seg: 'activated' }
                    : f.key === 'all' ? {} : { seg: 'unactivated' })
                  setTab('accounts')
                }}
                className="text-left surface-card p-5 rounded-[var(--radius-md)] border border-border hover:border-primary/40 transition-colors cursor-pointer"
              >
                <div className="text-sub text-ink-secondary">{f.label}</div>
                <div className="kpi-number text-[28px] font-semibold text-ink mt-1">
                  {f.known ? f.value : '未采集'}
                </div>
                {f.known && summary && summary.bound > 0 && (
                  <div className="text-caption text-ink-muted mt-1">
                    {(() => {
                      const pct = (Number(f.value ?? 0) / summary.bound) * 100
                      const shown = pct > 0 && pct < 1 ? (Math.round(pct * 10) / 10).toFixed(1) : String(Math.round(pct))
                      return `${shown}%（占绑定）`
                    })()}
                  </div>
                )}
                {!f.known && <div className="text-caption text-ink-muted mt-1">登录活跃暂无数据源</div>}
              </button>
            ))}
          </div>

          <Tabs
            items={[
              { key: 'accounts', label: `家长账号（${parents.length}）` },
              { key: 'bindings', label: `绑定关系（${bindings.length}）` },
            ]}
            active={tab}
            onChange={(k) => setTab(k as 'accounts' | 'bindings')}
          />

          {tab === 'accounts' && (
            <>
              <div className="flex items-center justify-between">
                <span className="text-sub text-ink-secondary">
                  已选 {selected.length} 人{seg === 'unactivated' ? '（当前只显示未激活）' : ''}
                </span>
                <div className="flex items-center gap-2">
                  {seg && <Button variant="ghost" onClick={() => { setParams({}); setSelected([]) }}>清除筛选</Button>}
                  <Button onClick={() => { void batchCreate() }} disabled={selected.length === 0}>
                    批量开通账号
                  </Button>
                </div>
              </div>
              {parents.length === 0 ? (
                <EmptyState title="没有匹配的家长" description="换一个筛选条件；绑定关系可在「绑定关系」页签查看。" />
              ) : (
                <Card flush>
                  <AntTable rowKey="parent_id" columns={parentColumns} dataSource={parents}
                    size="middle" pagination={{ pageSize: 15, showSizeChanger: false }}
                    rowSelection={{
                      selectedRowKeys: selected,
                      onChange: (keys) => setSelected(keys as string[]),
                      getCheckboxProps: (r) => ({ disabled: r.account_status !== 'none' }),
                    }} />
                </Card>
              )}
            </>
          )}

          {tab === 'bindings' && (
            <>
              <Card>
                <div className="flex flex-wrap items-end gap-3">
                  <Field label="异常筛选" className="w-48">
                    <Select value={issue} onChange={(e) => setIssue(e.target.value)}>
                      <option value="">全部</option>
                      <option value="dup">重复手机号（{anomalies.dup ?? 0}）</option>
                      <option value="unactivated">未激活（{anomalies.unactivated ?? 0}）</option>
                      <option value="over">一孩多家长（{anomalies.over ?? 0}）</option>
                    </Select>
                  </Field>
                  <span className="text-caption text-ink-muted pb-2">
                    解绑只改状态（保留历史），学生端立刻不再向该家长展示数据
                  </span>
                </div>
              </Card>
              {bindings.length === 0 ? (
                <EmptyState title="没有匹配的绑定" description="清除异常筛选，或先在班级中导入学生名册。" />
              ) : (
                <Card flush>
                  <AntTable rowKey="binding_id" columns={bindingColumns} dataSource={bindings}
                    size="middle" pagination={{ pageSize: 15, showSizeChanger: false }} />
                </Card>
              )}
            </>
          )}
        </>
      )}

      <Modal
        open={issued !== null}
        title="家长账号已开通"
        onClose={() => setIssued(null)}
        onConfirm={() => setIssued(null)}
        confirmText="我已记录，关闭"
        wide
      >
        <div className="space-y-3">
          <p className="text-sub text-ink-secondary m-0">初始密码只显示一次。</p>
          <table className="w-full text-sub">
            <thead>
              <tr className="text-left text-ink-muted border-b border-border">
                <th className="py-1.5">家长</th><th className="py-1.5">登录名</th><th className="py-1.5">初始密码</th>
              </tr>
            </thead>
            <tbody>
              {(issued ?? []).map((c, i) => (
                <tr key={i} className="border-b border-border last:border-0">
                  <td className="py-1.5">{c.name}</td>
                  <td className="py-1.5 font-mono">{c.username}</td>
                  <td className="py-1.5 font-mono font-semibold">{c.password}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {skipped.length > 0 && (
            <p className="text-caption text-warning m-0">
              跳过 {skipped.length} 人：{skipped.map((s) => `${s.name}（${s.reason}）`).join('、')}
            </p>
          )}
        </div>
      </Modal>
    </div>
  )
}
