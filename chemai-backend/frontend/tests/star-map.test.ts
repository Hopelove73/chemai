/* 星图编码纯函数（star-map-teacher / REQ-CSM-002）：亮度/大小/灰星降级。 */
import { describe, expect, it } from 'vitest'
import {
  BRIGHTNESS_FLOOR,
  GRAY_BRIGHTNESS,
  moduleAnchor,
  starBrightness,
  starKind,
  starSize,
} from '../src/lib/starMap'

describe('starKind', () => {
  it('null = 灰星（无可信样本：无作答或 <MIN_ANSWERS 整条压制）', () => {
    expect(starKind(null)).toBe('gray')
    expect(starKind(undefined)).toBe('gray')
  })
  it('错误率 ≥30% = 暗星（与 kb_error_rate ERROR_RATE_THRESHOLD 同源）', () => {
    expect(starKind(0.3)).toBe('dim')
    expect(starKind(0.9)).toBe('dim')
  })
  it('错误率 <30% = 亮星', () => {
    expect(starKind(0)).toBe('lit')
    expect(starKind(0.29)).toBe('lit')
  })
})

describe('starBrightness', () => {
  it('错误率 0 → 最亮 1', () => {
    expect(starBrightness(0)).toBe(1)
  })
  it('错误率越高越暗，带可见下限，不消失', () => {
    expect(starBrightness(0.3)).toBeLessThan(starBrightness(0.1))
    expect(starBrightness(0.6)).toBeCloseTo(BRIGHTNESS_FLOOR)
    expect(starBrightness(1)).toBeCloseTo(BRIGHTNESS_FLOOR) // 超过饱和点不再更暗
  })
  it('null → 固定灰星亮度（大于 0，图例可辨）', () => {
    expect(starBrightness(null)).toBe(GRAY_BRIGHTNESS)
    expect(starBrightness(null)).toBeGreaterThan(0)
  })
})

describe('starSize', () => {
  it('无作答 → 最小 0.6；随 sqrt(作答量) 增长', () => {
    expect(starSize(0)).toBeCloseTo(0.6)
    expect(starSize(100)).toBeGreaterThan(starSize(10))
  })
  it('饱和于 1.3（SIZE_SATURATION_ANSWERS=350 之后不再变大——371 作答的星曾顶格爆 bloom）', () => {
    expect(starSize(350)).toBeCloseTo(1.3)
    expect(starSize(20000)).toBeCloseTo(1.3)
  })
})

describe('moduleAnchor', () => {
  it('六册各有独立锚点（星系团互不重叠）', () => {
    const anchors = ['必修一', '必修二', '选择性必修一', '选择性必修二', '选择性必修三', '专题方法']
      .map((m) => moduleAnchor(m))
    const keys = new Set(anchors.map((a) => `${a.x},${a.y},${a.z}`))
    expect(keys.size).toBe(6)
  })
  it('未知册回落到原点（不抛错）', () => {
    expect(moduleAnchor('不存在的册')).toEqual({ x: 0, y: 0, z: 0 })
  })
})
