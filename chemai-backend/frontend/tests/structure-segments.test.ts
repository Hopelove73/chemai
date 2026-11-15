/* [SMILES:...] 行内标记分段（chem-markdown.splitStructureSegments）。
 * 标记是全项目唯一结构约定（出题 prompt 强制 / 服务端 structure_render 消费），
 * 前端分段补齐聊天/变式/题干路径——这里钉语法边界与降级行为。 */
import { describe, expect, it } from 'vitest'
import { splitStructureSegments } from '../src/lib/chem-markdown'

describe('splitStructureSegments', () => {
  it('纯文本 → 单 md 段', () => {
    expect(splitStructureSegments('盐类水解的本质是什么')).toEqual([
      { kind: 'md', text: '盐类水解的本质是什么' },
    ])
  })

  it('空文本 → 单空 md 段（组件层跳过）', () => {
    expect(splitStructureSegments('')).toEqual([{ kind: 'md', text: '' }])
  })

  it('行内标记 → md / smiles / md 三段，SMILES 保留原文', () => {
    const segs = splitStructureSegments('乙醇的结构：[SMILES:CCO]，看羟基。')
    expect(segs).toEqual([
      { kind: 'md', text: '乙醇的结构：' },
      { kind: 'smiles', text: 'CCO' },
      { kind: 'md', text: '，看羟基。' },
    ])
  })

  it('多个标记按序分段', () => {
    const segs = splitStructureSegments('[SMILES:C1=CC=CC=C1] 和 [SMILES:CCO]')
    expect(segs.map((s) => s.kind)).toEqual(['smiles', 'md', 'smiles'])
    expect(segs[0].text).toBe('C1=CC=CC=C1')
    expect(segs[2].text).toBe('CCO')
  })

  it('括号原子（[N+] 等）不被截断——与服务端正则同语法', () => {
    const segs = splitStructureSegments('硝基苯：[SMILES:[O-][N+](=O)c1ccccc1]')
    expect(segs[1].text).toBe('[O-][N+](=O)c1ccccc1')
  })

  it('无标记的方括号文本不受影响', () => {
    const segs = splitStructureSegments('参考[资料]第 3 页')
    expect(segs).toEqual([{ kind: 'md', text: '参考[资料]第 3 页' }])
  })

  it('标记trim首尾空白', () => {
    expect(splitStructureSegments('[SMILES:  CCO  ]')[0].text).toBe('CCO')
  })
})
