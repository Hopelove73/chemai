import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { ToolCard, type ToolCardData } from '../src/components/chat/ToolCard'
import { createStreamState, reduceAgentEvent } from '../src/lib/agent-stream-reducer'

describe('确认门暂缓的工具调用', () => {
  it.each<ToolCardData>([
    {
      tool: 'assign_adaptive_practice', status: 'error',
      data: { gate_open_blocked: true },
    },
    {
      tool: 'generate_parent_report', status: 'error',
      data: { error: 'waiting_for_gate' },
    },
    {
      tool: 'assign_adaptive_practice', status: 'error', error: 'waiting_for_gate',
    },
    {
      tool: 'assign_adaptive_practice', status: 'paused',
      data: { error: 'waiting_for_gate', gate_open_blocked: true, ui_label: '等待确认' },
    },
  ])('历史和兼容载荷统一显示中性等待状态：%j', (card) => {
    const { container } = render(<ToolCard card={card} />)

    expect(screen.getByText('已暂缓 · 等待确认')).toBeInTheDocument()
    expect(screen.queryByText('失败')).not.toBeInTheDocument()
    expect(screen.queryByText('waiting_for_gate', { exact: false })).not.toBeInTheDocument()
    expect(screen.queryByText(/已为.*学生布置练习/)).not.toBeInTheDocument()
    expect(container.querySelector('[class*="text-error"], [class*="border-error"], .animate-pulse')).toBeNull()
  })

  it('流式暂缓后批准重放：保留两次调用记录，分别展示暂缓和完成', () => {
    let seq = 0
    const ctx = { newId: () => String(++seq) }
    let state = reduceAgentEvent(createStreamState(), {
      type: 'tool_call', name: 'assign_adaptive_practice',
    }, ctx)
    state = reduceAgentEvent(state, {
      type: 'tool_result', name: 'assign_adaptive_practice', success: false,
      result: JSON.stringify({
        error: 'waiting_for_gate', gate_open_blocked: true, ui_label: '等待确认',
      }),
    }, ctx)
    state = reduceAgentEvent(state, {
      type: 'tool_result', name: 'assign_adaptive_practice', success: true,
      result: JSON.stringify({ assigned_count: 2 }),
    }, ctx)

    const cards = state.items.flatMap((item) => item.kind === 'tool' ? [item.card] : [])
    expect(cards).toHaveLength(2)
    render(<>{cards.map((card, i) => <ToolCard key={i} card={card} />)}</>)

    const buttons = screen.getAllByRole('button')
    expect(within(buttons[0]).getByText('已暂缓 · 等待确认')).toBeInTheDocument()
    expect(within(buttons[1]).getByText('完成')).toBeInTheDocument()
    expect(screen.queryByText('失败')).not.toBeInTheDocument()
  })

  it('真实工具错误仍显示失败和原因', () => {
    const { container } = render(<ToolCard card={{
      tool: 'web_search', status: 'error', error: '网络不可用',
      data: { error: 'network_error', ui_label: '网络不可用' },
    }} />)

    expect(screen.getByText('失败')).toBeInTheDocument()
    expect(screen.getAllByText('网络不可用')).not.toHaveLength(0)
    expect(screen.queryByText('已暂缓 · 等待确认')).not.toBeInTheDocument()
    expect(container.querySelector('[class*="border-error"]')).not.toBeNull()
  })
})
