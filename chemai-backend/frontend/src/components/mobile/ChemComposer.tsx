/* ChemComposer.tsx — 作答输入器（student-daily-loop X1 + 批 2 X2 / REQ-CMP-001..003、REQ-DGM-001）。
 * 三 tab 输入形态（按题意组合）：文本（多空字段）+ 化学符号键盘 + \ce{} 实时预览；
 * 画布（装置图）懒加载 Excalidraw（MIT，≈344KB gzip 不进主包）+ 器材元件库。
 * MathLive（MIT）承载公式结构编辑，同样懒加载。
 * canvasEnabled=true 时暴露 exportCanvas()（提交前导 PNG blob）。 */

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { FileText, Signature, Shapes } from 'lucide-react'
import { CHEM_KEYPAD, toLatexInsert } from '../../lib/chem-keypad'
import { latexToPreview } from '../../lib/chem-preview'
import { ChemMd } from '../ChemMd'
import labLibraryRaw from '../../assets/chem-lab-library.excalidrawlib?raw'

/* math-field web component：React 19 原生支持 custom element */
const MathField = 'math-field' as unknown as React.FC<Record<string, unknown>>

export interface ChemComposerHandle {
  exportCanvas: () => Promise<Blob | null>
}

type TabId = 'text' | 'formula' | 'canvas'

export const ChemComposer = forwardRef<ChemComposerHandle, {
  blanks: number
  values: string[]
  onChange: (next: string[]) => void
  canvasEnabled?: boolean
}>(function ChemComposer({ blanks, values, onChange, canvasEnabled = false }, ref) {
  const [tab, setTab] = useState<TabId>('text')
  const [mlReady, setMlReady] = useState(false)
  const [exReady, setExReady] = useState(false)
  const refs = useRef<Array<{ value: string; executeCommand?: (cmd: unknown) => void } | null>>([])
  const activeIdx = useRef(0)
  const canvasDirty = useRef(false)
  const sceneRef = useRef<unknown[]>([])
  const ExcalidrawHolder = useRef<React.ComponentType<Record<string, unknown>> | null>(null)

  /* MathLive 懒加载（REQ-CMP-002） */
  useEffect(() => {
    let alive = true
    import('mathlive').then(() => {
      if (alive) setMlReady(true)
    }).catch(() => {})
    return () => {
      alive = false
    }
  }, [])

  /* Excalidraw 懒加载（REQ-DGM-001：进画布 tab 才 import） */
  useEffect(() => {
    if (tab !== 'canvas' || ExcalidrawHolder.current) return
    let alive = true
    Promise.all([import('@excalidraw/excalidraw')]).then(([mod]) => {
      if (!alive) return
      ExcalidrawHolder.current = mod.Excalidraw as unknown as React.ComponentType<Record<string, unknown>>
      setExReady(true)
    }).catch(() => {})
    return () => {
      alive = false
    }
  }, [tab])

  useImperativeHandle(ref, () => ({
    exportCanvas: async () => {
      if (!canvasDirty.current) return null
      try {
        const mod = await import('@excalidraw/excalidraw')
        const scene = sceneRef.current
        if (!scene.length) return null
        const blob = await mod.exportToBlob({
          elements: scene as never[],
          appState: { exportBackground: true, viewBackgroundColor: '#ffffff' }, // @token-allow Excalidraw JS 配置需字面值
          files: null,
          mimeType: 'image/png',
        })
        return blob
      } catch {
        return null
      }
    },
  }), [])

  useEffect(() => {
    if (!mlReady) return
    refs.current.forEach((mf, i) => {
      if (mf && (mf.value || '') !== (values[i] || '')) mf.value = values[i] || ''
    })
  }, [mlReady, values])

  const emit = () => {
    onChange(refs.current.map((mf) => mf?.value || ''))
  }

  const insertToken = (insert: string) => {
    const mf = refs.current[activeIdx.current] || refs.current.find(Boolean)
    if (!mf) return
    mf.executeCommand?.(['insert', toLatexInsert(insert)])
    emit()
  }

  /* 预览：逐空 \ce{}，分号分隔；空空跳过（REQ-CMP-003） */
  const previewText = values
    .map((v) => latexToPreview(v))
    .filter((v) => v)
    .map((v) => `$\\ce{${v}}$`)
    .join('　；　')

  const tabs: { id: TabId; label: string; icon: typeof FileText }[] = [
    { id: 'text', label: '文本', icon: FileText },
    { id: 'formula', label: '公式', icon: Signature },
    ...(canvasEnabled ? [{ id: 'canvas' as TabId, label: '画布', icon: Shapes }] : []),
  ]

  return (
    <div className="space-y-2">
      {canvasEnabled && (
        <div className="flex gap-1.5">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1 px-3 py-1.5 rounded-full text-[12px] border transition-colors ${
                tab === t.id
                  ? 'border-primary bg-primary-soft text-primary font-medium'
                  : 'border-border bg-surface text-ink-muted'
              }`}
            >
              <t.icon size={12} />{t.label}
            </button>
          ))}
        </div>
      )}

      {/* 画布 tab（REQ-DGM-001） */}
      {tab === 'canvas' && canvasEnabled && (
        <div className="rounded-lg border-[1.5px] border-border overflow-hidden h-72">
          {exReady && ExcalidrawHolder.current ? (
            <ExcalidrawHolder.current
              initialData={{ libraryItems: (JSON.parse(labLibraryRaw) as { libraryItems?: unknown[] }).libraryItems ?? [] }}
              onChange={(elements: unknown[]) => {
                sceneRef.current = elements
                canvasDirty.current = elements.length > 0
              }}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-[12px] text-ink-muted">画布加载中…</div>
          )}
        </div>
      )}

      {/* 文本/公式 tab（REQ-CMP-001/002）：MathLive 就绪走结构编辑，未就绪纯文本兜底 */}
      {tab === 'text' && !mlReady && (
        <div className="space-y-2">
          {Array.from({ length: blanks }).map((_, i) => (
            <input
              key={i}
              value={values[i] || ''}
              onChange={(e) => {
                const next = [...values]
                next[i] = e.target.value
                onChange(next)
              }}
              onFocus={() => {
                activeIdx.current = i
              }}
              placeholder={`第 ${i + 1} 空`}
              className="w-full h-11 px-3.5 rounded-lg border-[1.5px] border-border bg-surface text-[14px] text-ink outline-none focus:border-primary"
            />
          ))}
          {previewText && <PreviewLine text={previewText} />}
          <KeypadRow onInsert={insertToken} />
        </div>
      )}

      {(tab === 'formula' || (tab === 'text' && mlReady)) && (
        <div className="space-y-2">
          {Array.from({ length: blanks }).map((_, i) => (
            <div key={i} className="flex items-center gap-2">
              <span className="shrink-0 text-micro text-ink-muted w-8">第{i + 1}空</span>
              <MathField
                ref={(el: unknown) => {
                  refs.current[i] = el as { value: string; executeCommand?: (cmd: unknown) => void } | null
                }}
                onFocus={() => {
                  activeIdx.current = i
                }}
                onInput={emit}
                math-virtual-keyboard-state="hidden"
                className="flex-1 min-w-0 min-h-11 rounded-lg border-[1.5px] border-border bg-surface text-[15px] outline-none focus:border-primary px-2"
              />
            </div>
          ))}
          {previewText && <PreviewLine text={previewText} />}
          <KeypadRow onInsert={insertToken} />
        </div>
      )}
    </div>
  )
})

function PreviewLine({ text }: { text: string }) {
  return (
    <div className="px-3 py-2 rounded-lg bg-surface-muted/60 border border-border/50 text-[14px] text-ink overflow-x-auto">
      <span className="text-[10px] text-ink-muted align-top mr-1.5">预览</span>
      <ChemMd text={text} className="!text-[color:var(--color-ink)]" />
    </div>
  )
}

function KeypadRow({ onInsert }: { onInsert: (insert: string) => void }) {
  return (
    <div className="space-y-1.5">
      {CHEM_KEYPAD.map((g) => (
        <div key={g.id} className="flex items-center gap-1.5 overflow-x-auto [scrollbar-width:none]">
          <span className="shrink-0 text-[10px] text-ink-muted w-6">{g.label}</span>
          {g.keys.map((k) => (
            <button
              key={k.label}
              type="button"
              title={k.title}
              onClick={() => onInsert(k.insert)}
              className="shrink-0 h-8 min-w-9 px-2 rounded-md border border-border bg-surface text-sub text-ink active:bg-primary-soft active:text-primary active:border-primary transition-colors"
            >
              {k.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  )
}
