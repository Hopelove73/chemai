/* StarDrilldownPanel — 星图下钻面板（star-map-teacher / REQ-CSM-004）。
 *
 * 点暗星 → 面板：知识点档案（复用 teacher graph 字段）+ 班级统计 + 薄弱学生
 * （懒加载 weak-students）+ 先修上下文（直接前驱掌握状态，前端本地算）+
 * 「出变式题」→ 跳出题工作台预选该知识点（?kp=），生成/派发走既有确认门。
 * 星图零写操作。 */

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Sparkles, X } from 'lucide-react'
import { api } from '../../lib/api'
import { starKind } from '../../lib/starMap'
import type { StarGraphEdge, StarGraphNode } from './StarMap'

interface WeakStudent {
  student_id: string
  name: string
  wrong_count: number
  last_wrong_at: string | null
  last_wrong_answer: string | null
}

export function StarDrilldownPanel({ node, nodes, edges, classId, onClose }: {
  node: StarGraphNode
  nodes: StarGraphNode[]
  edges: StarGraphEdge[]
  classId: string
  onClose: () => void
}) {
  const navigate = useNavigate()
  const [weak, setWeak] = useState<WeakStudent[] | null>(null)

  useEffect(() => {
    setWeak(null)
    let alive = true
    api.get<{ students?: WeakStudent[] }>(
      `/api/knowledge-map/class/${classId}/kp/${node.kp_id}/weak-students`,
    )
      .then((d) => { if (alive) setWeak(d.students || []) })
      .catch(() => { if (alive) setWeak([]) })
    return () => { alive = false }
  }, [classId, node.kp_id])

  const byId = new Map(nodes.map((n) => [n.kp_id, n]))
  const prereqs = edges
    .filter((e) => e.status === 'active' && e.to_kp === node.kp_id)
    .map((e) => byId.get(e.from_kp))
    .filter((n): n is StarGraphNode => Boolean(n))
  const unlocks = edges
    .filter((e) => e.status === 'active' && e.from_kp === node.kp_id)
    .map((e) => byId.get(e.to_kp))
    .filter((n): n is StarGraphNode => Boolean(n))

  const kind = starKind(node.class_error_rate)
  const rateText = node.class_error_rate === null
    ? '无可信样本'
    : `${(100 * node.class_error_rate).toFixed(1)}%`

  return (
    <aside
      className="absolute top-3 right-3 bottom-3 z-20 w-[340px] max-w-[85%] flex flex-col
                 rounded-[var(--radius-lg)] border border-white/10
                 bg-[#0b1220]/95 backdrop-blur-sm text-slate-200 shadow-2xl"
      aria-label="知识点下钻"
    >
      <div className="flex items-start gap-2 p-4 pb-3 border-b border-white/10">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span
              className="w-2.5 h-2.5 rounded-full shrink-0"
              style={{
                background: kind === 'gray' ? '#94a3b8' : '#2dd4bf',
                opacity: kind === 'dim' ? 0.35 : 1,
                boxShadow: kind === 'lit' ? '0 0 6px #2dd4bf' : 'none',
              }}
            />
            <h3 className="text-sm font-bold truncate">{node.name}</h3>
          </div>
          <p className="text-xs text-slate-400 mt-0.5">{node.module} · {node.theme}</p>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded-md text-slate-400 hover:text-slate-200 hover:bg-white/10 transition-colors cursor-pointer"
          aria-label="关闭下钻面板"
        >
          <X size={14} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {node.description && (
          <p className="text-xs leading-relaxed text-slate-300">{node.description}</p>
        )}

        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-lg bg-white/5 border border-white/10 px-3 py-2">
            <p className="text-[10px] text-slate-500">班级错误率</p>
            <p className={`text-sm font-semibold ${kind === 'dim' ? 'text-amber-400' : 'text-teal-300'}`}>
              {rateText}
            </p>
          </div>
          <div className="rounded-lg bg-white/5 border border-white/10 px-3 py-2">
            <p className="text-[10px] text-slate-500">班级作答量</p>
            <p className="text-sm font-semibold text-slate-200">{node.class_answers}</p>
          </div>
        </div>

        {node.equations.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold text-slate-400 mb-1.5">必背方程式</h4>
            <div className="space-y-1">
              {node.equations.slice(0, 4).map((eq, i) => (
                <p key={i} className="text-xs font-mono text-slate-300 bg-white/5 rounded-md px-2.5 py-1.5">
                  {eq}
                </p>
              ))}
            </div>
          </section>
        )}

        {node.reference_notes.length > 0 && (
          <section>
            <h4 className="text-xs font-semibold text-slate-400 mb-1.5">必背考点细则（{node.reference_notes.length}）</h4>
            <ol className="list-decimal list-inside space-y-1">
              {node.reference_notes.slice(0, 5).map((note) => (
                <li key={note.id} className="text-xs leading-relaxed text-slate-300">{note.text}</li>
              ))}
            </ol>
          </section>
        )}

        <section>
          <h4 className="text-xs font-semibold text-slate-400 mb-1.5">薄弱学生（最多 20 人）</h4>
          {weak === null ? (
            <p className="text-xs text-slate-500">加载中…</p>
          ) : weak.length === 0 ? (
            <p className="text-xs text-slate-500">这个知识点没有学生答错——不算暗星</p>
          ) : (
            <ul className="space-y-1.5">
              {weak.map((s) => (
                <li key={s.student_id} className="rounded-lg bg-white/5 border border-white/10 px-2.5 py-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-medium text-slate-200">{s.name}</span>
                    <span className="text-amber-400">{s.wrong_count} 错</span>
                  </div>
                  {s.last_wrong_answer && (
                    <p className="text-[11px] text-slate-500 mt-0.5 truncate">
                      最近：{s.last_wrong_answer}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        {(prereqs.length > 0 || unlocks.length > 0) && (
          <section>
            <h4 className="text-xs font-semibold text-slate-400 mb-1.5">先修上下文</h4>
            {prereqs.length > 0 && (
              <p className="text-xs text-slate-400 leading-relaxed">
                前置：
                {prereqs.map((p, i) => (
                  <span key={p.kp_id}>
                    {i > 0 && '、'}
                    <span className={starKind(p.class_error_rate) === 'dim' ? 'text-amber-400' : 'text-slate-300'}>
                      {p.name}
                    </span>
                  </span>
                ))}
                <span className="text-slate-600">（橙色 = 该点也薄弱，先补前置）</span>
              </p>
            )}
            {unlocks.length > 0 && (
              <p className="text-xs text-slate-500 leading-relaxed mt-1">
                后继：{unlocks.map((p) => p.name).join('、')}
              </p>
            )}
          </section>
        )}
      </div>

      <div className="p-3 border-t border-white/10">
        <button
          onClick={() => navigate(`/exam?kp=${encodeURIComponent(node.name)}`)}
          className="w-full inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-full text-xs font-medium
                     bg-[#2dd4bf] text-[#04211f] hover:bg-[#5eead4] transition-colors cursor-pointer"
        >
          <Sparkles size={13} />
          出变式题（Agent 生成 · 教师确认门）
        </button>
      </div>
    </aside>
  )
}
