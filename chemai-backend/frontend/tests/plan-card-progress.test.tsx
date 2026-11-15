import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { MessageList, type ChatItem } from '../src/components/chat/MessageList'
import { createStreamState, reduceAgentEvent, seedNewTurn } from '../src/lib/agent-stream-reducer'
import type { AgentEvent } from '../src/lib/agent-sse'

const goal = '诊断后布置练习并发送报告'
const steps = [
  { step: 1, skill: 'diagnose_barrier', description: '诊断薄弱点', status: 'pending' },
  { step: 2, skill: 'assign_adaptive_practice', description: '布置针对性练习', status: 'pending' },
  { step: 3, skill: 'send_report_to_parent', description: '发送家长报告', status: 'pending' },
]

beforeEach(() => { Element.prototype.scrollIntoView = () => {} })

function planState() {
  let seq = 0
  const ctx = { newId: () => `plan-${++seq}` }
  let state = createStreamState()
  const feed = (event: AgentEvent) => { state = reduceAgentEvent(state, event, ctx) }
  feed({ type: 'tool_call', name: 'plan_task' })
  feed({ type: 'tool_result', name: 'plan_task', success: true, data: { goal, steps } })
  feed({ type: 'plan_summary', goal, total_steps: 3, steps: steps.map((s, i) => ({ ...s, status: i === 0 ? 'running' : 'pending' })) })
  return { feed, items: () => state.items }
}

function view(items: ChatItem[]) {
  return <MessageList items={items} onApproval={() => {}} />
}

/* 计划卡（PlanCard）的步骤行**常驻渲染**，没有可点的头、也没有 <ul>/<li>：
 * MessageList 已经把那张家「任务规划」工具卡刻意去掉（两张卡渲染同一份计划，
 * 计划卡信息更全）。所以这里按「步骤行」收集、按卡分组，不再点按钮展开。
 */
function isPlanRow(el: Element): boolean {
  const c = (el as HTMLElement).className
  return typeof c === 'string' && c.includes('items-center')
    && c.includes('gap-2.5') && c.includes('text-sub')
}

function planRowGroups(): HTMLElement[][] {
  const groups = new Map<Element, HTMLElement[]>()
  Array.from(document.querySelectorAll('div')).filter(isPlanRow).forEach((row) => {
    const parent = row.parentElement!
    groups.set(parent, [...(groups.get(parent) ?? []), row as HTMLElement])
  })
  return [...groups.values()]
}

/** 第 index 张计划卡的步骤行（DOM 顺序 = 卡的出现顺序）。 */
function toolSteps(index = 0): HTMLElement[] {
  return planRowGroups()[index] ?? []
}

describe('任务规划工具卡的逐步进度', () => {
  it('摘要中的真实状态不会被重置，未知状态保守显示', () => {
    let seq = 0
    const state = reduceAgentEvent(createStreamState(), {
      type: 'plan_summary', goal, steps: steps.map((s, i) => ({
        ...s, status: ['completed', 'awaiting_confirmation', 'future-status'][i],
      })),
    }, { newId: () => String(++seq) })
    const plan = state.items.find((item) => item.kind === 'plan')
    expect(plan?.kind === 'plan' && plan.plan.steps.map((s) => s.status))
      .toEqual(['completed', 'awaiting_confirmation', 'unknown'])
  })

  it('摘要保留真实状态，等待确认、修订、完成和放弃都同步到工具卡', () => {
    const state = planState()
    const { rerender } = render(view(state.items()))
    expect(toolSteps()[0]).toHaveTextContent('执行中')
    expect(toolSteps()[1]).toHaveTextContent('等待中')

    for (const [status, label] of [
      ['awaiting_confirmation', '等待确认'], ['revising', '修订中'],
      ['failed', '失败'], ['cancelled', '已放弃'], ['completed', '完成'],
    ]) {
      state.feed({ type: 'plan_progress', current_step: 1, status })
      rerender(view(state.items()))
      expect(toolSteps()[0]).toHaveTextContent(label)
      expect(toolSteps()[1]).toHaveTextContent('等待中')
    }
  })

  it('完成后仍逐条保留完成标记', () => {
    const state = planState()
    for (const step of steps) state.feed({ type: 'plan_progress', current_step: step.step, status: 'completed' })
    render(view(state.items()))
    expect(toolSteps()).toHaveLength(3)
    for (const row of toolSteps()) expect(row).toHaveTextContent(/✓.*完成/)
  })

  it('新计划的进度不改写之前计划，未知状态不冒充完成', () => {
    const state = planState()
    state.feed({ type: 'plan_progress', current_step: 1, status: 'completed' })
    const newGoal = '另一份任务'
    state.feed({ type: 'tool_call', name: 'plan_task' })
    state.feed({ type: 'tool_result', name: 'plan_task', data: { goal: newGoal, steps } })
    state.feed({ type: 'plan_summary', goal: newGoal, steps })
    state.feed({ type: 'plan_progress', current_step: 1, status: 'future-status' })
    render(view(state.items()))
    expect(toolSteps(0)[0]).toHaveTextContent(/✓.*完成/)
    expect(toolSteps(1)[0]).toHaveTextContent('未知')
  })

  it('同目标再次规划，只给最新一次工具调用同步状态', () => {
    const state = planState()
    state.feed({ type: 'tool_call', name: 'plan_task' })
    state.feed({ type: 'tool_result', name: 'plan_task', data: { goal, steps } })
    state.feed({ type: 'plan_summary', goal, steps })
    state.feed({ type: 'plan_progress', current_step: 2, status: 'awaiting_confirmation' })
    render(view(state.items()))
    // 同 goal 的 plan_summary 是**原地替换**（不再堆第二张卡）——这正是本条要钉的：
    // 两次规划共用一张卡，状态只跟最新一次推进，不会留下两张各说各话的卡。
    expect(planRowGroups()).toHaveLength(1)
    expect(toolSteps(0)[1]).toHaveTextContent('等待确认')
    expect(toolSteps(0)[0]).toHaveTextContent('等待中')
  })

  it('重规划较早目标时，进度仍跟随本次摘要而非列表末尾的旧计划', () => {
    const state = planState()
    state.feed({ type: 'plan_summary', goal: '其他目标', steps })
    state.feed({ type: 'plan_summary', goal, steps })
    state.feed({ type: 'plan_progress', current_step: 2, status: 'awaiting_confirmation' })
    const plans = state.items().filter((item) => item.kind === 'plan')
    expect(plans[0].plan.steps[1].status).toBe('awaiting_confirmation')
    expect(plans[1].plan.steps[1].status).toBe('pending')
  })

  it('打回或文字修订开启新回合时，保留当前计划身份', () => {
    let seq = 0
    const ctx = { newId: () => String(++seq) }
    let state = createStreamState()
    for (const name of [goal, '其他目标', goal]) {
      state = reduceAgentEvent(state, { type: 'plan_summary', goal: name, steps }, ctx)
    }
    state = seedNewTurn(state.items, '请减轻每天的练习量', ctx.newId, state.activePlanId).state
    state = reduceAgentEvent(state, { type: 'plan_progress', current_step: 2, status: 'revising' }, ctx)
    const plans = state.items.filter((item) => item.kind === 'plan')
    expect(plans[0].plan.steps[1].status).toBe('revising')
    expect(plans[1].plan.steps[1].status).toBe('pending')
  })
})
