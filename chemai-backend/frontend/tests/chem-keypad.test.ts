/* chem-keypad.test.ts — 键盘布局常量钉住（REQ-CMP-002）：布局误删/键序漂移即红 */

import { describe, it, expect } from 'vitest'
import { CHEM_KEYPAD, CHEM_KEYPAD_KEY_COUNT } from '../src/lib/chem-keypad'

describe('CHEM_KEYPAD', () => {
  it('四组布局：结构/条件/状态/常用', () => {
    expect(CHEM_KEYPAD.map((g) => g.id)).toEqual(['structure', 'condition', 'state', 'common'])
  })

  it('关键键存在：⇌ / 点燃 / ↑↓ / 电荷', () => {
    const all = CHEM_KEYPAD.flatMap((g) => g.keys)
    const inserts = all.map((k) => k.insert)
    expect(inserts).toContain('⇌')
    expect(inserts).toContain('点燃')
    expect(inserts).toContain('催化剂')
    expect(inserts).toContain('\\uparrow')
    expect(inserts).toContain('\\downarrow')
    expect(inserts).toContain('\\Delta')
    expect(inserts).toContain('^2+')
    expect(all.every((k) => k.label.length > 0)).toBe(true)
  })

  it('键总数钉住（改动布局须同步本测试）', () => {
    expect(CHEM_KEYPAD_KEY_COUNT).toBe(24)
  })

  it('组内 label 不重复', () => {
    for (const g of CHEM_KEYPAD) {
      const labels = g.keys.map((k) => k.label)
      expect(new Set(labels).size).toBe(labels.length)
    }
  })
})
