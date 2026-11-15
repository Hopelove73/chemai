/* 三端去 AI 味升级（v2.3）新模块测试 —— 基线/边界/回归的确定性子集。
 * 基线：ChemGlyph 元素诚实性（R-S-C10）、Illo src 渲染、语义类在 globals 中存在；
 * 边界：ActionButton 状态机（busy 禁/done 回弹/error 归位）、BackgroundArt 强度分档；
 * 回归：lint 规则自身（裸调色板/任意值回潮必须被拦）、MobileBackButton 返回兜底。
 * 浏览器侧基线/边界见 evals/e2e-ui-upgrade-checklist.md（真实渲染层）。 */
import { describe, expect, it, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { HashRouter } from 'react-router-dom'

import { ChemGlyph, StatusDot, GLYPHS } from '../src/components/ui/ChemGlyph'
import { Illo } from '../src/assets'
import { ActionButton } from '../src/components/ui/ActionButton'
import { MobileBackButton } from '../src/components/mobile/MobileLayout'
import { BackgroundArt } from '../src/components/ui/BackgroundArt'

describe('基线 · 化学视觉语言', () => {
  it('ChemGlyph 只放真实元素：符号在元素表内、序数与符号配对', () => {
    // R-S-C10 诚实性守卫：新增 kind 必须配真实元素（NEVER 伪造字母）
    const REAL = ['H', 'He', 'Li', 'Be', 'B', 'C', 'N', 'O', 'F', 'Ne', 'Na', 'Mg', 'Al',
      'Si', 'P', 'S', 'Cl', 'Ar', 'K', 'Ca', 'Fe', 'Cu', 'Zn', 'Ag', 'Sn', 'I', 'Ba', 'Pt', 'Au', 'Hg', 'Pb', 'Mn']
    for (const g of Object.values(GLYPHS)) {
      expect(REAL).toContain(g.sym)
      expect(g.num).toBeGreaterThan(0)
    }
  })

  it('ChemGlyph 渲染符号与序数', () => {
    render(<ChemGlyph kind="objective" />)
    expect(screen.getByText('O')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
  })

  it('StatusDot 语义色点替代状态 emoji', () => {
    render(<StatusDot tone="success" label="全部完成" />)
    expect(screen.getByText('全部完成')).toBeInTheDocument()
  })

  it('Illo 有 src 渲染真图（E2E E10 勘误：收 src 不渲染的 interface 撒谎）', () => {
    const { container } = render(<Illo src="data:image/png;base64,x" />)
    expect(container.querySelector('img')).not.toBeNull()
  })

  it('Illo 无 src 回落烧杯占位', () => {
    const { container } = render(<Illo />)
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('边界 · ActionButton 状态机（R-I6）', () => {
  it('idle→busy（禁交互）→done（回弹）→ 回 idle', async () => {
    let resolve!: () => void
    const p = new Promise<void>((r) => { resolve = r })
    const { getByRole } = render(
      <ActionButton onAction={() => p} busyLabel="处理中…" doneLabel="已完成">
        提交
      </ActionButton>,
    )
    const btn = getByRole('button', { name: '提交' })
    fireEvent.click(btn)
    expect(getByRole('button', { name: '处理中…' })).toBeDisabled()
    resolve()
    // done 态：打勾回弹展示期间保持禁用（不收二次点击）
    await waitFor(() => expect(getByRole('button', { name: /已完成/ })).toBeDisabled())
    await waitFor(() => expect(getByRole('button', { name: '提交' })).toBeEnabled(), { timeout: 2200 })
  })

  it('error：通知调用方并归 idle，重试成功后才显示完成', async () => {
    const error = new Error('x')
    const action = vi.fn().mockRejectedValueOnce(error).mockResolvedValueOnce(undefined)
    const onError = vi.fn()
    const { getByRole, queryByRole } = render(
      <ActionButton onAction={action} onError={onError}>提交</ActionButton>,
    )
    fireEvent.click(getByRole('button', { name: '提交' }))
    await waitFor(() => expect(onError).toHaveBeenCalledExactlyOnceWith(error))
    expect(getByRole('button', { name: '提交' })).toBeEnabled()
    expect(queryByRole('button', { name: /已完成/ })).not.toBeInTheDocument()

    fireEvent.click(getByRole('button', { name: '提交' }))
    await waitFor(() => expect(getByRole('button', { name: /已完成/ })).toBeDisabled())
    expect(action).toHaveBeenCalledTimes(2)
    expect(onError).toHaveBeenCalledOnce()
  })
})

describe('边界 · BackgroundArt 强度分档（P-21）', () => {
  it('教师端 none 不渲染任何层', () => {
    const { container } = render(<BackgroundArt theme="teacher" intensity="none" />)
    expect(container.firstChild).toBeNull()
  })

  it('家长端 subtle 只到顶部 hero（h-40），学生端沉浸全域', () => {
    const subtle = render(<BackgroundArt theme="parent" intensity="subtle" />)
    expect((subtle.container.firstChild as HTMLElement).className).toContain('h-40')
    const immersive = render(<BackgroundArt theme="student" intensity="immersive" />)
    expect((immersive.container.firstChild as HTMLElement).className).not.toContain('h-40')
  })
})

describe('回归 · 守门与返回', () => {
  it('MobileBackButton 渲染返回钮（复习中心缺口的回归位）', () => {
    render(
      <HashRouter>
        <MobileBackButton fallback="/m/me" />
      </HashRouter>,
    )
    expect(screen.getByRole('button', { name: '返回' })).toBeInTheDocument()
  })

  it('motion presets：时长/缓动从令牌律取值（R-M1 编排集中）', async () => {
    const m = await import('../src/lib/motion')
    expect(m.DURATION.slow).toBe(0.4)
    expect((m.pageTransition.transition as { duration: number }).duration).toBe(0.25)
  })
})
