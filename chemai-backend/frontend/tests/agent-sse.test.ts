/* streamAgent 流收尾分支（E2E E5 回归）：
 * 流被掐断（服务重启/断网）时 Response body 干净关闭但无 [DONE] ——
 * 必须走 onAbnormalEnd（ADR-0016：调用方重拉 history，不伪装正常完成）；
 * 正常 [DONE] / done 事件走 onDone。浏览器 E2E 在 Windows 上造不出
 * 「FIN 无收尾」的 TCP 场景，故在流解析层锁定该契约。 */
import { describe, expect, it } from 'vitest'

import { streamAgent } from '../src/lib/agent-sse'

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) controller.enqueue(encoder.encode(c))
      controller.close()
    },
  })
  return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
}

describe('streamAgent 收尾分支', () => {
  it('流关闭但无 [DONE] → onAbnormalEnd，不触发 onDone', async () => {
    let events = 0
    let done = 0
    let abnormal = 0
    await streamAgent(sseResponse([
      'data: {"type":"text","content":"讲到一半"}\n\n',
      // 服务在这里被掐：连接干净关闭（FIN），没有收尾标记
    ]), {
      onEvent: () => { events++ },
      onDone: () => { done++ },
      onAbnormalEnd: () => { abnormal++ },
    })
    expect(events).toBe(1)
    expect(done).toBe(0)
    expect(abnormal).toBe(1)
  })

  it('[DONE] 收尾 → onDone，不触发 onAbnormalEnd', async () => {
    let done = 0
    let abnormal = 0
    await streamAgent(sseResponse([
      'data: {"type":"text","content":"完整回复"}\n\n',
      'data: [DONE]\n\n',
    ]), {
      onEvent: () => {},
      onDone: () => { done++ },
      onAbnormalEnd: () => { abnormal++ },
    })
    expect(done).toBe(1)
    expect(abnormal).toBe(0)
  })

  it('done 事件收尾 → onDone（协议等价 [DONE]）', async () => {
    let done = 0
    let abnormal = 0
    await streamAgent(sseResponse([
      'data: {"type":"done"}\n\n',
    ]), {
      onEvent: () => {},
      onDone: () => { done++ },
      onAbnormalEnd: () => { abnormal++ },
    })
    expect(done).toBe(1)
    expect(abnormal).toBe(0)
  })
})
