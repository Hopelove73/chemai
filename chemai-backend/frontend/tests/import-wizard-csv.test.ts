/* import-wizard-csv.test.ts — school-init-wizard 任务 2.1：CSV 解析纯函数。 */
import { describe, expect, it } from 'vitest'
import { detectDelimiter, parseCsv } from '../src/pages/admin/ImportWizard'

describe('detectDelimiter', () => {
  it('逗号最多 → 逗号', () => {
    expect(detectDelimiter('a,b,c\n1,2,3')).toBe(',')
  })
  it('分号 → 分号', () => {
    expect(detectDelimiter('a;b;c\n1;2;3')).toBe(';')
  })
  it('Tab → Tab', () => {
    expect(detectDelimiter('a\tb\tc\n1\t2\t3')).toBe('\t')
  })
  it('无分隔符 → 缺省逗号', () => {
    expect(detectDelimiter('abc')).toBe(',')
  })
})

describe('parseCsv', () => {
  it('基本行列', () => {
    expect(parseCsv('a,b\n1,2\n3,4', ',')).toEqual([['a', 'b'], ['1', '2'], ['3', '4']])
  })
  it('引号内的逗号不切列', () => {
    expect(parseCsv('name,note\n"张,三","含,逗号"', ',')).toEqual([
      ['name', 'note'], ['张,三', '含,逗号'],
    ])
  })
  it('引号内的换行不切行 + 转义引号', () => {
    expect(parseCsv('a,b\n"多\n行","含""引号"""', ',')).toEqual([
      ['a', 'b'], ['多\n行', '含"引号"'],
    ])
  })
  it('BOM 剥离 + CRLF', () => {
    expect(parseCsv('\uFEFFA,B\r\n1,2', ',')).toEqual([['A', 'B'], ['1', '2']])
  })
  it('空行丢弃', () => {
    expect(parseCsv('a,b\n\n1,2\n\n', ',')).toEqual([['a', 'b'], ['1', '2']])
  })
})
