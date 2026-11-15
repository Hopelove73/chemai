/* 资产与自定义元素的类型声明（批 2 X2） */

declare module '*.excalidrawlib?raw' {
  const content: string
  export default content
}

declare module '@excalidraw/excalidraw' {
  import type { ComponentType } from 'react'
  export const Excalidraw: ComponentType<Record<string, unknown>>
  export function exportToBlob(opts: {
    elements: unknown[]
    appState?: { exportBackground?: boolean; viewBackgroundColor?: string }
    files?: unknown
    mimeType?: string
  }): Promise<Blob>
}
