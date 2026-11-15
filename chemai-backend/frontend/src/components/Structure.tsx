/* Structure.tsx — SMILES → 结构式 SVG（smiles-drawer 2.4，MIT，开源现成能力）。
 * 消费行内 [SMILES:...] 标记（全项目唯一结构约定：出题 prompt 强制输出、
 * 服务端 structure_render 用 RDKit+教材白名单守正式题库；本组件补齐
 * 聊天/变式/题干路径的即时渲染）。
 * 主题跟随作用域的 color-scheme（深浅由主题 token 决定，不再按端名硬判）；解析失败回退等宽
 * SMILES 原文——诚实降级，不给空白块。 */

import { useEffect, useRef, useState } from 'react'
import SmilesDrawer from 'smiles-drawer'

type SmiDrawerInstance = InstanceType<typeof SmilesDrawer.SmiDrawer>

let sharedDrawer: SmiDrawerInstance | null = null

function getDrawer(): SmiDrawerInstance {
  if (!sharedDrawer) {
    sharedDrawer = new SmilesDrawer.SmiDrawer({
      width: 280,
      height: 190,
      padding: 10,
      bondThickness: 1.2,
    })
  }
  return sharedDrawer
}

export function Structure({ smiles, className = '' }: { smiles: string; className?: string }) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    setFailed(false)
    /* 主题判据不能再用「是不是学生端」——学生端 2026-10 已改浅色，但这里仍按端名
       传给 smiles-drawer 'dark'，结构式被画成浅色线条压在白色卡片上 = 一片空白
       （实测：svg 存在、data-smiles 正确，就是看不见）。
       改成读作用域计算出的 color-scheme：谁定深浅谁说话。 */
    const scheme = getComputedStyle(svg).colorScheme || ''
    const theme = scheme.includes('dark') ? 'dark' : 'light'
    try {
      svg.innerHTML = ''
      getDrawer().draw(smiles, svg, theme, undefined, () => setFailed(true))
    } catch {
      setFailed(true)
    }
  }, [smiles])

  if (failed) {
    return (
      <span className={`chem-structure-fallback my-2 mx-auto block w-fit max-w-full font-mono text-[12px] text-ink-muted break-all ${className}`}>
        {smiles}
      </span>
    )
  }
  return (
    <svg
      ref={svgRef}
      role="img"
      aria-label={`化学结构式（SMILES: ${smiles}）`}
      data-smiles={smiles}
      className={`chem-structure my-2 mx-auto block max-w-full h-auto ${className}`}
    />
  )
}
