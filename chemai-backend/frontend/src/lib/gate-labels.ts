/* 门终态小票的标签（「已批准执行：布置练习」里冒号后面那段）。
 *
 * 两个消费方共用同一份，别再各抄一遍：
 *  - ChatPage 历史恢复（closed_gates 账本行 → 终态小票）
 *  - ApprovalCard 活卡翻面（点击批准/放弃的乐观更新）
 * 8 步长链实测（2026-10-26）：布尔门文案是通用话术（「此操作需要老师确认…」），
 * 不带工具名的话三扇门的小票就是一摞无法区分的同文。 */

/** 布尔门短标签：通用门文案在终态小票上没有区分度。 */
export const SHORT_TOOL_LABELS: Record<string, string> = {
  assign_adaptive_practice: '布置练习',
  delete_bank: '删除题库',
  share_artifact: '分享工件',
  parent_send_task: '家长派任务',
  send_learning_plan: '发送学习计划',
  send_report_to_parent: '同步家长报告',
}

/** 终态小票标签：草稿门文案截到「等待（老师）确认」为止得到「学习计划（第 1 版）」；
 *  布尔门文案是通用话术没有区分度，回退工具短标签，再回退工具名。
 *  `toolName` 缺席（旧后端/旧事件）时行为与只传文案一致。 */
export function gateChipLabel(text: string | undefined, toolName?: string): string {
  const label = (text || '').split(/等待(?:老师)?确认/)[0].trim()
  if (label && label !== '此操作需要老师确认后执行'
      && label !== '此操作需要老师确认：请确认是否执行') {
    return label
  }
  return SHORT_TOOL_LABELS[toolName || ''] || toolName || '这项操作'
}
