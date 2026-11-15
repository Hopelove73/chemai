/* ChemGlyph — 化学视觉语言（总册 R-C8 / 学生分册 R-S-C10）。
 * 元素 tile 是三端唯一的图标语言：tile 内只放真实元素符号 + 原子序数，
 * 语义由使用处的文字承担（NEVER 伪造元素字母）。
 * 状态/文档类标识不进 tile —— 走 StatusDot（语义色点）与 lucide 图标。 */

export const GLYPHS = {
  objective: { sym: 'O', num: 8 },   // 目标 / 规划
  task: { sym: 'Zn', num: 30 },      // 任务 / 计划执行
  diagnosis: { sym: 'Cu', num: 29 }, // 诊断 / 障碍干预
  insight: { sym: 'Li', num: 3 },    // 洞察 / 建议
  stats: { sym: 'Fe', num: 26 },     // 统计 / 掌握度
  experiment: { sym: 'Na', num: 11 },// 实验 / 操作
  equation: { sym: 'H', num: 1 },    // 方程式 / 配平
} as const

export type GlyphKind = keyof typeof GLYPHS

/** 化学式水印（总册 P-22）：教师判卷中心 + 学生成绩页各一处封顶。装饰性巨字，NEVER 进列表/表单。 */
export function ChemHeroWatermark({ formula, className = '' }: { formula: string; className?: string }) {
  return (
    <span
      aria-hidden
      className={`chem-mono pointer-events-none select-none absolute right-4 top-2 text-[80px] font-black leading-none text-primary/[0.05] ${className}`}
    >
      {formula}
    </span>
  )
}

export function ChemGlyph({ kind, size = 22 }: { kind: GlyphKind; size?: number }) {
  const g = GLYPHS[kind]
  return (
    <span
      aria-hidden
      style={{ width: size, height: size }}
      className="inline-flex shrink-0 select-none flex-col items-center justify-center rounded-[4px] border border-border bg-surface-muted align-[-4px]"
    >
      <span className="chem-mono font-bold leading-none text-teal" style={{ fontSize: size * 0.42 }}>
        {g.sym}
      </span>
      <span className="chem-mono leading-none text-ink-muted" style={{ fontSize: size * 0.27 }}>
        {g.num}
      </span>
    </span>
  )
}

/** 状态色点（R-S-C10）：✅⚠️⏳❌ 一类状态 emoji 的语义替代 */
export function StatusDot({ tone, label }: { tone: 'success' | 'warning' | 'error' | 'pending'; label?: string }) {
  const cls =
    tone === 'success' ? 'bg-success' : tone === 'warning' ? 'bg-warning' : tone === 'error' ? 'bg-error' : 'bg-ink-muted'
  return (
    <span className="inline-flex items-center gap-1.5 align-[-2px]">
      <span aria-hidden className={`inline-block h-2 w-2 rounded-full ${cls} ${tone === 'pending' ? 'animate-pulse' : ''}`} />
      {label && <span>{label}</span>}
    </span>
  )
}
