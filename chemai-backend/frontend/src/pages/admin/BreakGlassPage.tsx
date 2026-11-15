import { useCallback, useEffect, useRef, useState } from 'react'
import { ShieldAlert, ShieldCheck } from 'lucide-react'
import { api } from '../../lib/api'
import {
  Badge, Button, Card, EmptyState, Field, Select, SkeletonCard, Textarea, useToast,
} from '../../components/ui'
import { ActionButton, useAsyncAction } from '../../components/ui/ActionButton'

interface BreakGlassSession {
  session_id: string
  reason: string
  started_at: string
  expires_at: string
  ended_at?: string | null
  remaining_seconds: number
}

const MAX_MINUTES = 60

/**
 * 超管提权工作台（/admin/break-glass，gate break_glass:read 仅 super_admin）。
 * 纯前端 change：发起/查生效/提前结束三个既有端点；纪律在后端
 * （理由必填 400、限时上限、不叠开、全程审计）。倒计时本地递减，
 * 到 0 主动重查一次防时钟漂移。
 */
export function BreakGlassPage() {
  const toast = useToast()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [session, setSession] = useState<BreakGlassSession | null>(null)
  const [remaining, setRemaining] = useState(0)
  const [reason, setReason] = useState('')
  const [minutes, setMinutes] = useState('15')
  const [revoking, setRevoking] = useState(false)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const r = await api.get<{ success: boolean; session: BreakGlassSession | null }>('/api/break-glass/active')
      setSession(r.session)
      setRemaining(r.session?.remaining_seconds ?? 0)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  /* 倒计时：每秒递减；到 0 重查一次（后端到期自动失效，前端只负责展示诚实） */
  useEffect(() => {
    if (!session) return
    timerRef.current = setInterval(() => {
      setRemaining((s) => {
        if (s <= 1) {
          clearInterval(timerRef.current!)
          void load()
          return 0
        }
        return s - 1
      })
    }, 1000)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [session, load])

  const submit = useAsyncAction(async () => {
    if (!reason.trim()) { toast.error('提权理由必填'); return }
    const r = await api.post<{ success: boolean; message?: string; session?: BreakGlassSession }>(
      '/api/break-glass', { reason: reason.trim(), minutes: Number(minutes) || 15 })
    if (!r.success) { toast.error(r.message || '发起失败'); return }
    toast.success(r.message || '提权已生效（此操作已审计留痕）')
    setReason('')
    await load()
  })

  const revoke = useAsyncAction(async () => {
    const r = await api.del<{ success: boolean; message?: string }>('/api/break-glass')
    if (!r.success) { toast.error(r.message || '提前结束失败'); return }
    toast.success('提权已提前结束')
    setRevoking(false)
    await load()
  })

  const mm = String(Math.floor(remaining / 60)).padStart(2, '0')
  const ss = String(remaining % 60).padStart(2, '0')

  if (loading) return <SkeletonCard />
  if (error) {
    return <EmptyState title="提权状态加载失败" description={error} action={<Button onClick={() => { void load() }}>重试</Button>} />
  }

  return (
    <div className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold text-ink m-0">平台提权（Break-Glass）</h1>
        <p className="text-sub text-ink-secondary mt-1 mb-0">
          超管跨校排查用的限时提权 —— 每一次发起与结束都有审计留痕，到期自动失效
        </p>
      </header>

      {session ? (
        <Card>
          <div className="flex items-start gap-4">
            <div className="w-10 h-10 rounded-[var(--radius-md)] bg-warning-soft text-warning flex items-center justify-center shrink-0">
              <ShieldAlert size={20} />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2">
                <Badge status="warning" label="提权生效中" />
                <span className="kpi-number text-[22px] font-semibold text-warning font-mono">{mm}:{ss}</span>
              </div>
              <div className="text-sub text-ink mt-2">理由：{session.reason}</div>
              <div className="text-caption text-ink-muted mt-1">
                开始 {session.started_at?.replace('T', ' ').slice(0, 19)} · 到期 {session.expires_at?.replace('T', ' ').slice(0, 19)}
              </div>
              <div className="mt-3">
                {revoking ? (
                  <span className="inline-flex items-center gap-1.5">
                    <ActionButton
                      onAction={() => revoke.run()}
                      variant="danger" busyLabel="结束中…" className="!px-2 !py-1 !text-[12px]"
                    >确认提前结束</ActionButton>
                    <Button variant="ghost" onClick={() => setRevoking(false)}>取消</Button>
                  </span>
                ) : (
                  <Button variant="ghost" onClick={() => setRevoking(true)}>提前结束</Button>
                )}
              </div>
            </div>
          </div>
        </Card>
      ) : (
        <Card>
          <EmptyState
            illustration={<div className="w-24 h-24 rounded-[var(--radius-lg)] bg-surface-muted flex items-center justify-center border border-dashed border-border-strong"><ShieldCheck size={30} className="text-ink-muted/70" /></div>}
            title="当前没有生效中的提权"
            description="超管默认不属于任何学校，数据范围为空（ADR-0011）。需要跨校排查时，在下方发起一次限时提权；用完即掉，也可提前结束。"
          />
        </Card>
      )}

      <Card title="发起提权">
        <div className="space-y-3 max-w-md">
          <Field label="提权理由（必填，将随审计留痕）">
            <Textarea value={reason} onChange={(e) => setReason(e.target.value)}
              placeholder="如：排查 sch2 的成绩导入异常" rows={2} />
          </Field>
          <Field label="时长">
            <Select value={minutes} onChange={(e) => setMinutes(e.target.value)}>
              <option value="15">15 分钟</option>
              <option value="30">30 分钟</option>
              <option value="60">60 分钟（上限）</option>
            </Select>
          </Field>
          <div className="flex items-center gap-2">
            <ActionButton
              onAction={() => submit.run()}
              disabled={!reason.trim()}
              busyLabel="发起中…"
            >发起提权</ActionButton>
            {!reason.trim() && <span className="text-caption text-ink-muted">填写理由后可发起</span>}
          </div>
          <p className="text-caption text-ink-muted m-0">
            单次最长 {MAX_MINUTES} 分钟；已有生效中的提权时不叠加（重复发起会展示既有会话）。
          </p>
        </div>
      </Card>
    </div>
  )
}
