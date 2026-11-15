/* log-labels.test.ts — 操作日志可读化（管理员不该看到 student_reset_password 这种串）。 */
import { describe, expect, it } from 'vitest'
import { actionLabel, detailEntries, targetLabel } from '../src/lib/log-labels'

describe('actionLabel', () => {
  it('已知动作给中文', () => {
    expect(actionLabel('student_reset_password')).toBe('重置学生密码')
    expect(actionLabel('teacher_application_approve')).toBe('通过教师入驻申请')
  })
  it('未知动作回落原始值（不吞掉新动作）', () => {
    expect(actionLabel('brand_new_action')).toBe('brand_new_action')
  })
})

describe('targetLabel', () => {
  it('目标类型给中文；缺失给 —', () => {
    expect(targetLabel('student')).toBe('学生')
    expect(targetLabel(undefined)).toBe('—')
    expect(targetLabel('weird')).toBe('weird')
  })
})

describe('detailEntries', () => {
  it('按字段顺序给中文键', () => {
    const out = detailEntries({ name: '张三', from_class: '2419班', to_class: '2420班' })
    expect(out).toEqual([
      ['姓名', '张三'],
      ['原班级', '2419班'],
      ['目标班级', '2420班'],
    ])
  })

  it('数组与对象转成可读文本，而不是 [object Object]', () => {
    const out = detailEntries({
      assignments: [{ class_id: 'c1', subject: '化学' }],
      report: { edges_proposed: 3 },
      reason: null,
    })
    expect(out).toEqual([
      ['任教安排', 'c1 · 化学'],
      ['结果', 'edges_proposed: 3'],
      ['原因/理由', '—'],
    ])
  })

  it('空/缺省返回空数组', () => {
    expect(detailEntries(undefined)).toEqual([])
    expect(detailEntries({})).toEqual([])
  })

  it('未知字段保留原始键名（不隐藏信息）', () => {
    expect(detailEntries({ foo_bar: 1 })).toEqual([['foo_bar', '1']])
  })
})
