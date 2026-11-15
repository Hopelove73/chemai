/* settings-pane.tsx — 家长设置 Pane：账号安全（行入口）+ 通知偏好 + 摘要频率
 * + 外发通道 + 退出登录（底部独立，二次确认）。
 * 契约：GET/PUT /api/parent/notify-prefs、GET/PUT /api/parent/notify-channel、
 * /test、POST /api/parent/change-password（验旧密码，落 Account 凭据面）。 */

import { useEffect, useState } from 'react'
import { Bell, ChevronRight, Loader2, LockKeyhole, Mail, Send } from 'lucide-react'
import { api } from '../../../lib/api'
import { MobileFullPane, MobileLoading } from '../../../components/mobile/MobileLayout'
import { clearMobileSession } from '../../../lib/mobile-auth'

/** 迷你开关（设置页专用：w-10 轨道 + 滑块，primary 激活） */
function PrefToggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} aria-pressed={on}
      className={`relative w-10 h-6 rounded-full transition-colors shrink-0 ${on ? 'bg-primary' : 'bg-border'}`}>
      <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-surface shadow-[var(--shadow-sm)] transition-all ${on ? 'left-[18px]' : 'left-0.5'}`} />
    </button>
  )
}

/** 修改密码全屏面板（点「账号安全」行弹出——常驻大表单不是设置页的形态） */
function ChangePasswordPane({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [oldPwd, setOldPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const submit = () => {
    setMsg(null)
    if (!oldPwd || !newPwd) {
      setMsg({ ok: false, text: '请填写原密码和新密码' })
      return
    }
    if (newPwd.length < 6) {
      setMsg({ ok: false, text: '新密码至少 6 位' })
      return
    }
    setBusy(true)
    api.post('/api/parent/change-password', { old_password: oldPwd, new_password: newPwd })
      .then(() => {
        setMsg({ ok: true, text: '密码已修改，下次登录用新密码' })
        setOldPwd('')
        setNewPwd('')
        setTimeout(onClose, 1200)  // 成功后自动回设置页
      })
      .catch((e: Error) => setMsg({ ok: false, text: e.message || '修改失败' }))
      .finally(() => setBusy(false))
  }

  return (
    <MobileFullPane open={open} title="修改密码" onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="block text-[11.5px] text-ink-muted mb-1">原密码</label>
          <input type="password" value={oldPwd} onChange={(e) => setOldPwd(e.target.value)}
            className="w-full h-11 px-3 rounded-lg border-[1.5px] border-border bg-surface text-sub text-ink outline-none focus:border-primary" />
        </div>
        <div>
          <label className="block text-[11.5px] text-ink-muted mb-1">新密码（至少 6 位）</label>
          <input type="password" value={newPwd} onChange={(e) => setNewPwd(e.target.value)}
            className="w-full h-11 px-3 rounded-lg border-[1.5px] border-border bg-surface text-sub text-ink outline-none focus:border-primary" />
        </div>
        {msg && <p className={`text-[12px] ${msg.ok ? 'text-success' : 'text-error'}`}>{msg.text}</p>}
        <button onClick={submit} disabled={busy}
          className="w-full h-11 rounded-[var(--radius-card)] bg-primary text-on-primary text-sub font-bold active:scale-[0.99] transition-transform disabled:opacity-50">
          {busy ? '提交中…' : '确认修改'}
        </button>
      </div>
    </MobileFullPane>
  )
}

export function SettingsPane() {
  const [freq, setFreq] = useState<string>('weekly')
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [channel, setChannel] = useState('')
  const [webhookMasked, setWebhookMasked] = useState('')
  const [webhookInput, setWebhookInput] = useState('')
  const [binding, setBinding] = useState(false)
  const [testing, setTesting] = useState(false)
  const [chMsg, setChMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [prefs, setPrefs] = useState<{
    types: Record<string, boolean>
    quiet_hours: { enabled: boolean; start: string; end: string }
    practice_reminder: { enabled: boolean; hour: number }
  } | null>(null)
  const [prefsMsg, setPrefsMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const [pwdOpen, setPwdOpen] = useState(false)
  const [logoutArmed, setLogoutArmed] = useState(false)

  useEffect(() => {
    api.get<{ frequency?: string }>(`/api/parent/summary-frequency`)
      .then((d) => setFreq(d.frequency || 'weekly'))
      .catch((e: Error) => setMsg({ ok: false, text: `读取频率失败：${e.message}` }))
    api.get<{ channel?: string; webhook_masked?: string }>(`/api/parent/notify-channel`)
      .then((d) => {
        setChannel(d.channel || '')
        setWebhookMasked(d.webhook_masked || '')
      })
      .catch(() => {})
    api.get<{ prefs?: { types: Record<string, boolean>; quiet_hours: { enabled: boolean; start: string; end: string }; practice_reminder: { enabled: boolean; hour: number } } }>(`/api/parent/notify-prefs`)
      .then((d) => d.prefs && setPrefs(d.prefs))
      .catch((e: Error) => {
        // 读失败回退默认形状：不无限转圈（报错留在屏上，服务恢复后保存即同步）
        setPrefs({
          types: { practice_completed: true, artifact_shared: true, streak_milestone: true },
          quiet_hours: { enabled: true, start: '22:00', end: '07:00' },
          practice_reminder: { enabled: false, hour: 19 },
        })
        setPrefsMsg({ ok: false, text: `读取通知偏好失败：${e.message}` })
      })
  }, [])

  const save = (f: string) => {
    setSaving(true)
    setMsg(null)
    api.put('/api/parent/summary-frequency', { frequency: f })
      .then(() => {
        setFreq(f)
        setMsg({ ok: true, text: '已保存' })
      })
      .catch((e: Error) => setMsg({ ok: false, text: `保存失败：${e.message || '请重试'}` }))
      .finally(() => setSaving(false))
  }

  const savePrefs = (next: NonNullable<typeof prefs>) => {
    setPrefs(next)
    setPrefsMsg(null)
    api.put('/api/parent/notify-prefs', next)
      .then(() => setPrefsMsg({ ok: true, text: '已保存' }))
      .catch((e: Error) => {
        setPrefsMsg({ ok: false, text: `保存失败：${e.message || '请重试'}` })
        api.get<{ prefs?: NonNullable<typeof prefs> }>(`/api/parent/notify-prefs`)
          .then((d) => d.prefs && setPrefs(d.prefs))  // 回读服务端真值，防本地漂移
          .catch(() => {})
      })
  }

  const toggleType = (key: string) => {
    if (!prefs) return
    savePrefs({ ...prefs, types: { ...prefs.types, [key]: !prefs.types[key] } })
  }
  const setQuiet = (patch: Partial<NonNullable<typeof prefs>['quiet_hours']>) => {
    if (!prefs) return
    savePrefs({ ...prefs, quiet_hours: { ...prefs.quiet_hours, ...patch } })
  }
  const setReminder = (patch: Partial<NonNullable<typeof prefs>['practice_reminder']>) => {
    if (!prefs) return
    savePrefs({ ...prefs, practice_reminder: { ...prefs.practice_reminder, ...patch } })
  }

  const bindWebhook = () => {
    if (!webhookInput.trim()) {
      setChMsg({ ok: false, text: '请先粘贴群机器人 webhook 地址' })
      return
    }
    setBinding(true)
    setChMsg(null)
    api.put('/api/parent/notify-channel', { webhook: webhookInput.trim() })
      .then(() => api.get<{ channel?: string; webhook_masked?: string }>(`/api/parent/notify-channel`))
      .then((d) => {
        setChannel(d.channel || '')
        setWebhookMasked(d.webhook_masked || '')
        setWebhookInput('')
        setChMsg({ ok: true, text: '已绑定，可发送测试消息验证' })
      })
      .catch((e: Error) => setChMsg({ ok: false, text: e.message || '绑定失败' }))
      .finally(() => setBinding(false))
  }

  const testSend = () => {
    setTesting(true)
    setChMsg(null)
    api.post('/api/parent/notify-channel/test', {})
      .then(() => setChMsg({ ok: true, text: '测试消息已发送，请到群里查看' }))
      .catch((e: Error) => setChMsg({ ok: false, text: e.message || '发送失败，请检查 webhook 是否有效' }))
      .finally(() => setTesting(false))
  }

  const doLogout = () => {
    clearMobileSession()
    window.location.href = '/#/m/parent-login'
  }

  const opts: { id: string; label: string }[] = [
    { id: 'weekly', label: '每周一次（周日 20:00）' },
    { id: 'daily', label: '每天（早上 8:00）' },
    { id: 'off', label: '关闭' },
  ]
  const CHANNEL_LABEL: Record<string, string> = {
    feishu: '飞书', wecom: '企业微信', dingtalk: '钉钉',
  }
  const TYPE_ROWS: { key: string; label: string }[] = [
    { key: 'practice_completed', label: '练习完成提醒' },
    { key: 'artifact_shared', label: '老师分享学习内容' },
    { key: 'streak_milestone', label: '复习连击里程碑' },
  ]

  return (
    <div className="p-3.5 space-y-3">
      {/* 账号安全：行入口 → 全屏改密面板 */}
      <button onClick={() => setPwdOpen(true)}
        className="w-full p-3.5 surface-card flex items-center gap-2.5 text-left active:bg-surface-muted transition-colors">
        <LockKeyhole size={15} className="text-primary" />
        <span className="flex-1">
          <span className="block text-sub font-semibold text-ink">账号安全</span>
          <span className="block text-[11.5px] text-ink-muted">修改登录密码</span>
        </span>
        <ChevronRight size={16} className="text-ink-muted" />
      </button>

      {/* 通知偏好：类型开关 + 免打扰 */}
      <div className="p-3.5 surface-card">
        <div className="flex items-center gap-2 mb-2.5">
          <Bell size={14} className="text-primary" />
          <span className="text-sub font-semibold text-ink">通知偏好</span>
        </div>
        {prefsMsg && (
          <p className={`text-[11.5px] mb-2 ${prefsMsg.ok ? 'text-success' : 'text-error'}`}>{prefsMsg.text}</p>
        )}
        {prefs ? (
          <div className="space-y-2.5">
            {TYPE_ROWS.map((row) => (
              <div key={row.key} className="flex items-center justify-between">
                <span className="text-sub text-ink-secondary">{row.label}</span>
                <PrefToggle on={prefs.types[row.key] !== false} onClick={() => toggleType(row.key)} />
              </div>
            ))}
            <div className="pt-2 border-t border-border/50">
              <div className="flex items-center justify-between">
                <span className="text-sub text-ink-secondary">免打扰时段</span>
                <PrefToggle on={prefs.quiet_hours.enabled} onClick={() => setQuiet({ enabled: !prefs.quiet_hours.enabled })} />
              </div>
              {prefs.quiet_hours.enabled && (
                <div className="flex items-center gap-2 mt-2">
                  <select value={prefs.quiet_hours.start} onChange={(e) => setQuiet({ start: e.target.value })}
                    className="flex-1 h-9 px-2 rounded-lg border border-border bg-surface text-sub text-ink outline-none">
                    {['20:00', '21:00', '22:00', '23:00'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <span className="text-ink-muted text-[12px]">至</span>
                  <select value={prefs.quiet_hours.end} onChange={(e) => setQuiet({ end: e.target.value })}
                    className="flex-1 h-9 px-2 rounded-lg border border-border bg-surface text-sub text-ink outline-none">
                    {['05:00', '06:00', '07:00', '08:00'].map((t) => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              )}
              <p className="text-caption text-ink-muted mt-1.5">免打扰时段内外发推送暂停，站内动态仍可查看；学业预警不受任何开关限制，始终送达。</p>
            </div>

            {/* 每日练习提醒（§11 B4）：默认关，开了才在该整点发一条「还没做完」 */}
            <div className="pt-2 border-t border-border/50">
              <div className="flex items-center justify-between">
                <span className="text-sub text-ink-secondary">每日练习提醒</span>
                <PrefToggle on={prefs.practice_reminder.enabled}
                  onClick={() => setReminder({ enabled: !prefs.practice_reminder.enabled })} />
              </div>
              {prefs.practice_reminder.enabled && (
                <div className="flex items-center justify-between mt-2">
                  <span className="text-[11.5px] text-ink-muted">提醒时间（当天练习没做完才发）</span>
                  <select value={prefs.practice_reminder.hour}
                    aria-label="提醒时间（当天练习没做完才发）"
                    onChange={(e) => setReminder({ hour: Number(e.target.value) })}
                    className="h-9 px-2 rounded-lg border border-border bg-surface text-sub text-ink outline-none">
                    {[17, 18, 19, 20, 21, 22].map((h) => <option key={h} value={h}>{h}:00</option>)}
                  </select>
                </div>
              )}
            </div>
          </div>
        ) : (
          <MobileLoading text="加载通知偏好…" />
        )}
      </div>

      {/* 摘要频率 */}
      <div className="p-3.5 surface-card">
        <div className="flex items-center gap-2 mb-2.5">
          <Mail size={14} className="text-primary" />
          <span className="text-sub font-semibold text-ink">学情摘要频率</span>
        </div>
        {msg && (
          <p className={`text-[11.5px] mb-2 ${msg.ok ? 'text-success' : 'text-error'}`}>{msg.text}</p>
        )}
        <div className="space-y-1.5">
          {opts.map((o) => (
            <button key={o.id} onClick={() => save(o.id)} disabled={saving}
              className={`w-full text-left px-3 py-2.5 rounded-lg border-[1.5px] text-sub transition-colors ${
                freq === o.id ? 'border-primary bg-primary-soft text-primary font-medium' : 'border-border bg-surface text-ink-secondary'
              } disabled:opacity-40`}>
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* 外发通道 */}
      <div className="p-3.5 surface-card">
        <div className="flex items-center gap-2 mb-2.5">
          <Send size={14} className="text-primary" />
          <span className="text-sub font-semibold text-ink">外发通知（飞书 / 企业微信 / 钉钉）</span>
        </div>
        <p className="text-[11.5px] text-ink-muted mb-2 leading-relaxed">
          在飞书 / 企业微信 / 钉钉的家长群里添加「群机器人」，把它的 webhook 地址粘贴到下面绑定，
          学情摘要就会推送到群里。
        </p>
        {chMsg && (
          <p className={`text-[11.5px] mb-2 ${chMsg.ok ? 'text-success' : 'text-error'}`}>{chMsg.text}</p>
        )}
        {channel ? (
          <div className="text-sub text-ink-secondary mb-2">
            已绑定：<span className="font-medium text-ink">{CHANNEL_LABEL[channel] || channel}</span>
            {webhookMasked && <span className="text-[11.5px] text-ink-muted ml-1">{webhookMasked}</span>}
          </div>
        ) : null}
        <div className="flex gap-2">
          <input value={webhookInput} onChange={(e) => setWebhookInput(e.target.value)}
            placeholder="粘贴群机器人 webhook 地址"
            className="flex-1 min-w-0 h-10 px-3 rounded-lg border-[1.5px] border-border bg-surface text-sub text-ink outline-none focus:border-primary" />
          <button onClick={bindWebhook} disabled={binding}
            className="px-4 h-10 rounded-lg bg-primary text-on-primary text-[12.5px] font-medium disabled:opacity-40">
            {binding ? '绑定中…' : channel ? '重新绑定' : '绑定'}
          </button>
        </div>
        <button onClick={testSend} disabled={testing || !channel}
          className="mt-2 text-[12px] text-ink-muted flex items-center gap-1 disabled:opacity-40">
          {testing ? <Loader2 size={11} className="animate-spin" /> : <Send size={11} />}发送测试消息
        </button>
      </div>

      {/* 退出登录：独立卡置底，二次确认防误触 */}
      <button
        onClick={() => (logoutArmed ? doLogout() : setLogoutArmed(true))}
        onBlur={() => setLogoutArmed(false)}
        className={`w-full h-12 rounded-[var(--radius-card)] text-sub font-semibold transition-colors ${
          logoutArmed ? 'bg-error text-white' : 'bg-surface text-error border border-border'
        }`}>
        {logoutArmed ? '再点一次确认退出' : '退出登录'}
      </button>

      {/* 关于 */}
      <p className="text-center text-caption text-ink-muted pt-1">智辅化学 ChemAI · v2.3 · 家长端</p>

      <ChangePasswordPane open={pwdOpen} onClose={() => setPwdOpen(false)} />
    </div>
  )
}
