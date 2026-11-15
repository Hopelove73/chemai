import { useCallback, useMemo, useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { api, can } from '../../lib/api'
import {
  Button, Card, EmptyState, Field, Input, Tabs, useToast,
} from '../../components/ui'
import { useAsyncAction } from '../../components/ui/ActionButton'

/* ── 轻量 CSV 解析（引号/转义/换行 + BOM + 分隔符探测）──────── */

export type Delimiter = ',' | ';' | '\t'

export function detectDelimiter(sample: string): Delimiter {
  const line = sample.split(/\r?\n/)[0] ?? ''
  const counts: Array<[Delimiter, number]> = [
    [',', (line.match(/,/g) ?? []).length],
    [';', (line.match(/;/g) ?? []).length],
    ['\t', (line.match(/\t/g) ?? []).length],
  ]
  counts.sort((a, b) => b[1] - a[1])
  return counts[0][1] > 0 ? counts[0][0] : ','
}

export function parseCsv(text: string, delimiter: Delimiter): string[][] {
  const clean = text.replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const rows: string[][] = []
  let cur: string[] = []
  let field = ''
  let inQuotes = false
  for (let i = 0; i < clean.length; i++) {
    const ch = clean[i]
    if (inQuotes) {
      if (ch === '"') {
        if (clean[i + 1] === '"') { field += '"'; i++ } else { inQuotes = false }
      } else field += ch
    } else if (ch === '"') {
      inQuotes = true
    } else if (ch === delimiter) {
      cur.push(field); field = ''
    } else if (ch === '\n') {
      cur.push(field); field = ''
      if (cur.some((c) => c.trim() !== '')) rows.push(cur)
      cur = []
    } else field += ch
  }
  cur.push(field)
  if (cur.some((c) => c.trim() !== '')) rows.push(cur)
  return rows
}

/* ── 步骤定义（列映射 = data_import 请求模型字段）────────────── */

interface ColumnSpec {
  key: string
  label: string
  required?: boolean
}

interface StepDef {
  key: string
  label: string
  gate: string
  columns: ColumnSpec[]
  /** 行 → 请求载荷片段（提交时组装） */
  toRow: (row: Record<string, string>) => unknown
}

const STEPS: StepDef[] = [
  {
    key: 'roster', label: '① 学籍名册', gate: 'class:create',
    columns: [
      { key: 'class_name', label: '班级名称', required: true },
      { key: 'student_no', label: '学号', required: true },
      { key: 'name', label: '姓名', required: true },
    ],
    toRow: (r) => ({ class_name: r.class_name, student_no: r.student_no, name: r.name }),
  },
  {
    key: 'teaching', label: '② 教学安排', gate: 'teacher:create',
    columns: [
      { key: 'teacher_id', label: '教师ID', required: true },
      { key: 'teacher_name', label: '姓名' },
      { key: 'username', label: '登录名' },
      { key: 'password', label: '初始密码' },
      { key: 'class_name', label: '班级名称', required: true },
      { key: 'subject', label: '科目' },
      { key: 'is_class_teacher', label: '班主任(true/false)' },
    ],
    toRow: (r) => ({
      teacher_id: r.teacher_id, teacher_name: r.teacher_name || undefined,
      username: r.username || undefined, password: r.password || undefined,
      class_name: r.class_name, subject: r.subject || '化学',
      is_class_teacher: (r.is_class_teacher || '').toLowerCase() === 'true',
    }),
  },
  {
    key: 'exam', label: '③ 考试成绩', gate: 'exam:create',
    columns: [
      { key: 'student_no', label: '学号', required: true },
      { key: 'total', label: '总分' },
    ],
    toRow: (r) => ({ student_no: r.student_no, total: r.total ? Number(r.total) : undefined }),
  },
]

interface ParsedRow {
  raw: Record<string, string>
  errors: string[]
}

/* ── 单步面板：解析 → 预览校验 → 提交 → 结果 ─────────────────── */

function StepPanel({ step }: { step: StepDef }) {
  const toast = useToast()
  const [pasted, setPasted] = useState('')
  const [delimiter, setDelimiter] = useState<Delimiter>(',')
  const [delimiterTouched, setDelimiterTouched] = useState(false)
  const [parsed, setParsed] = useState<ParsedRow[] | null>(null)
  const [, setHeader] = useState<string[]>([])
  const [dropBad, setDropBad] = useState(false)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  /* 成绩步骤的考试元数据表单 */
  const [examName, setExamName] = useState('')
  const [examClass, setExamClass] = useState('')
  const [examDate, setExamDate] = useState('')
  const [gradeName, setGradeName] = useState('')
  const [gradeYear, setGradeYear] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const parse = useCallback((text: string, delim: Delimiter) => {
    if (!text.trim()) { setParsed(null); return }
    const rows = parseCsv(text, delim)
    if (rows.length === 0) { setParsed(null); return }
    const head = rows[0].map((h) => h.trim())
    setHeader(head)
    const missing = step.columns.filter((c) => c.required && !head.includes(c.key))
    setParsed(rows.slice(1).map((cells) => {
      const raw: Record<string, string> = {}
      head.forEach((h, i) => { raw[h] = (cells[i] ?? '').trim() })
      const errors: string[] = []
      for (const m of missing) errors.push(`缺列「${m.label}」`)
      for (const c of step.columns) {
        if (c.required && !raw[c.key]) errors.push(`「${c.label}」为空`)
      }
      return { raw, errors }
    }))
  }, [step])

  const onPaste = (text: string) => {
    setPasted(text)
    if (!delimiterTouched) setDelimiter(detectDelimiter(text))
    parse(text, delimiterTouched ? delimiter : detectDelimiter(text))
  }

  const onFile = async (file: File) => {
    const text = await file.text()
    onPaste(text)
  }

  const good = useMemo(() => (parsed ?? []).filter((r) => r.errors.length === 0), [parsed])
  const bad = useMemo(() => (parsed ?? []).filter((r) => r.errors.length > 0), [parsed])
  const missingColumn = (parsed ?? []).some((r) => r.errors.some((e) => e.startsWith('缺列')))
  const submitRows = dropBad ? good : (parsed ?? []).filter((r) => r.errors.length === 0)

  const submit = useAsyncAction(async () => {
    if (submitRows.length === 0) { toast.error('没有可提交的行'); return }
    try {
      let r: { success: boolean; message?: string } & Record<string, unknown>
      if (step.key === 'roster') {
        r = await api.post('/api/import/roster', {
          rows: submitRows.map((p) => step.toRow(p.raw)),
          grade_name: gradeName || undefined, grade_year: gradeYear ? Number(gradeYear) : undefined,
        })
      } else if (step.key === 'teaching') {
        r = await api.post('/api/import/teaching-assignments', { rows: submitRows.map((p) => step.toRow(p.raw)) })
      } else {
        r = await api.post('/api/import/exam', {
          name: examName.trim() || undefined, class_name: examClass.trim() || undefined,
          exam_date: examDate || undefined, grade_name: gradeName || undefined,
          grade_year: gradeYear ? Number(gradeYear) : undefined,
          scores: submitRows.map((p) => step.toRow(p.raw)),
        })
      }
      if (!r.success) { toast.error(r.message || '导入失败'); return }
      toast.success('导入完成')
      setResult(r)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : '导入失败')
    }
  })

  return (
    <div className="space-y-3">
      {/* 粘贴 / 上传（P-12 upload-zone） */}
      <div
        className="rounded-[var(--radius-md)] border border-dashed border-border-strong p-4 hover:border-primary/40 transition-colors cursor-pointer"
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden"
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) void onFile(f) }}
        />
        <div className="flex items-center gap-2 text-sub text-ink-secondary">
          <Upload size={16} />
          点击选择 CSV 文件，或直接在下方粘贴表格文本（首行为表头，分隔符自动探测）
        </div>
        <textarea
          className="mt-3 w-full h-28 rounded-[var(--radius-sm)] border border-border bg-surface p-2 font-mono text-[12px] focus-visible:outline-2 focus-visible:outline-primary/40"
          placeholder={`${step.columns.map((c) => c.label).join(', ')}\n…`}
          value={pasted}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => onPaste(e.target.value)}
        />
        <div className="mt-2 flex items-center gap-2 text-caption text-ink-muted">
          分隔符
          <select
            className="rounded-[var(--radius-sm)] border border-border bg-surface px-1.5 py-0.5"
            value={delimiter}
            onChange={(e) => { setDelimiterTouched(true); const d = e.target.value as Delimiter; setDelimiter(d); parse(pasted, d) }}
          >
            <option value=",">,</option>
            <option value=";">;</option>
            <option value={String.fromCharCode(9)}>Tab</option>
          </select>
          {parsed && <span>共 {parsed.length} 行</span>}
        </div>
      </div>

      {/* 成绩步骤的考试元数据 */}
      {step.key === 'exam' && parsed && (
        <Card title="考试信息">
          <div className="grid grid-cols-4 gap-3 max-[860px]:grid-cols-1">
            <Field label="考试名称"><Input value={examName} onChange={(e) => setExamName(e.target.value)} /></Field>
            <Field label="班级名称 *"><Input value={examClass} onChange={(e) => setExamClass(e.target.value)} /></Field>
            <Field label="考试日期"><Input value={examDate} onChange={(e) => setExamDate(e.target.value)} placeholder="2026-04-01" /></Field>
            <Field label="年级（可选）">
              <div className="flex gap-1.5">
                <Input value={gradeName} onChange={(e) => setGradeName(e.target.value)} placeholder="高一" />
                <Input value={gradeYear} onChange={(e) => setGradeYear(e.target.value)} placeholder="2026" />
              </div>
            </Field>
          </div>
        </Card>
      )}

      {/* 预览校验 */}
      {parsed && (
        <Card title={`预览（${good.length} 行可提交，${bad.length} 行有问题）`} flush>
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-surface">
                <tr className="text-left text-ink-secondary border-b border-border">
                  <th className="px-2 py-1.5 font-medium">#</th>
                  {step.columns.map((c) => <th key={c.key} className="px-2 py-1.5 font-medium">{c.label}</th>)}
                  <th className="px-2 py-1.5 font-medium">校验</th>
                </tr>
              </thead>
              <tbody>
                {parsed.map((r, i) => (
                  <tr key={i} className={r.errors.length ? 'bg-warning-soft/40' : ''}>
                    <td className="px-2 py-1 text-ink-muted">{i + 1}</td>
                    {step.columns.map((c) => (
                      <td key={c.key} className={`px-2 py-1 font-mono ${c.required && !r.raw[c.key] ? 'text-error' : ''}`}>
                        {r.raw[c.key] || '—'}
                      </td>
                    ))}
                    <td className="px-2 py-1 text-error">{r.errors.join('；') || '✓'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {parsed && parsed.length > 0 && (
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 text-sub text-ink-secondary cursor-pointer">
            <input type="checkbox" checked={dropBad} onChange={(e) => setDropBad(e.target.checked)} />
            剔除问题行，只提交合格行（{good.length} 行）
          </label>
          <Button
            onClick={() => { void submit.run() }}
            disabled={missingColumn || submitRows.length === 0 || submit.state === 'busy' || (step.key === 'exam' && !examClass.trim())}
          >
            {submit.state === 'busy' ? '导入中…' : `提交 ${submitRows.length} 行`}
          </Button>
          {missingColumn && <span className="text-caption text-error">缺少必需列，无法提交</span>}
        </div>
      )}

      {/* 结果摘要（幂等跳过如实标注） */}
      {result && (
        <Card title="导入结果">
          <pre className="font-mono text-[12px] whitespace-pre-wrap text-ink-secondary m-0">
            {JSON.stringify(result, null, 2)}
          </pre>
        </Card>
      )}
    </div>
  )
}

/* ── 向导骨架：按能力显隐步骤 ────────────────────────────── */

export function ImportWizard() {
  const steps = STEPS.filter((s) => can(s.gate))
  const [active, setActive] = useState(steps[0]?.key ?? '')

  if (steps.length === 0) {
    return <EmptyState title="无导入权限" description="当前账号没有任何数据导入能力（class:create / teacher:create / exam:create）。" />
  }

  return (
    <div className="space-y-4">
      <Card>
        <div className="text-sub text-ink-secondary">
          分步导入学校数据：名册（按学号去重，重复跳过）→ 教学安排（教师建档 + 任教关系）→ 考试成绩（同班同名同日期幂等）。
          每一步的列名要与模板一致；大批量历史迁移仍可用脚本/API。
        </div>
      </Card>
      <Tabs items={steps.map((s) => ({ key: s.key, label: s.label }))} active={active} onChange={setActive} />
      {steps.filter((s) => s.key === active).map((s) => <StepPanel key={s.key} step={s} />)}
    </div>
  )
}

/** 给未启用步骤的占位统计（步骤被 can() 过滤后 caller 用于空态判断） */
export function hasImportSteps(): boolean {
  return STEPS.some((s) => can(s.gate))
}
