/* 星图氛围层纯函数（star-map-atmosphere / REQ-SMA-001/003）。 */
import { describe, expect, it } from 'vitest'
import {
  DUST_LAYERS,
  makeDustLayer,
  mulberry32,
  twinkleAlpha,
  type DustLayerSpec,
} from '../src/lib/starSky'

function magnitude(positions: Float32Array, i: number): number {
  return Math.hypot(positions[i * 3], positions[i * 3 + 1], positions[i * 3 + 2])
}

describe('DUST_LAYERS', () => {
  it('三层点数与总预算', () => {
    expect(DUST_LAYERS.map((l) => l.count)).toEqual([1400, 520, 72])
    expect(DUST_LAYERS.reduce((s, l) => s + l.count, 0)).toBeLessThanOrEqual(1992)
  })

  it('半径带互不重叠（视差的几何前提）', () => {
    const [far, mid, near] = DUST_LAYERS
    expect(far.rMin).toBeGreaterThan(mid.rMax)
    expect(mid.rMin).toBeGreaterThan(near.rMax)
  })

  it('尺寸与透明度随层靠近递增', () => {
    const [far, mid, near] = DUST_LAYERS
    expect(near.size).toBeGreaterThan(mid.size)
    expect(mid.size).toBeGreaterThan(far.size)
    expect(near.opacity).toBeGreaterThan(mid.opacity)
    expect(mid.opacity).toBeGreaterThan(far.opacity)
  })
})

describe('makeDustLayer', () => {
  it('点数与半径带正确（球壳体积均匀采样落在 [rMin, rMax]）', () => {
    for (const spec of DUST_LAYERS) {
      const data = makeDustLayer(spec, 42)
      expect(data.positions.length).toBe(spec.count * 3)
      for (let i = 0; i < spec.count; i++) {
        const r = magnitude(data.positions, i)
        expect(r).toBeGreaterThanOrEqual(spec.rMin - 1e-3)
        expect(r).toBeLessThanOrEqual(spec.rMax + 1e-3)
      }
    }
  })

  it('同种子逐值可复现', () => {
    const a = makeDustLayer(DUST_LAYERS[0], 7)
    const b = makeDustLayer(DUST_LAYERS[0], 7)
    expect(a.positions).toEqual(b.positions)
    expect(a.phases).toEqual(b.phases)
    expect(a.speeds).toEqual(b.speeds)
    expect(a.colors).toEqual(b.colors)
  })

  it('不同种子产生不同星空', () => {
    const a = makeDustLayer(DUST_LAYERS[0], 7)
    const b = makeDustLayer(DUST_LAYERS[0], 8)
    expect(a.positions).not.toEqual(b.positions)
  })

  it('相位与速度落在闪烁参数区间', () => {
    const d = makeDustLayer(DUST_LAYERS[1], 3)
    for (let i = 0; i < d.phases.length; i++) {
      expect(d.phases[i]).toBeGreaterThanOrEqual(0)
      expect(d.phases[i]).toBeLessThan(Math.PI * 2)
      expect(d.speeds[i]).toBeGreaterThanOrEqual(0.9)
      expect(d.speeds[i]).toBeLessThanOrEqual(1.6)
    }
  })

  it('颜色为线性分量且在 [0,1]，且冷暖两种色温都存在', () => {
    const d = makeDustLayer(DUST_LAYERS[0], 11)
    let warm = 0
    let cool = 0
    for (let i = 0; i < d.colors.length; i += 3) {
      const [r, g, b] = [d.colors[i], d.colors[i + 1], d.colors[i + 2]]
      for (const c of [r, g, b]) {
        expect(c).toBeGreaterThanOrEqual(0)
        expect(c).toBeLessThanOrEqual(1)
      }
      if (r > b + 0.05) warm++
      if (b > r + 0.05) cool++
    }
    expect(warm).toBeGreaterThan(0)
    expect(cool).toBeGreaterThan(0)
  })

  it('空层不抛错', () => {
    const spec: DustLayerSpec = { count: 0, rMin: 1, rMax: 2, size: 1, opacity: 1 }
    const d = makeDustLayer(spec, 1)
    expect(d.positions.length).toBe(0)
    expect(d.phases.length).toBe(0)
  })
})

describe('mulberry32', () => {
  it('同种子同序列，且值域 [0,1)', () => {
    const a = mulberry32(123)
    const b = mulberry32(123)
    for (let i = 0; i < 100; i++) {
      const v = a()
      expect(v).toBe(b())
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThan(1)
    }
  })
})

describe('twinkleAlpha', () => {
  it('始终落在 0.70–1.00（不熄灭、不超基准）', () => {
    for (let t = 0; t < 20; t += 0.05) {
      const a = twinkleAlpha(t, 1.3, 0.7)
      expect(a).toBeGreaterThanOrEqual(0.7 - 1e-9)
      expect(a).toBeLessThanOrEqual(1.0 + 1e-9)
    }
  })

  it('不同相位不同步', () => {
    expect(twinkleAlpha(1, 1.3, 0)).not.toBeCloseTo(twinkleAlpha(1, 1.3, Math.PI), 3)
  })
})
