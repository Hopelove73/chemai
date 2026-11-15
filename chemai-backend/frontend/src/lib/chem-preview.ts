/* chem-preview.ts — 作答 LaTeX → mhchem 预览文本的纯转换（X1 / REQ-CMP-003）。
 * math-field 输出 LaTeX；\ce{} 只吃化学记号，这里把 LaTeX 专属记号翻译成
 * mhchem 能消化的形式（ translating 失败降级为原文，预览不阻塞提交）。 */

const SUPERSCRIPTS: Record<string, string> = {
  '0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
  '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹',
  '+': '⁺', '-': '⁻', 'n': 'ⁿ',
}

const SUBSCRIPTS: Record<string, string> = {
  '0': '₀', '1': '₁', '2': '₂', '3': '₃', '4': '₄',
  '5': '₅', '6': '₆', '7': '₇', '8': '₈', '9': '₉',
}

const SYMBOLS: Record<string, string> = {
  '\\Delta': 'Δ',
  '\\rightarrow': '→',
  '\\uparrow': '↑',
  '\\downarrow': '↓',
  '\\times': '×',
  '\\le': '≤',
  '\\ge': '≥',
}

/** math-field 的 LaTeX 输出 → mhchem 可渲染的化学文本。 */
export function latexToPreview(latex: string): string {
  let s = String(latex || '')
  for (const [cmd, ch] of Object.entries(SYMBOLS)) s = s.split(cmd).join(ch)
  // \text{...} → 内容
  s = s.replace(/\\text\s*\{([^{}]*)\}/g, '$1')
  // ^{2+} / ^2+ → Unicode 上标
  s = s.replace(/\^\{([^{}]+)\}/g, (_, body: string) => superscript(body))
  s = s.replace(/\^([0-9+-]+)/g, (_, body: string) => superscript(body))
  // _{3} / _3 → Unicode 下标（mhchem 也会自动下标数字，这里保证非数字下标可见）
  s = s.replace(/_\{([^{}]+)\}/g, (_, body: string) => subscript(body))
  s = s.replace(/_([0-9]+)/g, (_, body: string) => subscript(body))
  // mhchem 不需要的花括号去掉
  s = s.replace(/[{}]/g, '')
  return s.trim()
}

function superscript(body: string): string {
  return [...body].map((c) => SUPERSCRIPTS[c] ?? c).join('')
}

function subscript(body: string): string {
  return [...body].map((c) => SUBSCRIPTS[c] ?? c).join('')
}
