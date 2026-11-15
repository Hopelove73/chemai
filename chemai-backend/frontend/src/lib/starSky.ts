/* 星图氛围层纯函数（star-map-atmosphere / REQ-SMA-001/003）。
 *
 * 只做星尘几何与参数生成：不碰 DOM、不碰 three 对象，vitest 可直接断言。
 * 随机源显式传种子（mulberry32），同种子逐值可复现——刷新后星空不变。
 *
 * 三层球壳半径互不重叠，相机移动时层间相对位移 = 自然视差，无需额外视差代码。 */

export interface DustLayerSpec {
  /** 点数 */
  count: number
  /** 球壳内半径 */
  rMin: number
  /** 球壳外半径 */
  rMax: number
  /** 世界单位点径（实际像素随距离衰减） */
  size: number
  /** 该层整体透明度 */
  opacity: number
}

/** 远 / 中 / 近三层。半径带互不重叠；总数 1992（预算 ≤ 2000）。
 * 透明度按验收反馈两轮上调（0.22/0.32/0.40 → 0.36/0.50/0.62），待机整体提亮。 */
export const DUST_LAYERS: readonly DustLayerSpec[] = [
  { count: 1400, rMin: 3000, rMax: 4400, size: 5, opacity: 0.36 },
  { count: 520, rMin: 1600, rMax: 2600, size: 8.5, opacity: 0.5 },
  { count: 72, rMin: 500, rMax: 1100, size: 14, opacity: 0.62 },
]

export interface DustLayerData {
  /** xyz 交错，长度 count*3 */
  positions: Float32Array
  /** 每点闪烁相位 [0, 2π) */
  phases: Float32Array
  /** 每点闪烁速度 [0.9, 1.6] */
  speeds: Float32Array
  /** 线性空间 rgb 交错，长度 count*3 */
  colors: Float32Array
}

/** 32 位整数种子 PRNG（mulberry32）：纯函数、不读全局 Math.random。 */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** sRGB 分量 → 线性（与 three 的 ColorManagement 一致，着色器直接吃线性值）。 */
function srgbToLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

function hexToLinear(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16)
  return [
    srgbToLinear(((n >> 16) & 255) / 255),
    srgbToLinear(((n >> 8) & 255) / 255),
    srgbToLinear((n & 255) / 255),
  ]
}

/* token-lint:disable-frame */
/* R-C9：星图深空画布专用色板（WebGL 线性空间），不参与浅色主题令牌体系 */
/** 冷暖混合：70% 冷白、20% 淡金、10% 淡蓝（视觉上避免"一锅灰点"）。 */
const COOL = hexToLinear('#dfe8ff')
const WARM = hexToLinear('#ffe9c4')
const BLUE = hexToLinear('#bcd6ff')
/* token-lint:enable-frame */

function pickColor(rand: () => number): readonly [number, number, number] {
  const p = rand()
  if (p < 0.7) return COOL
  if (p < 0.9) return WARM
  return BLUE
}

/** 球壳内体积均匀采样 + 每点相位/速度/色温。 */
export function makeDustLayer(spec: DustLayerSpec, seed: number): DustLayerData {
  const rand = mulberry32(seed)
  const n = spec.count
  const positions = new Float32Array(n * 3)
  const phases = new Float32Array(n)
  const speeds = new Float32Array(n)
  const colors = new Float32Array(n * 3)
  const span = spec.rMax - spec.rMin
  for (let i = 0; i < n; i++) {
    const theta = rand() * Math.PI * 2
    const phi = Math.acos(2 * rand() - 1)
    const r = spec.rMin + span * Math.cbrt(rand())
    const sinPhi = Math.sin(phi)
    positions[i * 3] = r * sinPhi * Math.cos(theta)
    positions[i * 3 + 1] = r * sinPhi * Math.sin(theta)
    positions[i * 3 + 2] = r * Math.cos(phi)
    phases[i] = rand() * Math.PI * 2
    speeds[i] = 0.9 + rand() * 0.7
    const [cr, cg, cb] = pickColor(rand)
    colors[i * 3] = cr
    colors[i * 3 + 1] = cg
    colors[i * 3 + 2] = cb
  }
  return { positions, phases, speeds, colors }
}

/** 星尘闪烁 alpha 系数（与顶点着色器同式）：0.70–1.00，周期 2–7 秒。 */
export function twinkleAlpha(t: number, speed: number, phase: number): number {
  return 0.7 + 0.3 * (0.5 + 0.5 * Math.sin(t * speed + phase))
}
