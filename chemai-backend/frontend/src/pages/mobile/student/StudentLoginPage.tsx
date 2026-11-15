/* StudentLoginPage — 学生登录（design/学生端.md：品牌蓝仅在此页出现，R-S-C6） */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Atom } from 'lucide-react'
import { api } from '../../../lib/api'
import { setMobileSession } from '../../../lib/mobile-auth'
import { MobileLayout } from '../../../components/mobile/MobileLayout'
import { assets } from '../../../assets'

interface LoginResp {
  success?: boolean
  message?: string
  token: string
  refresh_token?: string
  user: { id: string; username?: string; name?: string; role?: string; school?: string; class_name?: string; class_id?: string }
}

export function StudentLoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    document.title = '智辅化学 · 学生登录'
  }, [])

  async function handleLogin() {
    if (!username.trim() || !password.trim()) {
      setMsg({ text: '请输入学号和密码', ok: false })
      return
    }
    setLoading(true)
    setMsg(null)
    try {
      const d = await api.post<LoginResp>(
        '/api/auth/login',
        { username: username.trim(), password: password.trim() },
        { public: true },
      )
      setMobileSession(d.token, {
        id: d.user.id,
        username: d.user.username,
        name: d.user.name,
        role: d.user.role,
        school: d.user.school,
        class_name: d.user.class_name,
        class_id: d.user.class_id,
      })
      // 家长从这里登进来时不能落到学生端（F4）：家长端有独立首页 /m/parent
      if (d.user.role === 'parent') {
        navigate('/m/parent', { replace: true })
      } else if (d.user.role === 'teacher' || d.user.role === 'subject_lead' || d.user.role === 'admin') {
        navigate('/chat', { replace: true })
      } else {
        navigate('/m', { replace: true })
      }
    } catch (e) {
      setMsg({ text: e instanceof Error ? e.message : '网络错误，请重试', ok: false })
      setLoading(false)
    }
  }

  return (
    <MobileLayout theme="student" artIntensity="none">
      <div className="relative min-h-[100dvh] flex flex-col justify-center px-8 pb-16">
        {/* 品牌时刻底图（P-21 登录位，2026-10-05 用户素材）：深色霓虹全幅，
            上下深 scrim 保表单可读；图自带分子装饰，程序层不再叠加 */}
        <div aria-hidden className="absolute inset-0 bg-cover bg-center"
             style={{ backgroundImage: `url(${assets.studentLoginBg})` }} />
        <div aria-hidden className="absolute inset-0 bg-gradient-to-b from-black/45 via-black/10 to-black/60" />
        <div className="relative">
        {/* 品牌时刻：唯一允许品牌蓝的页面（R-S-C6） */}
        <div className="text-center mb-10">
          <div
            className="mx-auto w-16 h-16 rounded-[20px] flex items-center justify-center mb-4"
            style={{ background: 'linear-gradient(135deg, var(--color-primary), var(--color-accent-deep))' }}
          >
            {/* 品牌字形：lucide Atom（全 App 图标语言的化学字形），替换 AI 味的 H₂O 文字块 */}
            <Atom size={32} strokeWidth={2.2} className="text-white" />
          </div>
          <h1 className="font-display text-[26px] font-black text-ink">智辅化学</h1>
          <p className="text-sub text-ink-muted mt-1.5">学生登录 · 你的 AI 化学助教</p>
        </div>

        <div className="space-y-3.5">
          <div>
            <label className="block text-[12px] text-ink-secondary mb-1.5">学号</label>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="输入学号"
              autoComplete="username"
              className="w-full h-12 px-4 rounded-full border-[1.5px] border-border bg-surface-muted text-[15px] text-ink placeholder:text-ink-muted outline-none focus:border-primary transition-colors"
            />
          </div>
          <div>
            <label className="block text-[12px] text-ink-secondary mb-1.5">密码</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleLogin()}
              placeholder="输入密码"
              autoComplete="current-password"
              className="w-full h-12 px-4 rounded-full border-[1.5px] border-border bg-surface-muted text-[15px] text-ink placeholder:text-ink-muted outline-none focus:border-primary transition-colors"
            />
          </div>

          <button
            onClick={handleLogin}
            disabled={loading}
            className="w-full h-12 mt-2 rounded-full bg-primary text-on-primary text-[15px] font-bold active:scale-[0.98] transition-transform disabled:opacity-50"
          >
            {loading ? '登录中…' : '登录'}
          </button>

          {msg && (
            <p className={`text-center text-sub min-h-[20px] ${msg.ok ? 'text-success' : 'text-error'}`}>
              {msg.text}
            </p>
          )}
        </div>

        <p className="text-center text-micro text-ink-muted mt-10">
          智辅化学 ChemAI · 教师出题 · 学生辅导 · 家长关注
        </p>
        </div>
      </div>
    </MobileLayout>
  )
}
