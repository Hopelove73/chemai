import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Plus, Trash2 } from 'lucide-react'
import { api, can } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Input, Modal, SkeletonCard, Tag, useToast,
} from '../../components/ui'
import { ActionButton } from '../../components/ui/ActionButton'

interface GradeRow {
  grade_id: string
  name: string
  year?: number | null
  status?: number | null
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

/** 班级与年级 —— 年级 → 班级 组织树（两级展开，不引 Tree 组件）。 */
export function AdminClassesPage() {
  const toast = useToast()
  const [grades, setGrades] = useState<GradeRow[]>([])
  const [classes, setClasses] = useState<ClassRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState<string[]>([])

  const [gradeModal, setGradeModal] = useState<{ mode: 'create' | 'edit'; grade?: GradeRow } | null>(null)
  const [gName, setGName] = useState('')
  const [gYear, setGYear] = useState('')

  const [classModal, setClassModal] = useState<{ mode: 'create' | 'edit'; gradeId: string; cls?: ClassRow } | null>(null)
  const [cName, setCName] = useState('')
  const [cSubject, setCSubject] = useState('化学')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [g, c] = await Promise.all([
        api.get<{ success: boolean; grades: GradeRow[] }>('/api/grades'),
        api.get<{ success: boolean; classes: ClassRow[] }>('/api/classes'),
      ])
      setGrades(g.grades ?? [])
      setClasses(c.classes ?? [])
      setExpanded((prev) => (prev.length ? prev : (g.grades ?? []).map((x) => x.grade_id)))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const byGrade = useMemo(() => {
    const map: Record<string, ClassRow[]> = {}
    for (const c of classes) {
      const key = c.grade_id ?? ''
      if (!map[key]) map[key] = []
      map[key].push(c)
    }
    return map
  }, [classes])

  const totalStudents = classes.reduce((s, c) => s + (c.student_count ?? 0), 0)

  function openGradeCreate() {
    setGName('')
    setGYear(String(new Date().getFullYear()))
    setGradeModal({ mode: 'create' })
  }
  function openGradeEdit(g: GradeRow) {
    setGName(g.name)
    setGYear(String(g.year ?? ''))
    setGradeModal({ mode: 'edit', grade: g })
  }
  function openClassCreate(gradeId: string) {
    setCName('')
    setCSubject('化学')
    setClassModal({ mode: 'create', gradeId })
  }
  function openClassEdit(gradeId: string, cls: ClassRow) {
    setCName(cls.name)
    setCSubject(cls.subject ?? '化学')
    setClassModal({ mode: 'edit', gradeId, cls })
  }

  async function submitGrade() {
    if (!gName.trim()) { toast.error('年级名称必填'); return }
    const body = { name: gName.trim(), year: gYear ? Number(gYear) : undefined }
    const r = gradeModal?.mode === 'edit' && gradeModal.grade
      ? await api.put<{ success: boolean; message?: string }>(`/api/grades/${gradeModal.grade.grade_id}`, body)
      : await api.post<{ success: boolean; message?: string }>('/api/grades', body)
    if (!r.success) { toast.error(r.message || '保存失败'); return }
    toast.success(gradeModal?.mode === 'edit' ? '年级已更新' : '年级已创建')
    setGradeModal(null)
    await load()
  }

  async function submitClass() {
    if (!classModal) return
    if (!cName.trim()) { toast.error('班级名称必填'); return }
    const r = classModal.mode === 'edit' && classModal.cls
      ? await api.put<{ success: boolean; message?: string }>(`/api/classes/${classModal.cls.class_id}`,
        { name: cName.trim(), subject: cSubject })
      : await api.post<{ success: boolean; message?: string }>('/api/classes',
        { name: cName.trim(), grade_id: classModal.gradeId, subject: cSubject })
    if (!r.success) { toast.error(r.message || '保存失败'); return }
    toast.success(classModal.mode === 'edit' ? '班级已更新' : '班级已创建')
    setClassModal(null)
    await load()
  }

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState title="组织数据加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }

  return (
    <div className="space-y-4">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold text-ink m-0">班级与年级</h1>
          <p className="text-sub text-ink-secondary mt-1 mb-0">
            {grades.length} 个年级 · {classes.length} 个班级 · {totalStudents} 名学生
          </p>
        </div>
        {can('grade:create') && (
          <Button onClick={openGradeCreate}><Plus size={14} className="inline mr-1" />新建年级</Button>
        )}
      </header>

      {grades.length === 0 ? (
        <EmptyState
          title="还没有年级"
          description="先建年级（如「高一 2026」），再在年级下建班级，最后导入学生。"
          action={can('grade:create') ? <Button onClick={openGradeCreate}>新建年级</Button> : undefined}
        />
      ) : (
        <div className="space-y-3">
          {grades.map((g) => {
            const open = expanded.includes(g.grade_id)
            const rows = byGrade[g.grade_id] ?? []
            const students = rows.reduce((s, c) => s + (c.student_count ?? 0), 0)
            return (
              <Card key={g.grade_id} flush>
                <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
                  <button
                    onClick={() => setExpanded((prev) => (open
                      ? prev.filter((x) => x !== g.grade_id)
                      : [...prev, g.grade_id]))}
                    className="flex items-center gap-2 text-left cursor-pointer"
                  >
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    <span className="font-semibold text-ink">{g.name}</span>
                    {g.year && <Tag tone="blue">{g.year}</Tag>}
                    <span className="text-caption text-ink-muted">{rows.length} 个班 · {students} 人</span>
                  </button>
                  <div className="ml-auto flex items-center gap-2">
                    {can('class:create') && (
                      <Button variant="ghost" onClick={() => openClassCreate(g.grade_id)}>
                        <Plus size={12} className="inline mr-1" />建班
                      </Button>
                    )}
                    {can('grade:update') && (
                      <Button variant="ghost" onClick={() => openGradeEdit(g)}>编辑</Button>
                    )}
                    {can('grade:delete') && (
                      <ActionButton
                        variant="ghost" busyLabel="…" className="!px-2 !py-1 !text-[12px]"
                        onAction={async () => {
                          const r = await api.del<{ success: boolean; message?: string }>(`/api/grades/${g.grade_id}`)
                          if (!r.success) { toast.error(r.message || '删除失败'); throw new Error('failed') }
                          toast.success('年级已删除')
                          await load()
                        }}
                      >
                        <Trash2 size={12} />
                      </ActionButton>
                    )}
                  </div>
                </div>

                {open && (
                  rows.length === 0 ? (
                    <div className="px-4 py-4 text-sub text-ink-secondary">
                      该年级下还没有班级{can('class:create') ? '，点右上角「建班」' : ''}。
                    </div>
                  ) : (
                    <table className="w-full text-sub">
                      <thead>
                        <tr className="text-left text-caption text-ink-muted border-b border-border">
                          <th className="px-4 py-2 font-medium">班级</th>
                          <th className="px-4 py-2 font-medium">科目</th>
                          <th className="px-4 py-2 font-medium">学生</th>
                          <th className="px-4 py-2 font-medium">班主任</th>
                          <th className="px-4 py-2 font-medium w-40">操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((c) => (
                          <tr key={c.class_id} className="border-b border-border last:border-0">
                            <td className="px-4 py-2">{c.name}</td>
                            <td className="px-4 py-2 text-ink-secondary">{c.subject ?? '—'}</td>
                            <td className="px-4 py-2">{c.student_count ?? 0}</td>
                            <td className="px-4 py-2 text-ink-secondary">{c.teacher_name ?? '—'}</td>
                            <td className="px-4 py-2">
                              <span className="flex items-center gap-1.5">
                                {can('class:update') && (
                                  <Button variant="ghost" onClick={() => openClassEdit(g.grade_id, c)}>编辑</Button>
                                )}
                                {can('class:delete') && (
                                  <ActionButton
                                    variant="ghost" busyLabel="…" className="!px-2 !py-1 !text-[12px]"
                                    onAction={async () => {
                                      const r = await api.del<{ success: boolean; message?: string }>(`/api/classes/${c.class_id}`)
                                      if (!r.success) { toast.error(r.message || '删除失败'); throw new Error('failed') }
                                      toast.success('班级已删除')
                                      await load()
                                    }}
                                  >
                                    <Trash2 size={12} />
                                  </ActionButton>
                                )}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )
                )}
              </Card>
            )
          })}
        </div>
      )}

      <Modal
        open={gradeModal !== null}
        title={gradeModal?.mode === 'edit' ? `编辑年级：${gradeModal.grade?.name ?? ''}` : '新建年级'}
        onClose={() => setGradeModal(null)}
        onConfirm={() => { void submitGrade() }}
        confirmText="保存"
      >
        <div className="space-y-3">
          <Field label="年级名称">
            <Input value={gName} onChange={(e) => setGName(e.target.value)} placeholder="如：高一" />
          </Field>
          <Field label="入学年份">
            <Input value={gYear} onChange={(e) => setGYear(e.target.value)} placeholder="如：2026" />
          </Field>
        </div>
      </Modal>

      <Modal
        open={classModal !== null}
        title={classModal?.mode === 'edit' ? `编辑班级：${classModal.cls?.name ?? ''}` : '新建班级'}
        onClose={() => setClassModal(null)}
        onConfirm={() => { void submitClass() }}
        confirmText="保存"
      >
        <div className="space-y-3">
          <Field label="班级名称">
            <Input value={cName} onChange={(e) => setCName(e.target.value)} placeholder="如：高一 3 班" />
          </Field>
          <Field label="科目">
            <Input value={cSubject} onChange={(e) => setCSubject(e.target.value)} />
          </Field>
          <p className="text-caption text-ink-muted m-0">
            删除有学生的班级会被拒绝（先转班）；删除有班级的年级也会被拒绝。
          </p>
        </div>
      </Modal>
    </div>
  )
}
