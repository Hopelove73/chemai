/* A4：斜杠指令与 @ 引用纯函数（teaching-skills.ts）。
 *
 * 这些是输入框行为的大脑，钉死：
 *   1. /动画 /讲评 解析成调技能的确定性指令（含主题引号包裹）；非斜杠原样放行；
 *   2. @ 查询的过滤与 token 插入（替换 @ 片段、保留光标位置）。
 */
import { describe, expect, it } from 'vitest'
import {
  parseSlashInput, activeAtQuery, applyRef, filterRefs, SLASH_SKILLS,
  type RefItem,
} from '../src/lib/teaching-skills'

describe('斜杠指令解析', () => {
  it('/动画 带主题 → 调技能的确定性指令，主题引号包裹', () => {
    const r = parseSlashInput('/动画 盐类水解')
    expect(r).not.toBeNull()
    expect(r!.skill).toBe('teaching_animation')
    expect(r!.prompt).toContain('invoke_teaching_skill')
    expect(r!.prompt).toContain('skill="teaching_animation"')
    expect(r!.prompt).toContain('「盐类水解」')
  })

  it('/讲评 无主题 → 指令仍成立（agent 侧有默认/补齐约定）', () => {
    const r = parseSlashInput('/讲评')
    expect(r!.skill).toBe('review_deck')
    expect(r!.topic).toBe('')
  })

  it('非斜杠/未注册斜杠 → null（原样发送）', () => {
    expect(parseSlashInput('什么是勒夏特列原理')).toBeNull()
    expect(parseSlashInput('/ppt 做个课件')).toBeNull()
  })

  it('/表述 无主题 → 指令成立（scene/学生作答缺参由 agent 侧拦截并向用户追问）', () => {
    const r = parseSlashInput('/表述')
    expect(r!.skill).toBe('expression_training')
    expect(r!.topic).toBe('')
  })

  it('斜杠映射与后端注册表的技能名对得上（别名表仅是展示层）', () => {
    // tsc 无 node 类型，读文件走 vitest 的 ?raw（同 artifact-card.test 的 ChatPage 断言）
    // —— 但根外文件被 vite fs.allow 挡，这里退化为字面量对照 + 后端契约测试互钉
    // （tests/test_teaching_skills.py 已断言后端三个技能名）。
    expect(SLASH_SKILLS.map((s) => s.skill).sort())
      .toEqual(['expression_training', 'review_deck', 'teaching_animation'])
  })
})

describe('@ 引用', () => {
  const items: RefItem[] = [
    { id: 'up-1', kind: 'attachment', label: '答题卡.png', token: '[upload_id=up-1]' },
    { id: 'art-1', kind: 'artifact', label: '盐类水解动画', token: '[artifact_id=art-1]' },
  ]

  it('光标前有 @ 片段时返回查询词；没有 @ 返回 null', () => {
    expect(activeAtQuery('给 @答题 画图', 5)).toBe('答题')   // 紧跟片段后
    expect(activeAtQuery('给 @ 画图', 3)).toBe('')
    expect(activeAtQuery('没有引用', 4)).toBeNull()
    // @ 片段已被空格闭合（光标在句尾）→ 不算进行中
    expect(activeAtQuery('给 @答题 画图', 8)).toBeNull()
  })

  it('过滤按 label 包含匹配', () => {
    expect(filterRefs('答题', items)).toHaveLength(1)
    expect(filterRefs('', items)).toHaveLength(2)
    expect(filterRefs('动画', items).map((i) => i.id)).toEqual(['art-1'])
  })

  it('插入 token 替换整个 @ 片段并返回新光标位', () => {
    // caret=5 = '给 @答题' 之后；后面已有空格 → 不重复加
    const r = applyRef('给 @答题 画个图', 5, '[upload_id=up-1]')
    expect(r.text).toBe('给 [upload_id=up-1] 画个图')
    expect(r.caret).toBe(18)  // 紧跟 token 之后（后面是已有空格，不重复加）
    // 句尾插入补一个尾随空格
    const tail = applyRef('引用 @动画', 6, '[artifact_id=art-1]')
    expect(tail.text).toBe('引用 [artifact_id=art-1] ')
  })
})
