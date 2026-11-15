/* 家长端报告页下钻（§11 B3）：每块卡点开都要摊出真实明细，而不是只弹个空壳。
 * 断言是内容级的：答对/答错数、统计区间、三类障碍占比、知识点排名。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import * as apiMod from '../src/lib/api'

vi.mock('../src/lib/api', () => ({ api: { get: vi.fn() } }))

import { ReportPane } from '../src/pages/mobile/parent/ParentHomePage'

const apiGet = vi.mocked(apiMod.api.get) as unknown as ReturnType<typeof vi.fn>

const WEEKLY = {
  success: true,
  weekly: {
    practice_count: 12,
    practice_completed: 8,
    accuracy_rate: 0.67,
    streak_days: 4,
    weak_knowledge_points: ['氧化还原', '离子方程式'],
    week_start: '2026-10-14',
    week_end: '2026-10-20',
  },
}
const REPORT = {
  success: true,
  report: {
    exercises_completed: 87,
    last_exercise_at: '2026-10-20T08:00:00',
    barrier_type: { concept: 0.5, reading: 0.3, expression: 0.2 },
  },
}

async function mount() {
  apiGet.mockImplementation((url?: string) =>
    Promise.resolve(String(url ?? '').includes('/weekly') ? WEEKLY : REPORT))
  render(<ReportPane child={{ student_id: 's1' }} parentId='p1' />)
  await screen.findByText('本周正确率')
}

describe('家长端报告页下钻', () => {
  beforeEach(() => apiGet.mockReset())

  it('正确率卡摊出答对/答错与统计区间', async () => {
    await mount()
    fireEvent.click(screen.getByText('本周正确率'))
    const body = await screen.findByText(/本周共练/)
    expect(body).toHaveTextContent('本周共练 12 道，答对 8 道、答错 4 道')
    expect(screen.getByText(/2026-10-14 ~ 2026-10-20/)).toBeInTheDocument()
  })

  it('知识点卡摊出排名明细', async () => {
    await mount()
    fireEvent.click(screen.getByText('需要加强的知识点'))
    expect(await screen.findByText('1. 氧化还原')).toBeInTheDocument()
    expect(screen.getByText('2. 离子方程式')).toBeInTheDocument()
  })

  it('家庭配合建议摊出三类障碍占比', async () => {
    await mount()
    fireEvent.click(screen.getByText('家庭配合建议'))
    expect(await screen.findByText('50%')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
    expect(screen.getByText('20%')).toBeInTheDocument()
  })

  it('没有练习数据时正确率明细说人话，不显示 0/0', async () => {
    apiGet.mockImplementation((url?: string) => Promise.resolve(String(url ?? '').includes('/weekly')
      ? { success: true, weekly: { practice_count: 0, practice_completed: 0, accuracy_rate: 0, weak_knowledge_points: ['暂无薄弱知识点数据'] } }
      : REPORT))
    render(<ReportPane child={{ student_id: 's1' }} parentId='p1' />)
    fireEvent.click(await screen.findByText('本周正确率'))
    expect(await screen.findByText(/本周还没有练习记录/)).toBeInTheDocument()
  })
})