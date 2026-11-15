import { useCallback, useEffect, useMemo, useState } from 'react'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { Search } from 'lucide-react'
import { api } from '../../lib/api'
import { Button, Card, EmptyState, Field, Input, Modal, Select, SkeletonCard, Tag } from '../../components/ui'
import { ACTION_META, actionLabel, detailEntries, targetLabel } from '../../lib/log-labels'

interface LogRow {
  id: number
  user_id: string
  action: string
  target_type?: string | null
  target_id?: string | null
  detail?: Record<string, unknown> | null
  ip_address?: string | null
  created_at?: string | null
}

/**
 * 操作日志（/admin/logs，gate log:read）—— 只读消费侧。
 *
 * 管理员视角：动作显示中文、目标显示"类型 · 名字"、详情收进抽屉（结构化键值），
 * 不再把 `student_reset_password` 和整串 JSON 摊在表格里（review 抓到过）。
 */
export function AdminLogsPage() {
  const [rows, setRows] = useState<LogRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [action, setAction] = useState('')
  const [userId, setUserId] = useState('')
  const [days, setDays] = useState('30')
  const [detailRow, setDetailRow] = useState<LogRow | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const qs = new URLSearchParams()
      if (action) qs.set('action', action)
      if (userId.trim()) qs.set('user_id', userId.trim())
      if (days) qs.set('days', days)
      const r = await api.get<{ success: boolean; logs: LogRow[] }>(`/api/logs?${qs.toString()}`)
      setRows(r.logs ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [action, userId, days])

  useEffect(() => { void load() }, [load])

  const actionOptions = useMemo(
    () => [{ value: '', label: '全部动作' },
      ...Object.entries(ACTION_META).map(([value, meta]) => ({ value, label: meta.label }))],
    [],
  )

  /** 目标显示：优先"类型 · 名字"，没有名字时退回编号。 */
  function targetText(row: LogRow): string {
    const type = targetLabel(row.target_type)
    const name = row.detail && typeof row.detail.name === 'string' ? row.detail.name : null
    const tail = name ?? row.target_id
    if (!row.target_type && !tail) return '—'
    return tail ? `${type} · ${tail}` : type
  }

  const columns: TableColumnsType<LogRow> = [
    {
      title: '时间', dataIndex: 'created_at', key: 'created_at', width: 160,
      render: (v) => <span className="font-mono text-[12px]">{v ? v.replace('T', ' ').slice(0, 19) : '—'}</span>,
    },
    { title: '操作者', dataIndex: 'user_id', key: 'user_id', width: 130 },
    {
      title: '动作', dataIndex: 'action', key: 'action', width: 210,
      // 只给中文；原始动作标识放在详情抽屉里（给排查用，不占表格）
      render: (v: string) => <span className="text-sub">{actionLabel(v)}</span>,
    },
    {
      title: '目标', key: 'target', width: 220,
      render: (_, row) => <span className="text-sub">{targetText(row)}</span>,
    },
    {
      title: '操作', key: 'op', width: 90,
      render: (_, row) => <Button variant="ghost" onClick={() => setDetailRow(row)}>查看</Button>,
    },
  ]

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">操作日志</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">敏感管理动作的审计留痕（只读）</p>
      </header>

      <Card>
        <div className="flex flex-wrap items-end gap-3">
          <Field label="动作" className="w-56">
            <Select value={action} onChange={(e) => setAction(e.target.value)}>
              {actionOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </Field>
          <Field label="操作者" className="w-36">
            <Input value={userId} onChange={(e) => setUserId(e.target.value)} placeholder="user_id" />
          </Field>
          <Field label="时间范围" className="w-32">
            <Select value={days} onChange={(e) => setDays(e.target.value)}>
              <option value="1">今天</option>
              <option value="7">近 7 天</option>
              <option value="30">近 30 天</option>
              <option value="365">近一年</option>
            </Select>
          </Field>
          <Button onClick={() => { void load() }}><Search size={14} className="inline mr-1" /> 查询</Button>
        </div>
      </Card>

      {loading ? <SkeletonCard /> : error ? (
        <EmptyState title="日志加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : rows.length === 0 ? (
        <EmptyState
          title="没有匹配的日志"
          description="放宽筛选条件，或确认相关管理动作（审批/重置密码/调班等）是否已发生。"
          action={<Button onClick={() => { setAction(''); setUserId(''); setDays('365') }}>清空筛选</Button>}
        />
      ) : (
        <Card flush>
          <AntTable
            rowKey="id"
            columns={columns}
            dataSource={rows}
            pagination={{ pageSize: 20, showSizeChanger: false }}
            size="middle"
            onRow={(row) => ({ onClick: () => setDetailRow(row), className: 'cursor-pointer' })}
          />
        </Card>
      )}

      {/* 详情：结构化键值 + 原始 JSON（默认折叠） */}
      <Modal
        open={detailRow !== null}
        title={detailRow ? actionLabel(detailRow.action) : ''}
        onClose={() => setDetailRow(null)}
        hideFooter
        wide
      >
        {detailRow && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sub">
              <div><span className="text-ink-muted">时间</span><div className="font-mono">{detailRow.created_at?.replace('T', ' ').slice(0, 19) ?? '—'}</div></div>
              <div><span className="text-ink-muted">操作者</span><div className="font-mono">{detailRow.user_id}</div></div>
              <div><span className="text-ink-muted">目标</span><div>{targetText(detailRow)}</div></div>
              <div><span className="text-ink-muted">动作标识</span><div className="font-mono text-caption">{detailRow.action}</div></div>
            </div>

            {detailEntries(detailRow.detail).length > 0 ? (
              <div>
                <div className="text-caption text-ink-muted mb-1">变更内容</div>
                <table className="w-full text-sub">
                  <tbody>
                    {detailEntries(detailRow.detail).map(([k, v]) => (
                      <tr key={k} className="border-b border-border last:border-0">
                        <td className="py-1.5 pr-4 text-ink-secondary whitespace-nowrap align-top">{k}</td>
                        <td className="py-1.5 break-all">{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sub text-ink-secondary m-0">该动作没有附加详情。</p>
            )}

            {detailRow.detail && (
              <details className="text-caption text-ink-muted">
                <summary className="cursor-pointer">原始记录（JSON）</summary>
                <pre className="mt-1 whitespace-pre-wrap break-all font-mono">{JSON.stringify(detailRow.detail, null, 2)}</pre>
              </details>
            )}

            {detailRow.action.startsWith('runtime_setting') && (
              <div className="flex items-center gap-2">
                <Tag tone="blue">提示</Tag>
                <span className="text-caption text-ink-secondary">开关值含义见「运行时开关」页的说明。</span>
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  )
}
