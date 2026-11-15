/* ExamPage 出题降级提示（工单 VS1）。
 *
 * 后端在 /api/question/generate 的响应里带 `retrieval.degraded`；这一层没干活时题照样生成，
 * 只是不再锚在历年真题上。真渲染（jsdom + fetch 桩）而不是只测文案函数：要钉的是"老师真的
 * 会在生成结束时看到那句话"，这正是 VS1 的全部意义 —— 以前这件事只活在服务器 stdout。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExamPage } from '../src/pages/teacher/ExamPage'
import { AntdProvider } from '../src/components/antd/AntdProvider'
import { ToastProvider } from '../src/components/ui'

const GENERATED = {
  questions: [{
    content: '钠与水反应的实质是（　　）',
    options: ['A. 钠失去电子', 'B. 钠得到电子'],
    answer: 'A',
    analysis: '',
    knowledge_points: ['金属性质'],
    difficulty: 'medium',
    question_type: 'choice',
  }],
}

afterEach(() => vi.unstubAllGlobals())

/** 把生成接口返回的 `retrieval` 换给测试用；其余接口都回空数据。 */
function stubFetch(retrieval: Record<string, unknown> | undefined) {
  vi.stubGlobal(
    'fetch',
    vi.fn((input: RequestInfo | string) => {
      const url = String(input)
      if (url.includes('/api/question/generate')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ success: true, ...GENERATED, retrieval }),
        } as Response)
      }
      if (url.includes('/api/knowledge/list')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve({ knowledge_points: [{ name: '金属性质' }] }),
        } as Response)
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) } as Response)
    }),
  )
}

async function generate() {
  render(
    <AntdProvider>
      <ToastProvider>
        <ExamPage />
      </ToastProvider>
    </AntdProvider>,
  )
  await waitFor(() => expect(screen.getByText('金属性质')).toBeInTheDocument())
  fireEvent.click(screen.getByText('金属性质'))
  fireEvent.click(screen.getByText('AI 生成题目'))
}

describe('出题页的相似题检索降级提示', () => {
  it('降级时把后端那句原因说给老师', async () => {
    stubFetch({
      used: 'simple',
      degraded: true,
      reason: 'Cannot open header file（本轮只用简单匹配）',
      available: false,
    })
    await generate()

    await waitFor(() => {
      expect(document.body.textContent).toContain(
        '相似题检索暂时不可用：Cannot open header file（本轮只用简单匹配）（本次生成的题目没锚定历年真题）',
      )
    })
    // 题还是要出得来：降级不是失败，两件事必须分开说
    expect(document.body.textContent).toContain('钠与水反应的实质')
  })

  it('这一层正常时不多说话', async () => {
    stubFetch({ used: 'vector', degraded: false, reason: null, available: true, vector_count: 861 })
    await generate()

    await waitFor(() => expect(document.body.textContent).toContain('钠与水反应的实质'))
    expect(document.body.textContent).not.toContain('相似题检索暂时不可用')
  })
})
