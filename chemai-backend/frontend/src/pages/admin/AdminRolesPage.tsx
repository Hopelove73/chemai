import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { Card, EmptyState, SkeletonCard, Tag } from '../../components/ui'

interface RoleRow {
  role: string
  label: string
  count: number
  scope_hint: string
  permissions: Record<string, string[]>
}

const ACTION_LABEL: Record<string, string> = {
  create: '新建', read: '查看', update: '修改', delete: '删除',
}
const RESOURCE_LABEL: Record<string, string> = {
  school: '学校', grade: '年级', class: '班级', teacher: '教师', student: '学生',
  analysis: '学情分析', question: '题目', exam: '考试', exam_bank: '题库', ocr: '答题卡',
  practice: '练习', notification: '通知', warning: '预警', knowledge: '知识库',
  memory: '记忆', review: '复习', workspace: '工作区', agent: 'Agent 会话',
  integration: 'LMS 集成', log: '操作日志', settings: '运行时开关',
  break_glass: '平台提权', self_data: '本人数据', assignment: '作业',
}

/**
 * 角色与权限（/admin/roles）—— **只读**。
 * 矩阵每次从后端 ROLE_PERMISSIONS 派生；界面只解释「为什么这个按钮看不到」。
 */
export function AdminRolesPage() {
  const [roles, setRoles] = useState<RoleRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [active, setActive] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; roles: RoleRow[] }>('/api/roles/overview')
      setRoles(r.roles ?? [])
      setActive((prev) => prev || (r.roles?.[0]?.role ?? ''))
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState
      title="角色数据加载失败"
      description={error}
      action={<button className="text-primary cursor-pointer" onClick={() => { void load() }}>重试</button>}
    />
  }

  const current = roles.find((r) => r.role === active)

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">角色与权限</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          只读视图 · 权限真源在代码（<span className="font-mono text-caption">app/middleware/auth.py</span>），变更需发版
        </p>
      </header>

      <Card>
        <div className="flex flex-wrap gap-2">
          {roles.map((r) => (
            <button
              key={r.role}
              onClick={() => setActive(r.role)}
              className={`px-3 py-1.5 rounded-[var(--radius-md)] text-sm transition-colors cursor-pointer border ${
                r.role === active
                  ? 'bg-primary-soft text-primary border-primary/30 font-medium'
                  : 'bg-surface text-ink-secondary border-border hover:text-ink'
              }`}
            >
              {r.label}
              <span className="ml-1.5 text-caption text-ink-muted">{r.count} 人</span>
            </button>
          ))}
        </div>
      </Card>

      {current && (
        <>
          <Card title={`${current.label}（${current.role}）`}>
            <div className="grid grid-cols-2 gap-3 text-sub max-[860px]:grid-cols-1">
              <div>
                <div className="text-caption text-ink-muted mb-1">数据范围</div>
                <div>{current.scope_hint || '—'}</div>
              </div>
              <div>
                <div className="text-caption text-ink-muted mb-1">账号数</div>
                <div className="kpi-number font-mono">{current.count}</div>
              </div>
            </div>
          </Card>

          <Card title="权限矩阵">
            {Object.keys(current.permissions).length === 0 ? (
              <EmptyState
                title="该角色没有资源级能力"
                description="学生/家长的访问权是「身份即范围」（allow_self + 数据收窄），不走资源矩阵。"
              />
            ) : (
              <div className="grid grid-cols-2 gap-2 max-[1099px]:grid-cols-1">
                {Object.entries(current.permissions).sort(([a], [b]) => a.localeCompare(b)).map(([res, acts]) => (
                  <div key={res} className="flex items-center gap-2 rounded-[var(--radius-sm)] bg-surface-muted px-3 py-2">
                    <span className="text-sub font-medium w-24 shrink-0">
                      {RESOURCE_LABEL[res] ?? res}
                    </span>
                    <span className="font-mono text-caption text-ink-muted w-32 shrink-0">{res}</span>
                    <span className="flex flex-wrap gap-1">
                      {acts.map((a) => (
                        <Tag key={a} tone={a === 'delete' ? 'red' : a === 'update' || a === 'create' ? 'amber' : 'gray'}>
                          {ACTION_LABEL[a] ?? a}
                        </Tag>
                      ))}
                    </span>
                  </div>
                ))}
              </div>
            )}
            <p className="text-caption text-ink-muted mt-3 mb-0">
              这张表由后端矩阵实时派生（有测试保证与代码逐项一致），因此管理页改不了权限 —— 这是有意的。
            </p>
          </Card>
        </>
      )}
    </div>
  )
}
