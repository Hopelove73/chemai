/* MasteryRing.tsx — 掌握环卡（批 2 S2 / mastery-visualization REQ-MSV-001..003）。
 * 数据源 kp-mastery（mastery_probability = BKT 直读，四级映射同源 mastery_service）；
 * FSRS 到期卡所属知识点「褪色」态；点褪色节点跳复习中心。
 * 无 BKT 结论（样本不足）的知识点只展示正确率、不打级。 */

import { useCallback, useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { ringDraw } from '../../lib/motion'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, RotateCcw } from 'lucide-react'
import { api } from '../../lib/api'

interface KpRow {
  knowledge_point: string
  total_questions: number
  correct_count: number
  mastery: number
  mastery_probability: number | null
  level: string | null
}

const LEVEL_LABEL: Record<string, string> = {
  mastered: '精通',
  proficient: '熟练',
  familiar: '熟悉',
  attempted: '摸索',
}

const LEVEL_COLOR: Record<string, string> = {
  mastered: '#0f172a', // @token-allow canvas 描边需字面值（SVG stroke 不走 CSS var），待迁移 token 读取
  proficient: '#334155', // @token-allow 同上
  familiar: '#94a3b8', // @token-allow 同上
  attempted: '#e2e8f0', // @token-allow canvas 描边需字面值（SVG stroke 不走 CSS var），待迁移 token 读取
}

/** FSRS 褪色判定：该知识点有到期记忆卡（due 列表前 100 张的 KP 集合） */
export function useFadedKps(studentId?: string): Set<string> {
  const [faded, setFaded] = useState<Set<string>>(new Set())
  const load = useCallback(() => {
    if (!studentId) return
    api.get<{ tasks?: { knowledge_points?: string[] }[] }>(
      `/api/review/student/${encodeURIComponent(studentId)}/due?limit=100`,
    )
      .then((d) => {
        const s = new Set<string>()
        for (const t of d.tasks || []) for (const kp of t.knowledge_points || []) s.add(kp)
        setFaded(s)
      })
      .catch(() => {})
  }, [studentId])
  useEffect(() => {
    load()
  }, [load])
  return faded
}

export function MasteryRing({ studentId }: { studentId: string }) {
  const navigate = useNavigate()
  const [rows, setRows] = useState<KpRow[] | null>(null)
  const [coursePct, setCoursePct] = useState(0)
  const faded = useFadedKps(studentId)

  useEffect(() => {
    api.get<{ kp_mastery?: KpRow[]; course_mastery_pct?: number }>(
      `/api/report/student/${encodeURIComponent(studentId)}/kp-mastery`,
    )
      .then((d) => {
        setRows(d.kp_mastery || [])
        setCoursePct(d.course_mastery_pct ?? 0)
      })
      .catch(() => setRows([]))
  }, [studentId])

  if (!rows) return null
  if (rows.length === 0) return null // 无作答数据不渲染（空态由页面其他卡承担）

  const leveled = rows.filter((r) => r.mastery_probability !== null)
  // 课程掌握 % 由服务端 canonical 计算（course_mastery_percent）随响应下发，
  // 前端不自算（REQ-MSV-002：无第二处掌握度口径）
  // 环形进度 = 课程掌握 %；底色淡灰
  const deg = Math.round((coursePct / 100) * 360)

  const toReview = (kp: string) => navigate(faded.has(kp) ? '/m/review' : '/m/practice')

  return (
    <div className="mx-3.5 mt-3 p-4 surface-card">
      <div className="flex items-center gap-4">
        <motion.div
          {...ringDraw}
          className="relative w-24 h-24 rounded-full shrink-0"
          style={{ background: `conic-gradient(#0f172a ${deg}deg, #e2e8f0 ${deg}deg 360deg)` }} // @token-allow conic-gradient 字符串需字面值，同上
          role="img"
          aria-label={`课程掌握 ${coursePct}%`}
        >
          <div className="absolute inset-[10px] bg-surface rounded-full flex flex-col items-center justify-center">
            <span className="text-[20px] font-bold text-ink leading-none">{coursePct}%</span>
            <span className="text-[9px] text-ink-muted mt-0.5">课程掌握</span>
          </div>
        </motion.div>
        <div className="flex-1 min-w-0 space-y-1">
          {[['mastered', '精通'], ['proficient', '熟练'], ['familiar', '熟悉'], ['attempted', '摸索']].map(([lv, label]) => {
            const n = leveled.filter((r) => r.level === lv).length
            return (
              <div key={lv} className="flex items-center gap-1.5 text-micro text-ink-secondary">
                <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: LEVEL_COLOR[lv] }} />
                <span className="w-8">{label}</span>
                <span className="text-ink-muted">{n} 个知识点</span>
              </div>
            )
          })}
          <div className="text-[10px] text-ink-muted pt-0.5">
            {rows.length - leveled.length > 0 && `另有 ${rows.length - leveled.length} 个知识点数据不足，暂不下结论`}
          </div>
        </div>
      </div>

      {leveled.length > 0 && (
        <div className="mt-3 pt-2.5 border-t border-border/50 flex flex-wrap gap-1.5">
          {leveled.slice(0, 8).map((r) => {
            const isFaded = faded.has(r.knowledge_point)
            return (
              <button
                key={r.knowledge_point}
                onClick={() => toReview(r.knowledge_point)}
                className={`flex items-center gap-1 text-[10.5px] px-2 py-1 rounded-full border transition-colors ${
                  isFaded
                    ? 'border-dashed border-warning/50 bg-warning-soft text-warning active:bg-warning-soft'
                    : 'border-border bg-surface-muted text-ink-secondary active:bg-primary-soft'
                }`}
              >
                {isFaded && <RotateCcw size={9} />}
                {r.knowledge_point} · {LEVEL_LABEL[r.level || 'attempted']}{isFaded ? ' · 待复习' : ''}
              </button>
            )
          })}
          {rows.length > 8 && (
            <span className="text-[10.5px] px-2 py-1 text-ink-muted">…</span>
          )}
        </div>
      )}
      {leveled.some((r) => faded.has(r.knowledge_point)) && (
        <button
          onClick={() => navigate('/m/review')}
          className="mt-2 w-full py-1.5 rounded-full bg-primary-soft text-primary text-[12px] font-medium flex items-center justify-center gap-1"
        >
          有遗忘的知识点待复习 <ChevronRight size={12} />
        </button>
      )}
      <button
        onClick={() => navigate('/m/map')}
        className="mt-2 w-full py-1.5 rounded-full border border-border bg-surface text-ink-secondary text-[12px] flex items-center justify-center gap-1"
      >
        查看知识地图 <ChevronRight size={12} />
      </button>
    </div>
  )
}
