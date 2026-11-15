/* BackgroundArt — 背景艺术层（总册 P-21）：三端强度分档的层叠接缝。
 * 层 1：外供纹理图（用户素材已接线，student 深色霓虹 / parent 浅色水彩）；
 * 层 2：persona 渐变洗（令牌驱动）；
 * 层 3：自绘分子网络 SVG（键线拓扑，原创图形语言）。
 * 接口按数组留位——每端可多张。
 * 强度：学生端沉浸（fixed 全域）/ 家长端浅（顶部 hero）/ 教师端零（沿用壳底图）。 */
import { assets } from '../../assets'

/** 分子网络（层 3）：确定性伪随机 + 边缘偏置 + 淡键线。
 * 设计律（U 系截图复盘后定稿）：背景装饰必须「第二眼才看见」——
 * 布点推离中心内容带、键线低透明度、整层极慢漂移；NEVER 穿过正文成为前景。 */
function MoleculeNet({ seed = 7, count = 10 }: { seed?: number; count?: number }) {
  const pts = Array.from({ length: count }, (_, i) => {
    const a = Math.sin(seed * 97 + i * 31) * 10000
    const rawX = (a - Math.floor(a)) * 100
    const b = Math.sin(seed * 57 + i * 13) * 10000
    const rawY = (b - Math.floor(b)) * 100
    // 边缘偏置：离中心内容带 < 32 的点向外推（中心留给正文）
    let x = 2 + rawX * 0.96
    let y = 2 + rawY * 0.96
    const dx = x - 50
    const dy = (y - 50) * 1.35
    const d = Math.hypot(dx, dy) || 0.01
    if (d < 32) {
      x = 50 + (dx / d) * 36
      y = 50 + (dy / d) * 26.5
    }
    return { x: Math.max(3, Math.min(97, x)), y: Math.max(3, Math.min(97, y)) }
  })
  const bonds: [number, number][] = []
  for (let i = 0; i < pts.length; i++) {
    const near = pts
      .map((q, j) => ({ j, d: (q.x - pts[i].x) ** 2 + (q.y - pts[i].y) ** 2 }))
      .filter(({ j }) => j !== i)
      .sort((m, n) => m.d - n.d)
      .slice(0, 1) // 只连最近 1 点：键少而短，不成大网
    for (const { j } of near) if (!bonds.some(([a, b]) => (a === j && b === i) || (a === i && b === j))) bonds.push([i, j])
  }
  return (
    <svg aria-hidden className="molecule-net absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice">
      <g className="molecule-drift">
        {bonds.map(([a, b], i) => (
          <line key={i} x1={pts[a].x} y1={pts[a].y} x2={pts[b].x} y2={pts[b].y}
                stroke="currentColor" strokeWidth={0.14} strokeOpacity={0.28} strokeLinecap="round" />
        ))}
        {pts.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={i % 4 === 0 ? 0.7 : 0.42} fill="currentColor" fillOpacity={i % 4 === 0 ? 0.5 : 0.3} />
        ))}
      </g>
    </svg>
  )
}

export type BackgroundIntensity = 'immersive' | 'subtle' | 'none'

/** 纹理注册表（层 1）：用户素材 2026-10-05 就位接线（P-21）。
 * - student：深色霓虹线稿，图底近纯黑、与主题沉浸底同源 → screen 混合让
 *   黑底消失、只留荧光分子；immersive 全域。
 * - parent：浅色水彩，图底与主题鼠尾草画布同源 → 常规混合、顶部对位
 *   （subtle 只露 h-40 hero 带，图饰集中在顶角）。
 * 混合/透明度按端分档是材质事实：图底与主题底同色系时高不透明度也不脏内容。 */
interface Texture {
  src: string
  opacity: number
  blend?: 'screen'
  position?: 'top' | 'center'
}
const TEXTURES: Record<'student' | 'parent' | 'teacher', Texture[]> = {
  student: [{ src: assets.studentMainBg, opacity: 0.55, blend: 'screen' }],
  parent: [{ src: assets.parentMainBg, opacity: 0.55, position: 'top' }],
  teacher: [],
}

export function BackgroundArt({ theme, intensity }: { theme: 'student' | 'parent' | 'teacher'; intensity: BackgroundIntensity }) {
  if (intensity === 'none') return null
  const immersive = intensity === 'immersive'
  return (
    <div aria-hidden className={`pointer-events-none absolute inset-0 overflow-hidden text-primary ${immersive ? '' : 'h-40'}`}>
      {/* 层 1：外供纹理（数组留位）；对位用字面量映射——动态拼类 Tailwind 扫不到 */}
      {TEXTURES[theme].map((t) => (
        <div
          key={t.src}
          className={`absolute inset-0 bg-cover ${t.position === 'top' ? 'bg-top' : 'bg-center'}`}
          style={{ backgroundImage: `url(${t.src})`, opacity: t.opacity, mixBlendMode: t.blend }}
        />
      ))}
      {/* 层 2：persona 渐变洗（单彩槽内做深浅，不引入第二彩相，R-S-C1/R-C8） */}
      <div
        className="absolute inset-0"
        style={{
          background: `radial-gradient(120% 90% at 15% 0%, var(--color-primary-soft) 0%, transparent 55%),
                       radial-gradient(100% 80% at 90% 100%, var(--color-primary-soft) 0%, transparent 50%)`,
          opacity: immersive ? 0.34 : 0.5,
        }}
      />
      {/* 层 3：分子网络 */}
      <MoleculeNet seed={theme === 'student' ? 7 : 11} />
    </div>
  )
}
