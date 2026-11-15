import { forwardRef, type ButtonHTMLAttributes } from 'react'
import { Loader2 } from 'lucide-react'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'ai'
export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
}

const variantClasses: Record<ButtonVariant, string> = {
  // R-I7（v2.3 定案重开）：主 CTA = Oxford Blue 实色 + 按压下沉；渐变收敛至登录页品牌区唯一一处
  primary:
    'bg-primary text-white hover:bg-primary-hover press-target',
  secondary:
    'bg-surface text-ink border border-border hover:border-border-strong hover:bg-surface-muted press-target',
  ghost: 'bg-transparent text-ink-secondary hover:bg-surface-muted press-target',
  danger: 'bg-error text-white hover:bg-error/90 shadow-sm press-target',
  // AI 声音按钮（R-C2）：Teal 实色即 AI 的声音，不再借用渐变
  ai: 'bg-teal text-white hover:bg-teal-hover shadow-sm press-target',
}

const sizeClasses: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-sub gap-1.5 rounded-[var(--radius-sm)]',
  md: 'h-10 px-4 text-sm gap-2 rounded-[var(--radius-md)]',
  lg: 'h-11 px-6 text-[15px] gap-2 rounded-[var(--radius-md)]',
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, disabled, className = '', children, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center font-medium transition-all duration-[var(--duration-fast)] disabled:opacity-40 disabled:pointer-events-none cursor-pointer select-none ${variantClasses[variant]} ${sizeClasses[size]} ${className}`}
      {...rest}
    >
      {loading && <Loader2 size={size === 'sm' ? 14 : 16} className="animate-spin" />}
      {children}
    </button>
  )
})
