/* A2：工件面板（ArtifactPanel）——三 tab、联动聚焦、沙箱 iframe、blob 取数。
 *
 * 钉的是老师可感知的行为：
 *   1. 空态文案（引导第一眼）；
 *   2. 附件 tab：原图经 blob: 加载——img 不直接指向鉴权端点（子资源带不上
 *      Authorization header，直接 src 必 401，code-review P0）；
 *   3. 工件 tab：iframe 的 src 是 blob: 且 sandbox="allow-scripts"；
 *   4. focus 联动切 tab + 点历史卡片从详情接口拉回工件。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ArtifactPanel, type PanelFocus, type ArtifactRef, type Attachment } from '../src/components/chat/ArtifactPanel'
import * as apiMod from '../src/lib/api'

vi.mock('../src/lib/api', () => ({
  api: { get: vi.fn() },
}))

const apiGet = vi.mocked(apiMod.api.get) as unknown as ReturnType<typeof vi.fn>

/* fetch 桩：返回可用的 blob（jsdom 的 File 继承自 Blob，URL.createObjectURL 已被 jsdom 支持为合成 url） */
const fetchMock = vi.fn(async (path: string) => ({
  ok: true,
  blob: async () => new Blob([`content-of-${path}`]),
}))
vi.stubGlobal('fetch', fetchMock)

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
  apiGet.mockReset().mockResolvedValue({ versions: [], current_version: 1, artifact_id: '', kind: '', title: '' })
  fetchMock.mockClear()
  localStorage.setItem('token', 'test-token')
})

const ART: ArtifactRef = {
  artifact_id: 'art_abc', kind: 'animation', title: '盐类水解教学动画',
  version_seq: 2, url: '/static/artifacts/2026-09-30/art_abc_v2.html',
}

const IMG: Attachment = {
  upload_id: 'up-1', file_name: '答题卡.png',
  image_url: '/api/agent/upload/up-1/image',
}

function renderPanel(over: {
  focus?: PanelFocus | null
  artifacts?: ArtifactRef[]
  attachments?: Attachment[]
} = {}) {
  return render(<ArtifactPanel
    open
    onToggle={() => {}}
    focus={over.focus ?? null}
    artifacts={over.artifacts ?? []}
    attachments={over.attachments ?? []}
  />)
}

describe('工件面板 · 空态', () => {
  it('三个 tab 都可切换，空态文案引导下一步', () => {
    renderPanel()
    expect(screen.getByText('工件面板')).toBeTruthy()
    fireEvent.click(screen.getByTestId('panel-tab-attachments'))
    expect(screen.getByText(/还没有上传附件/)).toBeTruthy()
    fireEvent.click(screen.getByTestId('panel-tab-versions'))
    expect(screen.getByText(/版本历史在这里/)).toBeTruthy()
  })
})

describe('工件面板 · 附件原图（P0 修复：blob 取数）', () => {
  it('带 Authorization fetch 原图，img 的 src 是 blob: 而非鉴权端点', async () => {
    renderPanel({ attachments: [IMG], focus: { tab: 'attachments', targetId: 'up-1' } })

    await waitFor(() => expect(screen.getByTestId('attachment-image')).toBeTruthy())
    // fetch 必须带上了 token（真实环境 401 的根因就是带不上）
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/agent/upload/up-1/image',
      expect.objectContaining({ headers: { Authorization: 'Bearer test-token' } }))
    // img.src 是 blob:（子资源请求零鉴权暴露）
    const img = screen.getByTestId('attachment-image') as HTMLImageElement
    expect(img.src.startsWith('blob:')).toBe(true)
    expect(img.src).not.toContain('/api/agent/upload')
  })
})

describe('工件面板 · 工件预览（P0 修复：iframe 走 blob）', () => {
  it('iframe sandbox=allow-scripts，src 是 blob:，取数带鉴权', async () => {
    apiGet.mockResolvedValue({
      artifact_id: 'art_abc', kind: 'animation', title: '盐类水解教学动画',
      versions: [{ seq: 1, url: ART.url, note: null }, { seq: 2, url: ART.url, note: '加深底色' }],
      current_version: 2,
    })
    renderPanel({ artifacts: [ART], focus: { tab: 'artifact', targetId: 'art_abc' } })

    await waitFor(() => expect(screen.getByTestId('artifact-frame')).toBeTruthy())
    await waitFor(() => {
      const frame = screen.getByTestId('artifact-frame') as HTMLIFrameElement
      expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
      expect(frame.src.startsWith('blob:')).toBe(true)
    })
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/artifacts/art_abc/v/2`,
      expect.objectContaining({ headers: { Authorization: 'Bearer test-token' } }))
  })

  it('focus 指向数组里没有的工件（历史恢复卡片）→ 从详情接口拉回', async () => {
    apiGet.mockImplementation((path: string) => {
      if (path === '/api/artifacts/art_old') {
        return Promise.resolve({
          artifact_id: 'art_old', kind: 'doc', title: '旧教案',
          versions: [{ seq: 1, url: '/static/artifacts/old.html', note: null }],
          current_version: 1,
        })
      }
      return Promise.resolve({ versions: [], current_version: 1 })
    })
    renderPanel({ focus: { tab: 'artifact', targetId: 'art_old' } })

    // 拉回的工件出现在工件列表（按钮复合文本 "旧教案 · v1"）
    await waitFor(() => expect(screen.getByText(/旧教案/)).toBeTruthy())
    expect(apiGet).toHaveBeenCalledWith('/api/artifacts/art_old')
  })
})
