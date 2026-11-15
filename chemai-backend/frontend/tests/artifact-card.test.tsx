/* A1：工件卡（capability-upgrade-spec 接缝 2：SSE artifact 事件 → 消息流卡片）。
 *
 * 两层各钉最痛的行为：
 *   1. reducer：artifact 事件追加独立工件卡（不挂工具卡、多次生成不互相覆盖）；
 *   2. 真渲染：MessageList 收到工件项时渲染标题/类型/版本与「预览」按钮。
 * 历史恢复（ChatPage 把 tool data.artifact 重建为卡片）走源码文本断言——
 * 真渲染那套 fetch 桩太重，而要钉的只是「别把恢复分支删了」。
 */
import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { reduceAgentEvent, seedNewTurn } from '../src/lib/agent-stream-reducer'
import { MessageList } from '../src/components/chat/MessageList'
import type { ArtifactEvent } from '../src/lib/agent-sse'
import chatPageSrc from '../src/pages/teacher/ChatPage.tsx?raw'

beforeEach(() => {
  // jsdom 没有 scrollIntoView（MessageList 挂载即滚底）
  Element.prototype.scrollIntoView = vi.fn()
})

const nid = (() => {
  let n = 0
  return () => `id-${++n}`
})()

function artifactEvent(over: Partial<ArtifactEvent> = {}): ArtifactEvent {
  return {
    type: 'artifact',
    artifact_id: 'art_abc',
    kind: 'animation',
    title: '盐类水解教学动画',
    version_seq: 1,
    url: '/static/artifacts/2026-09-30/art_abc_v1.html',
    ...over,
  }
}

describe('reducer：artifact 事件', () => {
  it('追加独立工件卡，字段完整', () => {
    const seeded = seedNewTurn([], '做个盐类水解动画', nid)
    const next = reduceAgentEvent(seeded.state, artifactEvent(), { newId: nid })

    const cards = next.items.filter((it) => it.kind === 'artifact')
    expect(cards).toHaveLength(1)
    const card = cards[0] as Extract<(typeof cards)[0], { kind: 'artifact' }>
    expect(card.artifact_id).toBe('art_abc')
    expect(card.artKind).toBe('animation')
    expect(card.title).toBe('盐类水解教学动画')
    expect(card.versionSeq).toBe(1)
    expect(card.url).toContain('/static/artifacts/')
  })

  it('修订再生成是两张卡（v1/v2 并存），不是原地替换', () => {
    const seeded = seedNewTurn([], '深底看不清，重做', nid)
    const afterV1 = reduceAgentEvent(seeded.state, artifactEvent(), { newId: nid })
    const afterV2 = reduceAgentEvent(afterV1, artifactEvent({
      artifact_id: 'art_abc', version_seq: 2,
      url: '/static/artifacts/2026-09-30/art_abc_v2.html',
    }), { newId: nid })

    const cards = afterV2.items.filter((it) => it.kind === 'artifact')
    expect(cards).toHaveLength(2)
    expect(cards.map((c) => (c as { versionSeq: number }).versionSeq)).toEqual([1, 2])
  })
})

describe('渲染：MessageList 工件卡', () => {
  it('渲染标题、类型标签、版本号与预览按钮', () => {
    render(<MessageList
      items={[{
        id: 'a1', kind: 'artifact', artifact_id: 'art_abc', artKind: 'animation',
        title: '盐类水解教学动画', versionSeq: 3,
        url: '/static/artifacts/2026-09-30/art_abc_v3.html',
      }]}
      onApproval={() => {}}
    />)
    expect(screen.getByText('盐类水解教学动画')).toBeTruthy()
    expect(screen.getByText(/教学动画 · v3/)).toBeTruthy()
    const btn = screen.getByText('预览') as HTMLButtonElement
    expect(btn).toBeTruthy()
  })

  it('未知类型回落「工件」标签，不白屏', () => {
    render(<MessageList
      items={[{
        id: 'a2', kind: 'artifact', artifact_id: 'art_x', artKind: 'hologram',
        title: '全息投影', versionSeq: 1, url: '/static/artifacts/x.html',
      }]}
      onApproval={() => {}}
    />)
    expect(screen.getByText('全息投影')).toBeTruthy()
    expect(screen.getByText(/工件 · v1/)).toBeTruthy()
  })
})

describe('历史恢复：ChatPage 重建工件卡', () => {
  it('tool data 带 artifact 引用时重建卡片（源码断言，fetch 桩太重不值当）', () => {
    expect(chatPageSrc).toContain('data.artifact && data.artifact.artifact_id')
    expect(chatPageSrc).toContain("kind: 'artifact'")
  })
})
