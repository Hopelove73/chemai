/* A4：我的工件库列表页（ArtifactsPage）——跨会话索引的验收。
 *
 * 真渲染（jsdom + fetch 桩）钉四件老师可感知的事：
 *   1. 列表行：标题/类型/版本/日期；
 *   2. 类型筛选真的换了请求参数；
 *   3. 空态引导去工作台（第一眼知道去哪产工件）；
 *   4. 点行 → 预览 iframe 的取数走 blob（带 Authorization，P0 同款口径）。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ArtifactsPage } from '../src/pages/teacher/ArtifactsPage'

const fetchMock = vi.fn(async (path: string) => ({
  ok: true,
  blob: async () => new Blob([`content-of-${path}`]),
}))

/** 智能桩：列表请求回 JSON，版本文件请求回 blob（同一个 fetch，按路径路由）——
 * 页面里列表与预览走的是同一个全局 fetch。 */
function makeSmartFetch(listItems: unknown[]) {
  return vi.fn(async (path: string) => {
    if (String(path).includes('/v/')) {
      return { ok: true, blob: async () => new Blob([`content-of-${path}`]) }
    }
    return { ok: true, json: async () => ({ items: listItems }) }
  })
}

function renderPage() {
  return render(<MemoryRouter><ArtifactsPage /></MemoryRouter>)
}

beforeEach(() => {
  fetchMock.mockClear()
  vi.stubGlobal('fetch', fetchMock)
  localStorage.setItem('token', 'test-token')
})

const ROWS = [
  { artifact_id: 'art-a', kind: 'animation', title: '盐类水解动画', current_version: 3, updated_at: '2026-09-30T10:00:00' },
  { artifact_id: 'art-b', kind: 'doc', title: '期中讲评稿', current_version: 1, updated_at: '2026-09-29T10:00:00' },
]

describe('我的工件 · 列表', () => {
  it('渲染标题/类型/版本/日期行', async () => {
    vi.stubGlobal('fetch', makeSmartFetch(ROWS))
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('artifact-row')).toHaveLength(2))
    expect(screen.getByText('盐类水解动画')).toBeTruthy()
    expect(screen.getByText(/期中讲评稿/)).toBeTruthy()
    expect(screen.getByText(/v3/)).toBeTruthy()
  })

  it('点类型筛选换请求参数', async () => {
    const f = makeSmartFetch([])
    vi.stubGlobal('fetch', f)
    renderPage()
    await waitFor(() => expect(f).toHaveBeenCalled())
    f.mockClear()
    fireEvent.click(screen.getByText('文档'))
    await waitFor(() => {
      const called = f.mock.calls.map((c) => String(c[0]))
      expect(called.some((u) => u.includes('kind=doc'))).toBe(true)
    })
  })

  it('空态引导去工作台', async () => {
    vi.stubGlobal('fetch', makeSmartFetch([]))
    renderPage()
    await waitFor(() => expect(screen.getByText(/去 Agent 工作台/)).toBeTruthy())
  })
})

describe('我的工件 · 预览', () => {
  it('点行 → 预览 iframe 取数带 Authorization 且 src 是 blob:', async () => {
    const smart = makeSmartFetch(ROWS)
    vi.stubGlobal('fetch', smart)
    renderPage()
    await waitFor(() => expect(screen.getAllByTestId('artifact-row')).toHaveLength(2))
    fireEvent.click(screen.getByText('盐类水解动画'))
    await waitFor(() => expect(screen.getByTestId('library-frame')).toBeTruthy())
    const frame = screen.getByTestId('library-frame') as HTMLIFrameElement
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.src.startsWith('blob:')).toBe(true)
    expect(smart).toHaveBeenCalledWith(
      '/api/artifacts/art-a/v/3',
      expect.objectContaining({ headers: { Authorization: 'Bearer test-token' } }))
  })
})
