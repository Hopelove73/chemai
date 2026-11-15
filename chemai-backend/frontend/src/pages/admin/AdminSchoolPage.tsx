import { useCallback, useEffect, useState } from 'react'
import { api, can } from '../../lib/api'
import { Button, Card, EmptyState, Field, Input, SkeletonCard, Tabs, useToast } from '../../components/ui'
import { useAsyncAction } from '../../components/ui/ActionButton'
import { ImportWizard } from './ImportWizard'

interface SchoolInfo {
  school_id: string
  name: string
  region?: string | null
  address?: string | null
  phone?: string | null
  current_term?: string | null
  subjects?: string[]
}

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error'; message: string }
  | { phase: 'missing' }
  | { phase: 'ready'; school: SchoolInfo }

/**
 * 学校设置（/admin/school，gate school:update）。
 * 两页签：学校信息（未初始化时为建校表单）+ 数据导入（school-init-wizard，
 * 步骤按 class:create / teacher:create / exam:create 显隐）。
 */
export function AdminSchoolPage() {
  const toast = useToast()
  const [state, setState] = useState<LoadState>({ phase: 'loading' })
  const [tab, setTab] = useState('info')
  /* 建校表单（未初始化） */
  const [newName, setNewName] = useState('')
  const [newRegion, setNewRegion] = useState('')
  const [newAddress, setNewAddress] = useState('')
  const [newPhone, setNewPhone] = useState('')
  const [newTerm, setNewTerm] = useState('')
  /* 编辑表单（已初始化） */
  const [draft, setDraft] = useState<SchoolInfo | null>(null)

  const load = useCallback(async () => {
    setState({ phase: 'loading' })
    try {
      const r = await api.get<{ success: boolean; message?: string; school?: SchoolInfo }>('/api/school')
      if (!r.success || !r.school) {
        setState({ phase: 'missing' })
        return
      }
      setState({ phase: 'ready', school: r.school })
    } catch (e) {
      setState({ phase: 'error', message: e instanceof Error ? e.message : '加载失败' })
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const create = useAsyncAction(async () => {
    if (!newName.trim()) { toast.error('学校名称必填'); return }
    const r = await api.post<{ success: boolean; message?: string }>('/api/school', {
      name: newName.trim(), region: newRegion || null, address: newAddress || null,
      phone: newPhone || null, current_term: newTerm || null,
    })
    if (!r.success) { toast.error(r.message || '创建失败'); return }
    toast.success('学校已创建')
    await load()
  })

  const save = useAsyncAction(async () => {
    if (!draft) return
    if (!draft.name.trim()) { toast.error('学校名称必填'); return }
    const r = await api.put<{ success: boolean; message?: string }>('/api/school', {
      name: draft.name.trim(), region: draft.region ?? null, address: draft.address ?? null,
      phone: draft.phone ?? null, current_term: draft.current_term ?? null,
    })
    if (!r.success) { toast.error(r.message || '保存失败'); return }
    toast.success('学校信息已更新')
    await load()
  })

  const canImport = can('class:create')
  const tabs = [
    { key: 'info', label: '学校信息' },
    ...(canImport ? [{ key: 'import', label: '数据导入' }] : []),
  ]

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">学校设置</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">学校基础信息与批量数据导入</p>
      </header>

      <Tabs items={tabs} active={tab} onChange={setTab} />

      {tab === 'import' && canImport && <ImportWizard />}

      {tab === 'info' && state.phase === 'loading' && <SkeletonCard />}

      {tab === 'info' && state.phase === 'error' && (
        <EmptyState
          title="学校信息加载失败" description={state.message}
          action={<Button onClick={() => { void load() }}>重试</Button>}
        />
      )}

      {tab === 'info' && state.phase === 'missing' && (
        <Card>
          <EmptyState
            title="学校尚未初始化"
            description="先创建学校，才能导入年级/班级/师生数据。"
            action={
              <div className="w-full max-w-md space-y-3 text-left">
                <Field label="学校名称 *">
                  <Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="如：XX 市第一中学" />
                </Field>
                <Field label="所在地区">
                  <Input value={newRegion} onChange={(e) => setNewRegion(e.target.value)} />
                </Field>
                <Field label="地址">
                  <Input value={newAddress} onChange={(e) => setNewAddress(e.target.value)} />
                </Field>
                <Field label="联系电话">
                  <Input value={newPhone} onChange={(e) => setNewPhone(e.target.value)} />
                </Field>
                <Field label="当前学期">
                  <Input value={newTerm} onChange={(e) => setNewTerm(e.target.value)} placeholder="如：2026 春季学期" />
                </Field>
                <Button
                  onClick={() => { void create.run() }}
                  disabled={create.state === 'busy'}
                >
                  {create.state === 'busy' ? '创建中…' : '创建学校'}
                </Button>
              </div>
            }
          />
        </Card>
      )}

      {tab === 'info' && state.phase === 'ready' && (() => {
        const school = draft ?? state.school
        return (
          <Card title="学校信息">
            <div className="grid grid-cols-2 gap-3 max-[860px]:grid-cols-1">
              <Field label="学校名称">
                <Input value={school.name} onChange={(e) => setDraft({ ...school, name: e.target.value })} />
              </Field>
              <Field label="所在地区">
                <Input value={school.region ?? ''} onChange={(e) => setDraft({ ...school, region: e.target.value })} />
              </Field>
              <Field label="地址">
                <Input value={school.address ?? ''} onChange={(e) => setDraft({ ...school, address: e.target.value })} />
              </Field>
              <Field label="联系电话">
                <Input value={school.phone ?? ''} onChange={(e) => setDraft({ ...school, phone: e.target.value })} />
              </Field>
              <Field label="当前学期">
                <Input value={school.current_term ?? ''} onChange={(e) => setDraft({ ...school, current_term: e.target.value })} />
              </Field>
            </div>
            <div className="mt-4 flex items-center gap-2">
              <Button
                onClick={() => { void save.run() }}
                disabled={save.state === 'busy' || !draft}
              >
                {save.state === 'busy' ? '保存中…' : '保存'}
              </Button>
              {draft && <Button variant="ghost" onClick={() => setDraft(null)}>放弃修改</Button>}
              {!draft && <span className="text-caption text-ink-muted">修改后点击保存</span>}
            </div>
          </Card>
        )
      })()}

      {/* 只读角色兜底提示（页面 gate 是 school:update，这里防直连 URL 的困惑） */}
      {!can('school:update') && tab === 'info' && (
        <p className="text-caption text-ink-muted m-0">当前账号为只读视图（无 school:update 能力）。</p>
      )}
    </div>
  )
}
