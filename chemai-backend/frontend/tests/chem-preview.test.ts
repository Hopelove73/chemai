/* chem-preview.test.ts — LaTeX→mhchem 预览转换 + insert token 转换（REQ-CMP-002/003） */

import { describe, it, expect } from 'vitest'
import { latexToPreview } from '../src/lib/chem-preview'
import { toLatexInsert } from '../src/lib/chem-keypad'

describe('latexToPreview', () => {
  it('LaTeX 命令翻译为化学记号', () => {
    expect(latexToPreview('2H2 \\uparrow')).toBe('2H2 ↑')
    expect(latexToPreview('BaSO4 \\downarrow')).toBe('BaSO4 ↓')
    expect(latexToPreview('\\Delta')).toBe('Δ')
    expect(latexToPreview('A \\rightarrow B')).toBe('A → B')
  })

  it('上下标转 Unicode', () => {
    expect(latexToPreview('Cu^{2+}')).toBe('Cu²⁺')
    expect(latexToPreview('SO_{4}')).toBe('SO₄')
    expect(latexToPreview('Fe^3+')).toBe('Fe³⁺')
  })

  it('\\text{} 与残留花括号消除', () => {
    expect(latexToPreview('\\text{点燃}')).toBe('点燃')
    expect(latexToPreview('KClO3')).toBe('KClO3')
  })

  it('空值/原文安全', () => {
    expect(latexToPreview('')).toBe('')
    expect(latexToPreview('点燃')).toBe('点燃')
  })
})

describe('toLatexInsert', () => {
  it('结构键补花括号', () => {
    expect(toLatexInsert('^2+')).toBe('^{2+}')
    expect(toLatexInsert('_4')).toBe('_{4}')
  })
  it('普通记号原样', () => {
    expect(toLatexInsert('⇌')).toBe('⇌')
    expect(toLatexInsert('点燃')).toBe('点燃')
    expect(toLatexInsert('\\uparrow')).toBe('\\uparrow')
  })
})
