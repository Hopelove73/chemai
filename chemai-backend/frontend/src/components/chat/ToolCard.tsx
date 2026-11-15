import { useEffect, useState, type ReactNode } from 'react'
import {
  Search,
  FileText,
  Activity,
  BookOpen,
  FlaskConical,
  Scale,
  ClipboardList,
  Upload,
  Send,
  Globe,
  Wrench,
  ChevronDown,
  ChevronRight,
  LayoutGrid,
  UserRound,
  FolderOpen,
  Pencil,
  Terminal,
  ListTree,
  ListChecks,
  ShieldCheck,
  FilePen,
  MousePointerClick,
  Keyboard,
  Camera,
} from 'lucide-react'
import { Badge } from '../ui'
import { ToolResultView } from './tool-renderers'
import type { ToolStatus } from '../../lib/agent-sse'

/** 全部工具的展示元数据（DSH 式工作卡：图标 + 中文动作说明） */
export const TOOL_META: Record<string, { label: string; icon: ReactNode }> = {
  search_exam_bank: { label: '搜索真题', icon: <Search size={14} /> },
  generate_questions: { label: 'AI 出题', icon: <FileText size={14} /> },
  diagnose_barrier: { label: '障碍诊断', icon: <Activity size={14} /> },
  chemistry_tutor: { label: '智能辅导', icon: <BookOpen size={14} /> },
  simulate_experiment: { label: '模拟实验', icon: <FlaskConical size={14} /> },
  balance_equation: { label: '配平审核', icon: <Scale size={14} /> },
  weekly_report: { label: '生成周报', icon: <ClipboardList size={14} /> },
  import_exam_paper: { label: '导入试卷', icon: <Upload size={14} /> },
  assign_adaptive_practice: { label: '布置练习', icon: <Send size={14} /> },
  web_search: { label: '联网搜索', icon: <Globe size={14} /> },
  send_class_notification: { label: '发送班级通知', icon: <Send size={14} /> },
  /* 面板类工具（component 内联面板配套） */
  show_exam_workbench: { label: '打开出题工作台', icon: <LayoutGrid size={14} /> },
  show_diagnosis: { label: '展示学情诊断', icon: <Activity size={14} /> },
  show_students: { label: '查找学生', icon: <UserRound size={14} /> },
  /* 文件与命令（四件套） */
  read: { label: '读取文件', icon: <FileText size={14} /> },
  write: { label: '写入文件', icon: <FilePen size={14} /> },
  edit: { label: '编辑文件', icon: <Pencil size={14} /> },
  find: { label: '查找文件', icon: <FolderOpen size={14} /> },
  grep: { label: '搜索内容', icon: <ListTree size={14} /> },
  bash: { label: '执行命令', icon: <Terminal size={14} /> },
  plan_task: { label: '任务规划', icon: <ListChecks size={14} /> },
  request_approval: { label: '请求批准', icon: <ShieldCheck size={14} /> },
  /* 题库 */
  save_to_bank: { label: '保存题库', icon: <FileText size={14} /> },
  list_banks: { label: '题库列表', icon: <ListTree size={14} /> },
  delete_bank: { label: '删除题库', icon: <ListChecks size={14} /> },
  /* 学情记忆 / 学习计划 / 家长报告 */
  memory_student_get: { label: '查询学生记忆', icon: <BookOpen size={14} /> },
  memory_teacher_get: { label: '查询教师偏好', icon: <BookOpen size={14} /> },
  generate_learning_plan: { label: '生成学习计划', icon: <ClipboardList size={14} /> },
  send_learning_plan: { label: '发送学习计划', icon: <Send size={14} /> },
  generate_parent_report: { label: '生成家长报告', icon: <ClipboardList size={14} /> },
  send_report_to_parent: { label: '发送家长报告', icon: <Send size={14} /> },
  /* 辅导工具 */
  ionic_equation_tutor: { label: '离子方程式辅导', icon: <BookOpen size={14} /> },
  stoichiometry_tutor: { label: '化学计量辅导', icon: <BookOpen size={14} /> },
  redox_tutor: { label: '氧化还原辅导', icon: <BookOpen size={14} /> },
  equilibrium_tutor: { label: '化学平衡辅导', icon: <BookOpen size={14} /> },
  periodic_law_tutor: { label: '元素周期律辅导', icon: <BookOpen size={14} /> },
  organic_tutor: { label: '有机化学辅导', icon: <BookOpen size={14} /> },
  /* OCR / 批改 */
  query_ocr_progress: { label: '查询识别进度', icon: <Activity size={14} /> },
  grade_answer_sheets: { label: '批改答题卡', icon: <ClipboardList size={14} /> },
  save_grading_results: { label: '保存批改结果', icon: <FileText size={14} /> },
  query_batch_grades: { label: '查询批次成绩', icon: <ListTree size={14} /> },
  list_teacher_exams: { label: '我的考试列表', icon: <ListTree size={14} /> },
  /* 文档解析 */
  parse_chemistry_document: { label: '解析化学文档', icon: <FileText size={14} /> },
  import_question_bank: { label: '导入题库', icon: <Upload size={14} /> },
  check_parser_services: { label: '检查解析服务', icon: <ShieldCheck size={14} /> },

  /* 批改队列 / 任务进度 / 计划修订（2026-10-05 补齐：此前裸英文名回退） */
  query_grading_queue: { label: '待确认队列', icon: <ListTree size={14} /> },
  confirm_exam_grades: { label: '确认考试成绩', icon: <ShieldCheck size={14} /> },
  query_assignment_progress: { label: '布置进度查询', icon: <ListChecks size={14} /> },
  revise_learning_plan: { label: '修订学习计划', icon: <Pencil size={14} /> },
  invoke_teaching_skill: { label: '生成教学工件', icon: <FlaskConical size={14} /> },
  invoke_learning_skill: { label: '学习技能', icon: <BookOpen size={14} /> },
  decide_plan_draft: { label: '计划确认', icon: <ShieldCheck size={14} /> },
  share_artifact: { label: '分享工件', icon: <Send size={14} /> },

  /* 知识地图边治理 */
  propose_knowledge_edges: { label: '提议知识边', icon: <ListTree size={14} /> },
  accept_knowledge_edges: { label: '采纳知识边', icon: <ShieldCheck size={14} /> },
  disable_knowledge_edge: { label: '停用知识边', icon: <ListChecks size={14} /> },

  /* 家长端七件（家长会话渲染工具卡时同样走本表） */
  parent_learning_plan: { label: '查看学习计划', icon: <ClipboardList size={14} /> },
  parent_mastery_levels: { label: '掌握度查询', icon: <Activity size={14} /> },
  parent_barrier_distribution: { label: '障碍分布查询', icon: <Activity size={14} /> },
  parent_class_comparison: { label: '班级对比查询', icon: <ListTree size={14} /> },
  parent_weekly_summary: { label: '本周学情摘要', icon: <ClipboardList size={14} /> },
  parent_artifact_log: { label: '工件记录查询', icon: <FileText size={14} /> },
  parent_assign_task: { label: '派学习任务', icon: <Send size={14} /> },
  parent_send_task: { label: '发送学习任务', icon: <Send size={14} /> },

  /* 浏览器工具（2026-08 补齐：避免裸英文名回退） */
  browse_navigate: { label: '网页导航', icon: <Globe size={14} /> },
  browse_read: { label: '读取网页', icon: <FileText size={14} /> },
  browse_click: { label: '点击页面元素', icon: <MousePointerClick size={14} /> },
  browse_input: { label: '页面输入文本', icon: <Keyboard size={14} /> },
  browse_screenshot: { label: '页面截图', icon: <Camera size={14} /> },
}

export function toolLabel(tool: string): string {
  return TOOL_META[tool]?.label ?? tool
}

export function toolIcon(tool: string): ReactNode {
  return TOOL_META[tool]?.icon ?? <Wrench size={14} />
}

export interface ToolCardData {
  tool: string
  status: ToolStatus
  data?: unknown
  error?: string
}

/** 这些 error 码的共同点：**这次调用没有执行**，稍后重试即可 —— 不是失败。
 *  - waiting_for_gate        确认门开着，等老师决定
 *  - plan_dependency_unmet   计划前置步还没做完（载荷带 recoverable: true）
 *  - no_draft_to_send       没有可发送的草稿（后端也按非失败处理计划步）
 * 染成红色会让老师以为出事了，实际什么都没发生。 */
const DEFERRED_CODES = ['waiting_for_gate', 'plan_dependency_unmet', 'no_draft_to_send']

/** 工具调用内联卡片：图标 + 名称 + 状态 + 结果（完成/失败自动展开，与旧版即时可见一致） */
export function ToolCard({ card }: { card: ToolCardData }) {
  const [open, setOpen] = useState(false)
  const result = card.data && typeof card.data === 'object' ? card.data as Record<string, unknown> : undefined
  const errCode = typeof result?.error === 'string' ? result.error as string : card.error
  // 历史记录和兼容事件可能仍带 error 状态；没有执行的调用展示为暂缓。
  // dedup_skipped：同一调用重复了一次，护栏直接跳过 —— 同样什么都没执行。
  const paused = card.status === 'paused' || result?.gate_open_blocked === true
    || result?.dedup_skipped === true
    || (errCode !== undefined && DEFERRED_CODES.includes(errCode))
  const failed = card.status === 'error' && !paused
  const hasResult = (card.status === 'done' || card.status === 'error' || paused) && card.data !== undefined

  useEffect(() => {
    // 结果到达自动展开（旧版行为：工具结果直接可见；2026-08：失败结果同样可见）
    if ((card.status === 'done' || card.status === 'error' || paused) && card.data !== undefined) setOpen(true)
  }, [card.status, card.data, paused])

  return (
    <div
      className={`shrink-0 self-start max-w-[92%] bg-surface border rounded-[var(--radius-md)] shadow-[var(--shadow-sm)] overflow-hidden animate-fade-in-up ${
        failed
          ? 'border-error/40'
          : card.status === 'running' && !paused
            ? 'border-teal/30'
            : 'border-border'
      }`}
    >
      <button
        className="w-full flex items-center gap-2.5 px-3.5 py-2.5 text-left hover:bg-surface-muted/60 transition-colors cursor-pointer"
        onClick={() => setOpen((v) => !v)}
      >
        <span
          className={`w-7 h-7 rounded-[var(--radius-sm)] flex items-center justify-center shrink-0 ${
            failed ? 'bg-error-soft text-error' : paused ? 'bg-surface-muted text-ink-secondary' : 'bg-teal-soft text-teal'
          }`}
        >
          {toolIcon(card.tool)}
        </span>
        <span className="flex-1 text-sub font-medium text-ink">{toolLabel(card.tool)}</span>
        {paused ? (
          <Badge status="idle" label={
            result?.dedup_skipped === true ? '已跳过 · 重复调用'
              : errCode === 'plan_dependency_unmet' ? '已暂缓 · 前置步骤未完成'
              : errCode === 'no_draft_to_send' ? '已暂缓 · 无可发送草稿'
                : '已暂缓 · 等待确认'
          } />
        ) : card.status === 'running' ? (
          <Badge status="running" label="执行中" />
        ) : failed ? (
          <Badge status="blocked" label="失败" />
        ) : (
          <Badge status="pass" label="完成" />
        )}
        {hasResult && (open ? <ChevronDown size={14} className="text-ink-muted" /> : <ChevronRight size={14} className="text-ink-muted" />)}
      </button>
      {hasResult && open && (
        <div className="px-3.5 pb-3.5 pt-1 border-t border-border/60">
          {paused ? (
            <p className="text-sm text-ink-secondary m-0">{String(result?.ui_label ?? '等待确认后任务会自动继续。')}</p>
          ) : (
            <ToolResultView tool={card.tool} data={card.data} />
          )}
        </div>
      )}
      {failed && card.error && (
        <div className="px-3.5 pb-3 text-xs text-error">{card.error}</div>
      )}
    </div>
  )
}
