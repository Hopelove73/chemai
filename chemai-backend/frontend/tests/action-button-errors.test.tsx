import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/lib/api'
import { DraftGatePanel } from '../src/components/chat/DraftGatePanel'
import { OcrSheetsPage } from '../src/pages/teacher/OcrSheetsPage'

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))

vi.mock('../src/components/ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('../src/components/ui')>(),
  useToast: () => toast,
}))
vi.mock('../src/components/chat/tool-renderers', () => ({
  LearningPlan: () => <div>学习计划内容</div>,
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(api, 'get').mockImplementation(async (url) => (
    url === '/api/practice/diagram-queue'
      ? { items: [{
          answer_id: 'answer-qa', student_id: 'student-qa', practice_id: 'practice-qa',
          practice_name: '回归练习', question_id: 'question-qa', attachments: ['diagram.png'],
          judged: false,
        }] }
      : {}
  ))
  vi.spyOn(api, 'post').mockRejectedValue(new Error('请求失败'))
})

afterEach(() => vi.restoreAllMocks())

describe('ActionButton 调用方的失败反馈', () => {
  it('计划批准失败时保留确认按钮，不显示已发送', async () => {
    const onDone = vi.fn()
    render(<DraftGatePanel
      studentId="student-qa"
      draft={{ draft_id: 'draft-qa', student_id: 'student-qa', version: 1,
        status: 'awaiting_confirmation', content: {} }}
      onDraftChanged={vi.fn()}
      onDone={onDone}
    />)

    fireEvent.click(screen.getByRole('button', { name: '批准发送' }))
    fireEvent.click(screen.getByRole('button', { name: '确认批准发送' }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledExactlyOnceWith('操作失败: 请求失败'))
    expect(screen.getByRole('button', { name: '确认批准发送' })).toBeEnabled()
    expect(screen.queryByText('已发送')).not.toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
    expect(onDone).not.toHaveBeenCalled()
  })

  it.each(['判对', '判错'])('装置图%s失败时恢复按钮，不显示判定完成', async (label) => {
    render(<OcrSheetsPage />)
    fireEvent.click(screen.getByRole('button', { name: '装置图' }))
    fireEvent.click(await screen.findByRole('button', { name: label }))

    await waitFor(() => expect(toast.error).toHaveBeenCalledExactlyOnceWith('判定失败'))
    expect(screen.getByRole('button', { name: label })).toBeEnabled()
    expect(screen.queryByText(label === '判对' ? '已判对' : '已判错')).not.toBeInTheDocument()
    expect(toast.success).not.toHaveBeenCalled()
  })
})
