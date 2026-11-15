/* 题目选项里的裸 LaTeX 泄漏（2026-10 实测，live 会话 t-1792616525774）。
 * 现场：选项 `$$H_2O_2 + 2KI + H_2SO_4 \rightarrow I_2 + K_2SO_4 + 2H_2O$$` 渲染后，页面文本
 * 里既有渲好的产物，又多出一行 `I + H I+H_2SO_4 \rightarrow I_2 + K_2SO_4 + 2H_2O`。
 * 根因：renderChemMD 末尾的「自动识别化学方程式」后处理把 \ce 的 KaTeX HTML 注入了
 * KaTeX 自己的 MathML <annotation> —— annotation 被提前闭合，剩下的原始 TeX 于是变成
 * 普通文本节点渲到页面上。修好后：可见输出里不得残留裸命令，箭头必须是真的箭头。 */
import { describe, expect, it } from 'vitest'
import { cleanLatex, renderChemMD } from '../src/lib/chem-markdown'

/** KaTeX 的 MathML 层把原始 TeX 留在 <annotation> 里，浏览器不渲染它（实测页面文本里
 *  不计入：修复前全页 \rightarrow 只有 1 次，而三条选项的 annotation 都含它）。
 *  这里同样只检查 annotation 之外的可见部分。 */
const visible = (html: string) => html.replace(/<annotation\b[\s\S]*?<\/annotation>/g, '')

/** 真实题目选项（出题工具返回的原文，取自现场会话的 checkpoint） */
const REAL_OPTIONS = [
  'A. $$2H_2O_2 \\xrightarrow{{MnO_2}} 2H_2O + O_2\\uparrow$$',
  'B. $$H_2O_2 + 2KI + H_2SO_4 \\rightarrow I_2 + K_2SO_4 + 2H_2O$$',
  'C. $$H_2O_2 + Cl_2 \\rightarrow 2HCl + O_2$$',
  'D. $$H_2O_2 + 2FeCl_2 + 2HCl \\rightarrow 2FeCl_3 + 2H_2O$$',
]

describe('题目选项：渲染器不把裸 LaTeX 漏到页面上', () => {
  it('含 \\rightarrow / \\uparrow / \\xrightarrow 的选项：可见输出无裸命令，含 → / ↑', () => {
    for (const opt of REAL_OPTIONS) {
      const out = visible(renderChemMD(opt))
      expect(out).not.toContain('\\rightarrow')
      expect(out).not.toContain('\\uparrow')
      expect(out).not.toContain('\\xrightarrow')
      expect(out).not.toContain('\\text')
      expect(out).not.toContain('$')
    }
    const arrow = visible(renderChemMD(REAL_OPTIONS[1]))
    expect(arrow).toContain('→')
    const up = visible(renderChemMD(REAL_OPTIONS[0]))
    expect(up).toContain('↑')
  })

  it('化学式仍然是下标（不是把反斜杠删掉了事）', () => {
    const out = renderChemMD(REAL_OPTIONS[1])
    expect(out).toContain('<msub>')
    expect(visible(out)).not.toContain('H_2SO_4')
  })

  it('\\text{...} 里的中文按文字渲出，不露 \\text', () => {
    const out = visible(renderChemMD('$$2H_2O_2 \\xrightarrow{\\text{点燃}} 2H_2O + O_2\\uparrow$$'))
    expect(out).not.toContain('\\text')
    expect(out).not.toContain('\\xrightarrow')
    expect(out).toContain('点燃')
    expect(out).toContain('↑')
  })

  it('KaTeX 的 MathML annotation 仍然保留原始 TeX（可访问性不倒退）', () => {
    // 注释只是浏览器不渲染，源码要在 —— 否则读屏/复制公式会退化
    expect(renderChemMD(REAL_OPTIONS[1])).toContain('<annotation')
  })
})
/* ── 分隔符没配对（2026-10 实测 C1/C2，模型多吐一个 $ / $$）──────────────────────
 * C1：`…$2KMnO_4 + 16HCl(浓$) \rightarrow 2KCl + … + 8H_2O$$`
 * C2：`…$MnO_2 + 4HCl(浓$) \xrightarrow{{\triangle}} MnCl_2 + … + 2H_2O$$`
 * 按配对切分之后剩下那截原来是纯文本，\rightarrow / \uparrow / \xrightarrow 直接露在页面上。 */
const C1 = '用 $KMnO_4$ 与浓盐酸反应制取氯气：$2KMnO_4 + 16HCl(浓$) \\rightarrow 2KCl + 2MnCl_2 + 5Cl_2\\uparrow + 8H_2O$$。下列说法正确的是（ ）'
const C2 = '在反应 $MnO_2 + 4HCl(浓$) \\xrightarrow{{\\triangle}} MnCl_2 + Cl_2\\uparrow + 2H_2O$$ 中，被氧化的 $HCl$ 与 被还原的 $MnO_2$ 的物质的量之比为（ ）'

describe('分隔符没配对的残段：兜底渲成公式，不许露原文', () => {
  it('C1（多余 $ 与结尾 $$）：可见文本无裸命令、无 $，箭头是真箭头、下标仍是 <msub>', () => {
    const html = renderChemMD(C1)
    const out = visible(html)
    expect(out).not.toContain('\\rightarrow')
    expect(out).not.toContain('\\uparrow')
    expect(out).not.toContain('$')
    expect(out).toContain('→')
    expect(out).toContain('↑')
    expect(out).toContain('<msub>')
  })

  it('C2（多余 $ + \\xrightarrow{{\\triangle}} 嵌套花括号）：三角渲在箭头上方，无裸命令', () => {
    const html = renderChemMD(C2)
    const out = visible(html)
    expect(out).not.toContain('\\xrightarrow')
    expect(out).not.toContain('\\triangle')
    expect(out).not.toContain('\\uparrow')
    expect(out).not.toContain('$')
    expect(out).toContain('△')
    expect(out).toContain('<msub>')
  })

  it('空白清理不得把命令词和后面的字母粘死（\\rightarrow KCl → \\rightarrowKCl 会被 KaTeX 原样吐出）', () => {
    const src = '配平化学方程式 $KMnO_4 + HCl(浓) \\rightarrow KCl + MnCl_2 + Cl_2\\uparrow + H_2O$$，下列系数正确的是（ ）'
    // 清理后命令词后面必须还留着那个空格
    expect(cleanLatex('$\\rightarrow KCl$')).toBe('$\\rightarrow KCl$')
    // 下标归一化不受影响（命令后是花括号，不是字母）
    expect(cleanLatex('$\\mathrm { C H } _ { 3 }$')).toBe('$\\mathrm{CH}_{3}$')
    const out = visible(renderChemMD(src))
    expect(out).not.toContain('\\rightarrowKC')
    expect(out).not.toContain('\\uparrow')
    expect(out).toContain('→')
    expect(out).toContain('<msub>')
  })

  it('兜底不会把 Windows 路径当 LaTeX 渲（\\Users 不是控制序列）', () => {
    const out = visible(renderChemMD('文件在 C:\\Users\\Administrator\\Desktop\\x.md 里'))
    expect(out).toContain('C:\\Users\\Administrator')
    expect(out).not.toContain('katex')
  })

  it('代码块里的反斜杠源码不被当公式渲', () => {
    const out = visible(renderChemMD('\`\`\`\n\\rightarrow 2KCl\n\`\`\`'))
    expect(out).toContain('<pre><code>')
    expect(out).toContain('\\rightarrow 2KCl')
  })
})

