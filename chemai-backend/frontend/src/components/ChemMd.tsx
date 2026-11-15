/* ChemMd.tsx — 共享化学 Markdown 渲染组件（KaTeX + 图片 + 表格 + SMILES 结构式），
 * AI 气泡与题目内容共用，避免样式类名散落。
 * 行内 [SMILES:...] 标记 → Structure 组件（smiles-drawer 结构式）：
 * 聊天气泡 / 练习题干 / 变式训练三处共用本组件，标记一处支持三处点亮。 */

import { Fragment } from 'react'
import { renderChemMD, splitStructureSegments } from '../lib/chem-markdown'
import { Structure } from './Structure'

const MD_CLASSES = [
  'chem-md',
  'text-[14px] leading-relaxed text-ink',
  // 窄气泡防御（复盘：1469px 表格挤进 275px 气泡把列压成一字宽）：
  // 表格变块级横向滚动；单元格与链接 overflow-wrap:anywhere 让长 URL 可断行
  '[&_table]:block [&_table]:w-full [&_table]:max-w-full [&_table]:overflow-x-auto [&_table]:text-sub [&_table]:border-collapse',
  '[&_th]:text-left [&_th]:font-medium [&_th]:text-ink-muted [&_th]:py-1.5 [&_th]:border-b [&_th]:border-border [&_th]:[overflow-wrap:anywhere]',
  '[&_td]:py-1.5 [&_td]:border-b [&_td]:border-border/60 [&_td]:[overflow-wrap:anywhere] [&_td]:align-top',
  '[&_a]:[overflow-wrap:anywhere]',
  '[&_pre]:bg-surface-muted [&_pre]:rounded-[var(--radius-sm)] [&_pre]:p-3 [&_pre]:overflow-x-auto',
  '[&_code]:font-mono',
  '[&_blockquote]:border-l-2 [&_blockquote]:border-teal [&_blockquote]:pl-3 [&_blockquote]:text-ink-secondary [&_blockquote]:italic',
  '[&_hr]:border-border [&_strong]:font-semibold',
  '[&_h1]:text-lg [&_h2]:text-base [&_h3]:text-[15px] [&_h1],\\[&_h2],\\[&_h3]:font-semibold [&_h1],\\[&_h2],\\[&_h3]:mt-3 [&_h1],\\[&_h2],\\[&_h3]:mb-1',
  '[&_ul]:pl-4 [&_ol]:pl-4 [&_li]:my-0.5',
  '[&_p]:my-1 [&_p]:first:mt-0 [&_p]:last:mb-0',
  '[&_img]:max-w-full [&_img]:rounded-[var(--radius-sm)] [&_img]:my-2 [&_img]:block [&_img]:mx-auto',
].join(' ')

export function ChemMd({ text, className = '' }: { text: string; className?: string }) {
  const segments = splitStructureSegments(text || '')
  return (
    <>
      {segments.map((seg, i) =>
        seg.kind === 'smiles' ? (
          <Structure key={i} smiles={seg.text} />
        ) : seg.text.trim() ? (
          <Fragment key={i}>
            <div
              className={`${MD_CLASSES} ${className}`}
              dangerouslySetInnerHTML={{ __html: renderChemMD(seg.text) }}
            />
          </Fragment>
        ) : null,
      )}
    </>
  )
}
