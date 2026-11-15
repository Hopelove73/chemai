/* 教学技能斜杠指令 + 聊天 @ 引用（能力升级批次 A4，均为前端机制）。
 *
 * 斜杠：`/动画 盐类水解` → 解析成让 agent 调 invoke_teaching_skill 的确定性指令。
 *   agent 侧的技能分发表在 app/services/teaching_skills（ADR-0027）；这里的映射
 *   只是斜杠别名 → 技能名的**展示层**对照，两侧键集合由契约测试钉住。
 * @ 引用：输入框 @ 唤起弹层选附件/工件，选中插入结构化 token（替换裸 upload_id 拼接）。
 */

export interface SlashSkill {
  slash: string
  skill: string
  label: string
}

export const SLASH_SKILLS: SlashSkill[] = [
  { slash: '/动画', skill: 'teaching_animation', label: '教学动画' },
  { slash: '/讲评', skill: 'review_deck', label: '讲评稿' },
  { slash: '/表述', skill: 'expression_training', label: '表述特训' },
]

/** `/动画 盐类水解` → 调技能的确定性指令；不匹配返回 null（原样发送）。 */
export function parseSlashInput(
  text: string,
): { skill: string; label: string; topic: string; prompt: string } | null {
  const trimmed = text.trim()
  if (!trimmed.startsWith('/')) return null
  const spaceIdx = trimmed.indexOf(' ')
  const slash = spaceIdx < 0 ? trimmed : trimmed.slice(0, spaceIdx)
  const topic = spaceIdx < 0 ? '' : trimmed.slice(spaceIdx + 1).trim()
  const hit = SLASH_SKILLS.find((s) => s.slash === slash)
  if (!hit) return null
  const topicPart = topic ? `主题：「${topic}」。` : '主题待用户补充时用合理默认或先完成生成再让用户改。'
  return {
    skill: hit.skill,
    label: hit.label,
    topic,
    prompt: `请调用教学技能工具 invoke_teaching_skill（skill="${hit.skill}"）生成${hit.label}工件。${topicPart}`,
  }
}

export interface RefItem {
  id: string
  kind: 'attachment' | 'artifact'
  /** 展示名：附件文件名 / 工件标题 */
  label: string
  /** 插入输入框的结构化 token（LLM 与既有工具约定都认） */
  token: string
}

/** @ 查询过滤：按 label 前缀/包含匹配（大小写不敏感）。 */
export function filterRefs(query: string, items: RefItem[]): RefItem[] {
  const q = query.trim().toLowerCase()
  if (!q) return items
  return items.filter((it) => it.label.toLowerCase().includes(q))
}

/** 从输入文本取光标前的 @ 片段：`给 @答题卡 画个图` → '答题卡'；无进行中的 @ 返回 null。 */
export function activeAtQuery(text: string, caret: number): string | null {
  const before = text.slice(0, caret)
  const m = before.match(/(?:^|\s)@([^\s@]*)$/)
  return m ? m[1] : null
}

/** 把光标处的 @ 片段替换成引用 token，返回新文本与 token 之后的插入位置。
 * 后面紧跟空格时不重复加（避免双空格），句尾则补一个。 */
export function applyRef(text: string, caret: number, token: string): { text: string; caret: number } {
  const before = text.slice(0, caret)
  const m = before.match(/(?:^|\s)@([^\s@]*)$/)
  if (!m) return { text, caret }
  const start = caret - m[1].length - 1 // @ 也在替换范围内
  const rest = text.slice(caret)
  const inserted = rest.length > 0 && rest[0] === ' ' ? token : `${token} `
  return {
    text: text.slice(0, start) + inserted + rest,
    caret: start + inserted.length,
  }
}
