/* teacher-chat-file-context.test.ts — 教师端每一次请求都要带上文件档位与工作区
 *
 * 后端把「文件访问档位 + 老师自定义工作区」当**每请求**上下文注入
 * （ADR-0020：`set_file_access_level(request.file_access)` / `set_workspace_root(request.workspace)`
 * 在三个入口各调一次）。请求没带 → 回落默认档 `workspace` 与默认目录
 * `~/ChemAI-Workspace`。于是批准重放那一回合把文件写到老师没选的那个文件夹里——
 * 他指定了 `D:\高三化学`，卷子却落在 `~/ChemAI-Workspace`。
 *
 * 所以守的是「每个调用点都带」，而不是 postStream 内部会不会转发——
 * 少一处调用点就少一回合正确的档位，而 TypeScript 看不出来（字段是可选的）。
 */
import ChatPageSource from '../src/pages/teacher/ChatPage.tsx?raw'
import { describe, expect, it } from 'vitest'

const SOURCE = ChatPageSource

/** 取出源码里每处 `postStream(` 的完整实参文本（括号配平，跳过字符串字面量）。 */
function postStreamCalls(src: string): string[] {
  const out: string[] = []
  const marker = 'postStream('
  let from = 0
  for (;;) {
    const start = src.indexOf(marker, from)
    if (start < 0) return out
    let i = start + marker.length
    let depth = 1
    let quote = ''
    while (i < src.length && depth > 0) {
      const ch = src[i]
      if (quote) {
        if (ch === '\\') i += 1
        else if (ch === quote) quote = ''
      } else if (ch === "'" || ch === '"' || ch === '`') {
        quote = ch
      } else if (ch === '(') {
        depth += 1
      } else if (ch === ')') {
        depth -= 1
      }
      i += 1
    }
    out.push(src.slice(start + marker.length, i - 1))
    from = i
  }
}

describe('教师端请求携带文件上下文', () => {
  it('ChatPage 里能找到若干 postStream 调用点', () => {
    // 找不到调用点 = 本守卫被静默架空（改名/挪文件时先红在这里）
    expect(postStreamCalls(SOURCE).length).toBeGreaterThanOrEqual(3)
  })

  it('每一处调用都带 file_access 与 workspace', () => {
    const offenders = postStreamCalls(SOURCE)
      .map((args, n) => ({ n, args }))
      .filter(({ args }) => !/file_access\s*:/.test(args) || !/workspace\s*:/.test(args))
      .map(({ n, args }) => `第 ${n + 1} 处：${args.replace(/\s+/g, ' ').slice(0, 120)}`)
    expect(offenders, '漏带文件上下文的调用点会让那一回合回到默认档/默认目录：\n' + offenders.join('\n')).toEqual([])
  })

  it('带的是组件里的当前值，不是写死的字面量', () => {
    // `file_access: 'workspace'` 这种写法同样能满足上一条，但老师在设置里改档位
    // 后依然无效——必须引用 state。
    for (const args of postStreamCalls(SOURCE)) {
      expect(/file_access\s*:\s*['"]/.test(args), `写死档位：${args.slice(0, 120)}`).toBe(false)
      expect(/workspace\s*:\s*['"]/.test(args), `写死工作区：${args.slice(0, 120)}`).toBe(false)
    }
  })
})
