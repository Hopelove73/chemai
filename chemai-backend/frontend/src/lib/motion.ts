/* motion presets — 动效接缝的唯一定义处（总册 R-M1）。
 * 引擎：framer-motion。一切编排经这里取 preset，NEVER 页内散写 transition 数值。
 * 时长/缓动与 CSS 令牌同值（--duration-* / --ease-*），两层共用一套令牌律。 */

export const DURATION = { fast: 0.15, normal: 0.25, slow: 0.4 } as const
export const EASE = {
  out: [0.22, 0.61, 0.36, 1],
  spring: [0.34, 1.56, 0.64, 1],
  draw: [0.65, 0, 0.35, 1],
} as const

/** 路由切换：淡入 + 6px 上浮（进出对称） */
export const pageTransition = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0 },
  exit: { opacity: 0, y: -6 },
  transition: { duration: DURATION.normal, ease: EASE.out },
}

/** 列表 stagger：容器 40ms/项，cap 8 项（超出项直接出现不再延迟） */
export const staggerContainer = {
  animate: { transition: { staggerChildren: 0.04 } },
}
export const staggerItem = {
  initial: { opacity: 0, y: 6 },
  animate: { opacity: 1, y: 0, transition: { duration: DURATION.normal, ease: EASE.out } },
}

/** 掌握环 draw-in：旋入 + 回弹（R-M1 签名瞬间③） */
export const ringDraw = {
  initial: { opacity: 0, rotate: -90, scale: 0.92 },
  animate: { opacity: 1, rotate: 0, scale: 1 },
  transition: { duration: DURATION.slow, ease: EASE.spring },
}

/** 关键动作完成反馈：打勾回弹（R-I6 done 态） */
export const donePop = {
  initial: { scale: 0.8, opacity: 0 },
  animate: { scale: 1, opacity: 1 },
  transition: { duration: DURATION.slow, ease: EASE.spring },
}
