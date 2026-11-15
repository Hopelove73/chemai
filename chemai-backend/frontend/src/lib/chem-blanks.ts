/* chem-blanks.ts — 多空答案的前端拆分（S3 / REQ-WBR-001 逐空对比展示）。
 * 与后端 app.models.database.split_answer_blanks 同约定：
 * 多空 = `;`/`；` 分隔字符串或 JSON 数组；单空 = 原串。 */

export function splitAnswerBlanks(answer: unknown): string[] {
  if (answer === null || answer === undefined) return ['']
  if (Array.isArray(answer)) return (answer as unknown[]).map(String)
  const s = String(answer)
  try {
    const parsed = JSON.parse(s)
    if (Array.isArray(parsed)) return (parsed as unknown[]).map(String)
  } catch {
    /* 非 JSON，按分隔符走 */
  }
  const parts = s.split(/[;；]/).map((p) => p.trim())
  return parts.length > 1 ? parts : [s.trim()]
}
