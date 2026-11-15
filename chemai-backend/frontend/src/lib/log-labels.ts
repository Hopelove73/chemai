/* log-labels.ts — 操作日志的中文化（admin-shell-v2 后续修正）。
 *
 * 管理员看到的应该是「重置学生密码 / 学生 张三」，不是 `student_reset_password /
 * user·241901 {"name":"周翔"}`。三张表：动作、目标类型、详情字段。
 * 未登记的动作/字段**原样显示**（新动作不会因为没登记就消失）。
 */

export interface ActionMeta {
  label: string
  /** 该动作通常作用在什么对象上（目标列的中文名） */
  target?: string
}

export const ACTION_META: Record<string, ActionMeta> = {
  import: { label: '数据导入' },
  teacher_application_approve: { label: '通过教师入驻申请', target: '教师申请' },
  teacher_application_reject: { label: '驳回教师入驻申请', target: '教师申请' },
  teacher_approve: { label: '审批通过教师', target: '教师' },
  student_approve: { label: '审批通过学生', target: '学生' },
  student_transfer: { label: '学生调班', target: '学生' },
  student_reset_password: { label: '重置学生密码', target: '学生' },
  student_delete: { label: '删除学生', target: '学生' },
  teacher_assign_classes: { label: '分配任教班级', target: '教师' },
  kb_review_approve: { label: 'KB 审校批准落库', target: '审校包' },
  kb_review_reject: { label: 'KB 审校驳回', target: '审校包' },
  kb_ingest_submit: { label: '触发 KB 摄取', target: '摄取任务' },
  kb_dictionary_sync: { label: '同步 KB 词典', target: 'KB 词典' },
  kb_tag_map_sync: { label: '导入 KB 标签映射', target: 'KB 映射' },
  runtime_setting_update: { label: '修改运行时开关', target: '运行时开关' },
  runtime_setting_reset: { label: '恢复开关默认值', target: '运行时开关' },
  api_key_created: { label: '创建 API 密钥', target: 'API 密钥' },
  api_key_deleted: { label: '删除 API 密钥', target: 'API 密钥' },
  lms_config: { label: '修改 LMS 配置', target: 'LMS 平台' },
  break_glass_start: { label: '发起平台提权', target: '提权会话' },
  break_glass_end: { label: '结束平台提权', target: '提权会话' },
}

export const TARGET_LABELS: Record<string, string> = {
  user: '用户',
  student: '学生',
  teacher: '教师',
  teacher_application: '教师申请',
  class: '班级',
  grade: '年级',
  school: '学校',
  kb_review_package: '审校包',
  kb_job: '摄取任务',
  kb_dictionary: 'KB 词典',
  kb_tag_map: 'KB 映射',
  runtime_setting: '运行时开关',
  integration_api_key: 'API 密钥',
  integration_webhook: 'Webhook',
  break_glass: '提权会话',
  kb_chunk: '知识分块',
  integration_config: '集成配置',
}

export const DETAIL_LABELS: Record<string, string> = {
  name: '姓名',
  from_class: '原班级',
  to_class: '目标班级',
  class_id: '班级',
  class_name: '班级',
  assignments: '任教安排',
  subject: '科目',
  is_class_teacher: '班主任',
  reason: '原因/理由',
  report: '结果',
  old: '原值',
  new: '新值',
  key_id: '密钥编号',
  permissions: '权限',
  module: '教材册',
  teacher_id: '教师编号',
  platform: '平台',
  enabled: '启用',
  connected: '已连接',
  fingerprint: '文件指纹',
}

export function actionLabel(action: string): string {
  return ACTION_META[action]?.label ?? action
}

export function targetLabel(target?: string | null): string {
  if (!target) return '—'
  return TARGET_LABELS[target] ?? target
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (Array.isArray(value)) {
    if (value.length === 0) return '—'
    return value
      .map((v) => {
        if (v && typeof v === 'object') {
          // 任教安排这类对象：取最有辨识度的几列
          const o = v as Record<string, unknown>
          const parts = [o.class_name ?? o.class_id, o.subject, o.is_class_teacher ? '班主任' : '']
            .filter(Boolean)
          return parts.length ? parts.join(' · ') : JSON.stringify(v)
        }
        return String(v)
      })
      .join('；')
  }
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${k}: ${formatValue(v)}`)
      .join('；')
  }
  return String(value)
}

/** 详情 → 可读的键值对（中文键；未知键原样保留）。 */
export function detailEntries(detail?: Record<string, unknown> | null): Array<[string, string]> {
  if (!detail) return []
  return Object.entries(detail).map(([k, v]) => [DETAIL_LABELS[k] ?? k, formatValue(v)])
}
