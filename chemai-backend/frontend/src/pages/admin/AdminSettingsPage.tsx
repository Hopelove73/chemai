import { useCallback, useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { api } from '../../lib/api'
import {
  Button, Card, EmptyState, Modal, SkeletonCard, Tag, useToast,
} from '../../components/ui'
import { ActionButton, useAsyncAction } from '../../components/ui/ActionButton'

interface SettingRow {
  key: string
  type: 'bool' | 'enum'
  choices?: string[] | null
  /** 人话说明（后端下发）：没有它们，管理员只能看到裸 key */
  label?: string
  description?: string
  impact?: string
  choice_labels?: Record<string, string>
  effective: unknown
  default: unknown
  overridden: boolean
}

/**
 * 运行时开关（/admin/settings，gate settings:update）。
 * 白名单封闭（当前 2 个行为旗子）；修改热生效（≈5s 缓存时延），恢复默认删覆写行。
 */
export function AdminSettingsPage() {
  const toast = useToast()
  const [rows, setRows] = useState<SettingRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /* 修改确认卡（R-I2）：记录待写值。恢复默认走行内 ActionButton（可逆动作，无需双确认） */
  const [pending, setPending] = useState<{ key: string; value: unknown; label: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; settings: SettingRow[] }>('/api/settings')
      setRows(r.settings ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const update = useAsyncAction(async () => {
    if (!pending) return
    const r = await api.put<{ success: boolean; message?: string }>(`/api/settings/${pending.key}`, {
      value: pending.value,
    })
    if (!r.success) { toast.error(r.message || '修改失败'); return }
    toast.success(`已生效（缓存时延 ≤5s）：${pending.key}`)
    setPending(null)
    await load()
  })

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">运行时开关</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          白名单行为旗子热切换 —— 修改无需重启，每次修改都写入操作日志
        </p>
      </header>

      {loading ? <SkeletonCard /> : error ? (
        <EmptyState title="设置加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : rows.length === 0 ? (
        <EmptyState title="白名单为空" description="没有登记任何可切换旗子（服务端 WHITELIST）。" />
      ) : (
        <div className="space-y-3">
          {rows.map((row) => (
            <Card key={row.key}>
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-[15px] font-semibold text-ink">{row.label || row.key}</span>
                    {row.overridden
                      ? <Tag tone="amber">已覆写</Tag>
                      : <Tag tone="gray">默认（env）</Tag>}
                  </div>
                  <div className="text-caption text-ink-secondary mt-1">
                    当前生效：
                    <span className="kpi-number font-mono font-semibold text-ink ml-1">
                      {row.type === 'bool'
                        ? (row.effective ? '开启' : '关闭')
                        : (row.choice_labels?.[String(row.effective)] ?? String(row.effective))}
                    </span>
                    {row.overridden && (
                      <span className="ml-2">默认值：<span className="font-mono">{String(row.default)}</span></span>
                    )}
                  </div>
                  {row.description && (
                    <p className="text-sub text-ink-secondary mt-1.5 mb-0 leading-relaxed">{row.description}</p>
                  )}
                  {row.impact && (
                    <p className="text-caption text-ink-muted mt-1 mb-0">切换影响：{row.impact}</p>
                  )}
                  <div className="text-caption text-ink-muted mt-1 font-mono">配置项：{row.key}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {row.type === 'bool' ? (
                    <Button
                      variant={row.effective ? 'secondary' : 'primary'}
                      onClick={() => setPending({
                        key: row.key,
                        value: !row.effective,
                        label: row.choice_labels?.[String(!row.effective)] ?? String(!row.effective),
                      })}
                    >
                      {row.effective ? '关闭' : '开启'}
                    </Button>
                  ) : (
                    (row.choices ?? []).filter((c) => c !== row.effective).map((c) => (
                      <Button key={c} variant="secondary"
                        onClick={() => setPending({
                          key: row.key,
                          value: c,
                          label: row.choice_labels?.[c] ?? c,
                        })}>
                        改用 {row.choice_labels?.[c] ?? c}
                      </Button>
                    ))
                  )}
                  {row.overridden && (
                    <ActionButton
                      onAction={async () => {
                        const r = await api.del<{ success: boolean; message?: string }>(`/api/settings/${row.key}`)
                        if (!r.success) { toast.error(r.message || '恢复默认失败'); return }
                        toast.success('已恢复默认（env 种子值重新生效）')
                        await load()
                      }}
                      variant="ghost" busyLabel="恢复中…"
                      className="!px-2 !py-1 !text-[12px]"
                    >
                      <RotateCcw size={12} className="inline mr-1" />恢复默认
                    </ActionButton>
                  )}
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      {/* 修改确认（写审计前最后一次确认） */}
      <Modal
        open={pending !== null}
        title={`确认修改 ${pending?.key ?? ''}`}
        onClose={() => setPending(null)}
        onConfirm={() => { void update.run() }}
        confirmText={update.state === 'busy' ? '提交中…' : '确认修改'}
      >
        <p className="text-sub m-0">
          将「<span className="font-mono font-semibold">{pending?.key}</span>」设为{' '}
          <span className="font-mono font-semibold">{pending?.label}</span>。
          修改约 5 秒内对全部新请求生效（无需重启），并写入操作日志。
        </p>
      </Modal>

    </div>
  )
}
