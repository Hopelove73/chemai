/* 确认门死锁（2026-10 实测）：门卡按钮原先由父级 `disabled={streaming}` 锁住。
 * 门开着时模型会在同一轮里继续跑（一直流式）→ 按钮整段时间是灰的 → 老师点不了
 * → 门关不掉 → 后面每次写操作都被 waiting_for_gate 挡住，越重试越死。
 * 现在卡片自管在途状态：流式中可点、连点只发一次。 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { ApprovalCard } from '../src/components/chat/ApprovalCard'

const APPROVAL = {
  text: '学习计划（第 1 版）等待老师确认，可打回修改',
  status: 'pending' as const,
  draft_id: 'd-1',
  version: 1,
  gate_id: 'g-draft-d-1',
}

describe('确认门卡不被流式锁死', () => {
  it('没有 disabled 入口：父级流式中也能点（组件不再接受该开关）', () => {
    const onResolve = vi.fn()
    render(<ApprovalCard approval={APPROVAL} onResolve={onResolve} />)
    const send = screen.getByRole('button', { name: '批准' })
    expect(send).toBeEnabled()
    fireEvent.click(send)
    expect(onResolve).toHaveBeenCalledWith('approve', undefined)
  })

  it('连点只发一次（在途状态自管）', () => {
    const onResolve = vi.fn()
    render(<ApprovalCard approval={APPROVAL} onResolve={onResolve} />)
    const send = screen.getByRole('button', { name: '批准' })
    fireEvent.click(send)
    fireEvent.click(send)
    fireEvent.click(send)
    expect(onResolve).toHaveBeenCalledTimes(1)
  })

  it('1.2s 后自动放开：请求失败也不会把按钮永久锁死', async () => {
    vi.useFakeTimers()
    const onResolve = vi.fn()
    render(<ApprovalCard approval={APPROVAL} onResolve={onResolve} />)
    fireEvent.click(screen.getByRole('button', { name: '批准' }))
    expect(screen.getByRole('button', { name: '批准' })).toBeDisabled()
    act(() => { vi.advanceTimersByTime(1300) })
    expect(screen.getByRole('button', { name: '批准' })).toBeEnabled()
    vi.useRealTimers()
  })

  it('放弃按钮同样可用（门永远有一条能关的路）', () => {
    const onResolve = vi.fn()
    render(<ApprovalCard approval={APPROVAL} onResolve={onResolve} />)
    fireEvent.click(screen.getByRole('button', { name: /放弃/ }))
    expect(onResolve).toHaveBeenCalledWith('abandon', undefined)
  })
})