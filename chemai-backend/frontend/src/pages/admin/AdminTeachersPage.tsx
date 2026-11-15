import { useCallback, useEffect, useState } from 'react'
import { Table as AntTable, type TableColumnsType } from 'antd'
import { Plus, Trash2 } from 'lucide-react'
import { api, can } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Input, Modal, Select, SkeletonCard,
  StatCard, Tabs, Tag, Textarea, useToast,
} from '../../components/ui'
import { ActionButton, useAsyncAction } from '../../components/ui/ActionButton'

/* ── 类型 ─────────────────────────────────────────────── */

interface TeacherApplication {
  id: string
  name: string
  phone: string
  school?: string
  subject?: string
  status: string
  created_at?: string
}

interface TeacherRow {
  teacher_id: string
  name: string
  phone?: string
  status: string
}

interface TeacherClassRow {
  class_id: string
  class_name: string
  subject: string
  is_class_teacher: boolean
}

interface ClassRow {
  class_id: string
  name: string
  grade: string
  grade_id?: string
  subject?: string
  student_count?: number
  teacher_name?: string | null
}


/* 状态徽章（P-02：浅底深字标签式） */
function StatusTag({ status }: { status: string }) {
  const tone = status === 'approved' || status === 'active' ? 'green' : status === 'pending' ? 'amber' : 'gray'
  const label = { approved: '已通过', pending: '待审批', rejected: '已驳回', active: '启用', disabled: '停用' }[status] ?? status
  return <Tag tone={tone}>{label}</Tag>
}

/** 业务失败约定：toast + 抛错 —— 动作状态机回 idle 而不是打勾（R-I6） */
function fail(toast: { error: (m: string) => void }, message: string): never {
  toast.error(message)
  throw new Error(message)
}

/* ── Tab 1：教师入驻审批（gate teacher:create）────────────── */

function ApplicationsTab() {
  const toast = useToast()
  const [rows, setRows] = useState<TeacherApplication[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /* R-I5 两段式：批准（不可逆方向）先在原位展开微确认，NEVER 直接直发 */
  const [confirmingId, setConfirmingId] = useState<string | null>(null)
  const [rejecting, setRejecting] = useState<TeacherApplication | null>(null)
  const [rejectReason, setRejectReason] = useState('')
  /* 审批通过后的一次性凭据展示（默认密码只此机会告知） */
  const [issued, setIssued] = useState<{ username: string; default_password: string } | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; applications: TeacherApplication[] }>('/api/teacher-applications')
      setRows(r.applications ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const reject = useAsyncAction(async () => {
    if (!rejecting) return
    if (!rejectReason.trim()) fail(toast, '驳回必须附意见')
    const r = await api.post<{ success: boolean; message?: string }>(
      `/api/teacher-applications/${rejecting.id}/reject?reason=${encodeURIComponent(rejectReason.trim())}`)
    if (!r.success) fail(toast, r.message || '驳回失败')
    toast.success('已驳回申请')
    setRejecting(null)
    setRejectReason('')
    await load()
  })

  const pending = rows.filter((r) => r.status === 'pending')
  const columns: TableColumnsType<TeacherApplication> = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '手机号', dataIndex: 'phone', key: 'phone' },
    { title: '意向学校', dataIndex: 'school', key: 'school', render: (v) => v || '—' },
    { title: '科目', dataIndex: 'subject', key: 'subject', render: (v) => v || '—' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <StatusTag status={v} /> },
    {
      title: '操作', key: 'actions', width: 230,
      render: (_, row) => row.status === 'pending' ? (
        confirmingId === row.id ? (
          <span className="inline-flex items-center gap-1.5">
            <ActionButton
              onAction={async () => {
                const r = await api.post<{ success: boolean; message?: string; username?: string; default_password?: string }>(
                  `/api/teacher-applications/${row.id}/approve`)
                if (!r.success) fail(toast, r.message || '审批失败')
                setConfirmingId(null)
                setIssued({ username: r.username ?? '', default_password: r.default_password ?? '' })
                await load()
              }}
              busyLabel="审批中…"
              className="!px-2 !py-1 !text-[12px]"
            >确认通过</ActionButton>
            <Button variant="ghost" onClick={() => setConfirmingId(null)}>取消</Button>
          </span>
        ) : (
          <span className="inline-flex items-center gap-1.5">
            <Button onClick={() => setConfirmingId(row.id)}>通过</Button>
            <Button variant="ghost" onClick={() => { setRejecting(row); setRejectReason('') }}>驳回</Button>
          </span>
        )
      ) : null,
    },
  ]

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState title="申请列表加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }
  if (rows.length === 0) {
    return <EmptyState title="暂无入驻申请" description="教师注册提交后会出现在这里等待审批。" />
  }

  return (
    <div className="space-y-3">
      <StatCard label="待审批" value={pending.length} suffix="条" tone={pending.length > 0 ? 'warn' : 'ok'} />
      <Card flush>
        <AntTable
          rowKey="id"
          columns={columns}
          dataSource={rows}
          pagination={{ pageSize: 10, showSizeChanger: false }}
          size="middle"
        />
      </Card>

      {/* 驳回：必附意见（R-I5 打回），确认卡形式（R-I2） */}
      <Modal
        open={rejecting !== null}
        title={`驳回 ${rejecting?.name ?? ''} 的入驻申请`}
        onClose={() => setRejecting(null)}
        onConfirm={() => { void reject.run() }}
        confirmText={reject.state === 'busy' ? '提交中…' : '确认驳回'}
        danger
      >
        <Field label="驳回意见（必填）">
          <Textarea
            value={rejectReason}
            onChange={(e) => setRejectReason(e.target.value)}
            placeholder="说明驳回原因，将随申请记录留痕"
            rows={3}
          />
        </Field>
      </Modal>

      {/* 通过后的一次性凭据 */}
      <Modal
        open={issued !== null}
        title="教师账号已创建"
        onClose={() => setIssued(null)}
        confirmText="知道了"
        onConfirm={() => setIssued(null)}
      >
        <div className="space-y-2 text-sub">
          <div>登录用户名（手机号）：<span className="font-mono font-semibold">{issued?.username}</span></div>
          <div>初始密码（手机号后 6 位）：<span className="font-mono font-semibold">{issued?.default_password}</span></div>
          <div className="text-caption text-ink-secondary">请把凭据转告教师，首次登录后修改密码。</div>
        </div>
      </Modal>
    </div>
  )
}

/* ── Tab 2：教师账号（gate teacher:create）────────────────── */

function TeachersTab() {
  const toast = useToast()
  const [rows, setRows] = useState<TeacherRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<TeacherRow | null>(null)
  const [assigning, setAssigning] = useState<TeacherRow | null>(null)
  /* 任教分配：本地编辑全量列表，保存走全量替换接口（后端先删后加） */
  const [assignments, setAssignments] = useState<TeacherClassRow[]>([])
  const [classList, setClassList] = useState<ClassRow[]>([])
  const [newClassId, setNewClassId] = useState('')
  const [newSubject, setNewSubject] = useState('化学')
  const [newIsHead, setNewIsHead] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; teachers: TeacherRow[] }>('/api/users/teachers')
      setRows(r.teachers ?? [])
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const saveEdit = useAsyncAction(async () => {
    if (!editing) return
    const r = await api.put<{ success: boolean; message?: string }>(
      `/api/users/teacher/${editing.teacher_id}`,
      { name: editing.name, phone: editing.phone, status: editing.status })
    if (!r.success) fail(toast, r.message || '保存失败')
    toast.success('教师信息已更新')
    setEditing(null)
    await load()
  })

  const saveAssign = useAsyncAction(async () => {
    if (!assigning) return
    const r = await api.post<{ success: boolean; message?: string }>(
      `/api/users/teacher/${assigning.teacher_id}/assign-classes`,
      assignments.map((a) => ({ class_id: a.class_id, subject: a.subject, is_class_teacher: a.is_class_teacher })))
    if (!r.success) fail(toast, r.message || '保存失败')
    toast.success(`已保存 ${assignments.length} 条任教安排`)
    setAssigning(null)
  })

  const openAssign = async (t: TeacherRow) => {
    setAssigning(t)
    const [mine, classes] = await Promise.all([
      api.get<{ success: boolean; classes: TeacherClassRow[] }>(`/api/users/teacher/${t.teacher_id}/classes`),
      api.get<{ success: boolean; classes: ClassRow[] }>('/api/classes'),
    ])
    setAssignments(mine.classes ?? [])
    setClassList(classes.classes ?? [])
    setNewClassId('')
    setNewIsHead(false)
  }

  const columns: TableColumnsType<TeacherRow> = [
    { title: '姓名', dataIndex: 'name', key: 'name' },
    { title: '手机号', dataIndex: 'phone', key: 'phone', render: (v) => v || '—' },
    { title: '状态', dataIndex: 'status', key: 'status', render: (v) => <StatusTag status={v} /> },
    {
      title: '操作', key: 'actions', width: 180,
      render: (_, row) => (
        <span className="inline-flex items-center gap-1.5">
          <Button variant="ghost" onClick={() => setEditing({ ...row })}>编辑</Button>
          <Button variant="ghost" onClick={() => { void openAssign(row) }}>任教分配</Button>
        </span>
      ),
    },
  ]

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState title="教师列表加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }
  if (rows.length === 0) {
    return <EmptyState title="暂无教师账号" description="教师入驻审批通过后会自动创建账号。" />
  }

  return (
    <>
      <Card flush>
        <AntTable rowKey="teacher_id" columns={columns} dataSource={rows}
          pagination={{ pageSize: 10, showSizeChanger: false }} size="middle" />
      </Card>

      {/* 编辑教师 */}
      <Modal
        open={editing !== null} title={`编辑教师：${editing?.name ?? ''}`}
        onClose={() => setEditing(null)} onConfirm={() => { void saveEdit.run() }}
        confirmText={saveEdit.state === 'busy' ? '保存中…' : '保存'}
      >
        {editing && (
          <div className="space-y-3">
            <Field label="姓名">
              <Input value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
            </Field>
            <Field label="手机号">
              <Input value={editing.phone ?? ''} onChange={(e) => setEditing({ ...editing, phone: e.target.value })} />
            </Field>
            <Field label="状态">
              <Select value={editing.status} onChange={(e) => setEditing({ ...editing, status: e.target.value })}>
                <option value="approved">已通过</option>
                <option value="disabled">停用</option>
              </Select>
            </Field>
          </div>
        )}
      </Modal>

      {/* 任教分配（全量替换） */}
      <Modal
        open={assigning !== null} title={`任教分配：${assigning?.name ?? ''}`} wide
        onClose={() => setAssigning(null)} onConfirm={() => { void saveAssign.run() }}
        confirmText={saveAssign.state === 'busy' ? '保存中…' : '保存任教安排'}
      >
        <div className="space-y-3">
          {assignments.length === 0 && (
            <p className="text-sub text-ink-secondary m-0">尚未分配任教班级，从下方添加。</p>
          )}
          <ul className="space-y-1.5 m-0 p-0 list-none">
            {assignments.map((a, i) => (
              <li key={`${a.class_id}-${a.subject}`} className="flex items-center gap-2 text-sub">
                <span className="font-medium">{a.class_name}</span>
                <span className="text-ink-muted">{a.subject}</span>
                {a.is_class_teacher && <Tag tone="green">班主任</Tag>}
                <button
                  className="ml-auto text-ink-muted hover:text-error cursor-pointer"
                  onClick={() => setAssignments(assignments.filter((_, j) => j !== i))}
                  title="移除"
                >
                  <Trash2 size={14} />
                </button>
              </li>
            ))}
          </ul>
          <div className="flex items-end gap-2 pt-2 border-t border-border">
            <Field label="班级" className="flex-1">
              <Select value={newClassId} onChange={(e) => setNewClassId(e.target.value)}>
                <option value="">选择班级…</option>
                {classList.map((c) => <option key={c.class_id} value={c.class_id}>{c.name}（{c.grade}）</option>)}
              </Select>
            </Field>
            <Field label="科目" className="w-28">
              <Input value={newSubject} onChange={(e) => setNewSubject(e.target.value)} />
            </Field>
            <label className="flex items-center gap-1.5 text-sub text-ink-secondary pb-1.5 cursor-pointer">
              <input type="checkbox" checked={newIsHead} onChange={(e) => setNewIsHead(e.target.checked)} />
              班主任
            </label>
            <Button
              disabled={!newClassId}
              onClick={() => {
                const cls = classList.find((c) => c.class_id === newClassId)
                if (!cls) return
                if (assignments.some((a) => a.class_id === newClassId && a.subject === newSubject)) return
                setAssignments([...assignments, {
                  class_id: newClassId, class_name: cls.name, subject: newSubject, is_class_teacher: newIsHead,
                }])
                setNewClassId('')
                setNewIsHead(false)
              }}
            >
              <Plus size={14} className="inline mr-1" /> 添加
            </Button>
          </div>
          <p className="text-caption text-ink-secondary m-0">保存会整体替换该教师的任教安排（先清空再写入）。</p>
        </div>
      </Modal>
    </>
  )
}


/* ── 页面骨架 ──────────────────────────────────────────── */

export function AdminTeachersPage() {
  const tabs = [
    ...(can('teacher:create') ? [{ key: 'applications', label: '教师审批' }] : []),
    ...(can('teacher:create') ? [{ key: 'teachers', label: '教师账号' }] : []),
  ]
  const [active, setActive] = useState(tabs[0]?.key ?? '')

  if (tabs.length === 0) {
    return <EmptyState title="无管理权限" description="当前账号没有教师管理能力（需 teacher:create）。" />
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">教师账号</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">入驻审批与账号管理</p>
      </header>
      <Tabs items={tabs} active={active} onChange={setActive} />
      {active === 'applications' && <ApplicationsTab />}
      {active === 'teachers' && <TeachersTab />}
    </div>
  )
}
