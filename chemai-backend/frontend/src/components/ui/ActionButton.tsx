/* ActionButton — 不可逆/异步动作的状态机按钮（总册 R-I6）。
 * idle → busy（禁交互 + 文案态）→ done（打勾回弹，--duration-slow）/ error（回传给调用方处理）。
 * 三处先行：学生发送、教师判卷保存、家长门卡决定；新动作 MUST 复用，NEVER 页内手写状态机。
 * 皮肤沿用 Button（.press-target），这里只承担行为编排。 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Check } from 'lucide-react'
import { motion } from 'framer-motion'
import { donePop } from '../../lib/motion'

import { Button, type ButtonProps, type ButtonSize, type ButtonVariant } from './Button'

type ActionState = 'idle' | 'busy' | 'done'

export interface ActionButtonProps
  extends Omit<ButtonProps, 'onClick' | 'loading' | 'children' | 'onError'> {
  /** 异步动作；失败须抛错，按钮回 idle 并调用 onError */
  onAction: () => Promise<void> | void
  /** 接收动作失败，供调用方 toast/提示；DOM 点击事件不传播 Promise 异常 */
  onError?: (error: unknown) => void
  /** busy 文案（缺省「处理中…」） */
  busyLabel?: string
  /** done 打勾文案；缺省 1.4s 后自动回 idle。传 null = 停在 done */
  doneLabel?: string
  /** done 停留 ms（仅 doneLabel 非空时生效） */
  doneMs?: number
  variant?: ButtonVariant
  size?: ButtonSize
  children: React.ReactNode
}

export function ActionButton({
  onAction, onError, busyLabel = '处理中…', doneLabel = '已完成', doneMs = 1400,
  variant = 'primary', size = 'md', children, disabled, className, ...rest
}: ActionButtonProps) {
  const [state, setState] = useState<ActionState>('idle')
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])
  const run = useCallback(async () => {
    if (state !== 'idle') return
    setState('busy')
    try {
      await onAction()
      if (!alive.current) return
      setState('done')
      if (doneLabel !== null) {
        window.setTimeout(() => alive.current && setState('idle'), doneMs)
      }
    } catch (e) {
      if (alive.current) setState('idle')
      onError?.(e)
    }
  }, [state, onAction, onError, doneLabel, doneMs])

  return (
    <Button
      {...rest}
      variant={variant}
      size={size}
      disabled={disabled || state !== 'idle'}
      className={className}
      onClick={run}
    >
      {state === 'busy' && busyLabel}
      {state === 'done' && doneLabel !== null && (
        <motion.span
          className="inline-flex items-center gap-1.5"
          {...donePop}
        >
          <Check size={size === 'sm' ? 14 : 16} />
          {doneLabel}
        </motion.span>
      )}
      {state === 'idle' && children}
    </Button>
  )
}

/** 配套 hook：非按钮场景（发送键等图标钮）复用同一编排 */
export function useAsyncAction(fn: () => Promise<void> | void) {
  const [state, setState] = useState<ActionState>('idle')
  const alive = useRef(true)
  const busyRef = useRef(false)
  const run = useCallback(async () => {
    if (busyRef.current) return
    busyRef.current = true
    setState('busy')
    try {
      await fn()
      if (alive.current) setState('done')
    } catch {
      if (alive.current) setState('idle')
    } finally {
      busyRef.current = false
    }
  }, [fn])
  return { state, run }
}
