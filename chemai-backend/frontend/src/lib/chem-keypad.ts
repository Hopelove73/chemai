/* chem-keypad.ts — 化学符号键盘布局常量（student-daily-loop X1 / REQ-CMP-002）。
 * 纯数据模块：布局在此钉住（单测），ChemComposer 只负责渲染与插入。
 * token 语义：insert 即在光标处原样插入的文本片段。 */

export interface ChemKey {
  /** 插入文本（光标处） */
  insert: string
  /** 键面显示 */
  label: string
  /** 键面提示（可选） */
  title?: string
}

export interface ChemKeypadGroup {
  id: string
  label: string
  keys: ChemKey[]
}

/** 化学键盘布局：结构符号 / 反应条件 / 状态符号 / 高频离子与物质。
 * 键序按高一化学作答频率排列，改动需同步单测。 */
export const CHEM_KEYPAD: ChemKeypadGroup[] = [
  {
    id: 'structure',
    label: '结构',
    keys: [
      { insert: '⇌', label: '⇌', title: '可逆反应' },
      { insert: '→', label: '→', title: '生成箭头' },
      { insert: '=', label: '=', title: '等号' },
      { insert: '+', label: '+' },
      { insert: '^', label: 'xⁿ', title: '上标（电荷/价态）' },
      { insert: '_', label: 'xₙ', title: '下标（原子数）' },
    ],
  },
  {
    id: 'condition',
    label: '条件',
    keys: [
      { insert: '点燃', label: '点燃' },
      { insert: '\\Delta', label: 'Δ', title: '加热' },
      { insert: '催化剂', label: '催化剂' },
      { insert: '高温', label: '高温' },
      { insert: '通电', label: '通电' },
      { insert: '光照', label: '光照' },
    ],
  },
  {
    id: 'state',
    label: '状态',
    keys: [
      { insert: '\\uparrow', label: '↑', title: '气体' },
      { insert: '\\downarrow', label: '↓', title: '沉淀' },
      { insert: '(aq)', label: '(aq)', title: '溶液' },
      { insert: '(s)', label: '(s)', title: '固体' },
      { insert: '(l)', label: '(l)', title: '液体' },
      { insert: '(g)', label: '(g)', title: '气体' },
    ],
  },
  {
    id: 'common',
    label: '常用',
    keys: [
      { insert: '^2+', label: '²⁺', title: '二价阳离子' },
      { insert: '^2-', label: '²⁻', title: '二价阴离子' },
      { insert: '^+', label: '⁺', title: '阳离子' },
      { insert: '^-', label: '⁻', title: '阴离子' },
      { insert: 'H2O', label: 'H₂O' },
      { insert: 'CO2', label: 'CO₂' },
    ],
  },
]

/** 键盘键总数（单测钉住布局规模，防误删） */
export const CHEM_KEYPAD_KEY_COUNT = CHEM_KEYPAD.reduce((n, g) => n + g.keys.length, 0)

/** 键面 insert token → math-field 的 LaTeX 插入串。
 * 结构键（^ / _ 后跟记号）补花括号成合法 LaTeX 组；其余原样。 */
export function toLatexInsert(token: string): string {
  const m = String(token || '').match(/^(\^|_)([A-Za-z0-9+\-−]+)$/)
  if (m) return `${m[1]}{${m[2]}}`
  return token
}
