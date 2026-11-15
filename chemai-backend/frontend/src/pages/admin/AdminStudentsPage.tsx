import { useCallback, useEffect, useMemo, useState } from 'react'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { KeyRound, UserCheck, UserX } from 'lucide-react'
import { api, can } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Input, Modal, Select, SkeletonCard, StatCard,
  Tag, useToast,
} from '../../components/ui'
import { ActionButton, useAsyncAction } from '../../components/ui/ActionButton'

interface StudentAccountRow {
  student_id: string
  name: string
  class_id: string
  class_name: string
  approval_status: string
  account_status: 'active' | 'disabled' | 'none'
  username?: string | null
  last_exercise_at?: string | null
  exercises_completed: number
  parent_count: number
}

interface Summary {
  total: number
  with_account: number
  without_account: number
  with_parent: number
}

function AccountTag({ status }: { status: StudentAccountRow['account_status'] }) {
  if (status === 'active') return <Tag tone="green">正常</Tag>
  if (status === 'disabled') return <Tag tone="gray">已停用</Tag>
  return <Tag tone="amber">未开通</Tag>
}

function formatDate(iso?: string | null) {
  if (!iso) return '—'
  return iso.replace('T', ' ').slice(0, 10)
}

/**
 * 学生账号（/admin/students）—— **账号生命周期视角**，与教师端「学生管理」（学情视角）分工。
 * 决策：批量开通的一次性密码只在结果弹窗展示一次，落库为哈希。
 */
export function AdminStudentsPage() {
  const toast = useToast()
  const [rows, setRows] = useState<StudentAccountRow[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [classes, setClasses] = useState<Array<{ class_id: string; name: string }>>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [classId, setClassId] = useState('')
  const [account, setAccount] = useState('')
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<string[]>([])
  const [issued, setIssued] = useState<Array<{ student_id: string; name: string; username: string; password: string }> | null>(null)
  const [skipped, setSkipped] = useState<Array<{ name: string; reason: string }>>([])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const qs = new URLSearchParams()
      if (classId) qs.set('class_id', classId)
      if (account) qs.set('account', account)
      if (q.trim()) qs.set('q', q.trim())
      const [list, sum, cls] = await Promise.all([
        api.get<{ success: boolean; students: StudentAccountRow[] }>(`/api/account-center/students?${qs}`),
        api.get<Summary & { success: boolean }>('/api/account-center/students/summary'),
        api.get<{ success: boolean; classes: Array<{ class_id: string; name: string }> }>('/api/classes'),
      ])
      setRows(list.students ?? [])
      setSummary(sum)
      setClasses(cls.classes ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [classId, account, q])

  useEffect(() => { void load() }, [load])

  const batchCreate = useAsyncAction(async () => {
    if (selected.length === 0) { toast.error('先勾选学生'); return }
    const r = await api.post<{
      success: boolean
      created: Array<{ student_id: string; name: string; username: string; password: string }>
      skipped: Array<{ name: string; reason: string }>
    }>('/api/account-center/students/batch-accounts', { student_ids: selected })
    if (!r.success) { toast.error('开通失败'); return }
    setIssued(r.created ?? [])
    setSkipped(r.skipped ?? [])
    setSelected([])
    await load()
  })

  const columns: TableColumnsType<StudentAccountRow> = [
    { title: '学号', dataIndex: 'student_id', key: 'student_id', width: 130 },
    { title: '姓名', dataIndex: 'name', key: 'name', width: 110 },
    { title: '班级', dataIndex: 'class_name', key: 'class_name', width: 110 },
    { title: '账号', dataIndex: 'account_status', key: 'account_status', width: 100,
      render: (v) => <AccountTag status={v} /> },
    { title: '家长绑定', dataIndex: 'parent_count', key: 'parent_count', width: 90,
      render: (v: number) => (v > 0 ? `${v} 位` : '—') },
    { title: '最近练习', dataIndex: 'last_exercise_at', key: 'last_exercise_at', width: 120,
      render: (v) => <span className="font-mono text-[12px]">{formatDate(v)}</span> },
    {
      title: '操作', key: 'actions', width: 140,
      render: (_, row) => row.account_status === 'none' ? (
        <span className="text-caption text-ink-muted">待开通</span>
      ) : (
        <ActionButton
          variant="ghost"
          busyLabel="处理中…"
          className="!px-2 !py-1 !text-[12px]"
          onAction={async () => {
            const next = row.account_status === 'active' ? 'disabled' : 'active'
            const r = await api.post<{ success: boolean; message?: string }>(
              `/api/account-center/students/${row.student_id}/account-status`, { status: next })
            if (!r.success) { toast.error(r.message || '操作失败'); throw new Error('failed') }
            toast.success(next === 'active' ? '已启用' : '已停用')
            await load()
          }}
        >
          {row.account_status === 'active' ? '停用' : '启用'}
        </ActionButton>
      ),
    },
  ]

  const filters = useMemo(() => (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="班级" className="w-40">
        <Select value={classId} onChange={(e) => setClassId(e.target.value)}>
          <option value="">全部班级</option>
          {classes.map((c) => <option key={c.class_id} value={c.class_id}>{c.name}</option>)}
        </Select>
      </Field>
      <Field label="账号状态" className="w-36">
        <Select value={account} onChange={(e) => setAccount(e.target.value)}>
          <option value="">全部</option>
          <option value="has">已有账号</option>
          <option value="none">未开通</option>
        </Select>
      </Field>
      <Field label="搜索" className="w-48">
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="姓名或学号" />
      </Field>
      <Button onClick={() => { void load() }}>查询</Button>
    </div>
  ), [classId, account, q, classes, load])

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">学生账号</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          账号生命周期视角（学情请看教师端「学生管理」）
        </p>
      </header>

      {loading ? <SkeletonCard /> : error ? (
        <EmptyState title="加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
      ) : (
        <>
          <div className="grid grid-cols-4 gap-4 max-[1099px]:grid-cols-2">
            <StatCard label="学生" value={summary?.total ?? 0} suffix="人" />
            <StatCard label="已开通账号" value={summary?.with_account ?? 0} suffix="人" tone="ok" />
            <StatCard label="未开通" value={summary?.without_account ?? 0} suffix="人"
              tone={(summary?.without_account ?? 0) > 0 ? 'warn' : 'normal'} />
            <StatCard label="已绑定家长" value={summary?.with_parent ?? 0} suffix="人" />
          </div>

          <Card>{filters}</Card>

          <div className="flex items-center justify-between">
            <span className="text-sub text-ink-secondary">已选 {selected.length} 人</span>
            <Button
              onClick={() => { void batchCreate.run() }}
              disabled={selected.length === 0 || !can('student:update')}
            >
              <UserCheck size={14} className="inline mr-1" /> 批量开通账号
            </Button>
          </div>

          {rows.length === 0 ? (
            <EmptyState
              title="没有匹配的学生"
              description="放宽筛选条件，或先在「班级与年级」建班并导入学生。"
            />
          ) : (
            <Card flush>
              <AntTable
                rowKey="student_id"
                columns={columns}
                dataSource={rows}
                size="middle"
                pagination={{ pageSize: 20, showSizeChanger: false }}
                rowSelection={{
                  selectedRowKeys: selected,
                  onChange: (keys) => setSelected(keys as string[]),
                  getCheckboxProps: (row) => ({ disabled: row.account_status !== 'none' }),
                }}
              />
            </Card>
          )}
        </>
      )}

      {/* 一次性凭据：只展示这一次 */}
      <Modal
        open={issued !== null}
        title="账号已开通"
        onClose={() => setIssued(null)}
        onConfirm={() => setIssued(null)}
        confirmText="我已记录，关闭"
        wide
      >
        <div className="space-y-3">
          <p className="text-sub text-ink-secondary m-0">
            初始密码只在这里显示一次。请导出或抄写后再关闭。
          </p>
          <table className="w-full text-sub">
            <thead>
              <tr className="text-left text-ink-muted border-b border-border">
                <th className="py-1.5">姓名</th><th className="py-1.5">学号（登录名）</th><th className="py-1.5">初始密码</th>
              </tr>
            </thead>
            <tbody>
              {(issued ?? []).map((c) => (
                <tr key={c.student_id} className="border-b border-border last:border-0">
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
          {(issued ?? []).length === 0 && (
            <p className="text-sub text-ink-secondary m-0">选中的学生都已有账号，未做任何修改。</p>
          )}
        </div>
      </Modal>

      {/* 提示：停用/启用与重置密码的区别 */}
      <div className="flex items-center gap-2 text-caption text-ink-muted">
        <KeyRound size={12} />
        重置密码请到教师端「学生管理」详情；这里只管账号的开启与停用。
        <UserX size={12} className="ml-2" />
        停用后该生无法登录，数据保留。
      </div>
    </div>
  )
}
