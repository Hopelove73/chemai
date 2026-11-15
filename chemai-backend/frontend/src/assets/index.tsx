/* 素材注册表：设计 agent 生成的品牌素材（素材就位后替换占位） */
import { FlaskConical } from 'lucide-react'
import logoMark from './logo/logo-mark.png'
import favicon from './favicon.png'
import aiTutor from './avatars/ai-tutor.png'
import teacherDefault from './avatars/teacher-default.png'
import emptyChat from './illustrations/empty-chat.png'
import emptyClass from './illustrations/empty-class.png'
import emptyStudents from './illustrations/empty-students.png'
import loginBg from './backgrounds/login-bg.webp'
import mainBg from './backgrounds/main-bg.webp'
import navBg from './backgrounds/nav-bg.webp'
import loginBgAlt1 from './backgrounds/login-bg-alt-1.svg'
import loginBgAlt2 from './backgrounds/login-bg-alt-2.svg'
/* 三端背景图（2026-10-05 用户素材就位，P-21 层 1 接线）：学生端深色霓虹 / 家长端浅色水彩 */
import studentMainBg from './backgrounds/student-main.webp'
import studentLoginBg from './backgrounds/student-login.webp'
import parentMainBg from './backgrounds/parent-main.webp'
import parentLoginBg from './backgrounds/parent-login.webp'

export const assets = {
  logoMark,
  favicon,
  aiTutor,
  teacherDefault,
  emptyChat,
  emptyClass,
  emptyStudents,
  loginBg,
  /** 备选登录背景（切换：LoginPage 内 LOGIN_BG 改一行） */
  loginBgAlt1,
  loginBgAlt2,
  mainBg,
  navBg,
  studentMainBg,
  studentLoginBg,
  parentMainBg,
  parentLoginBg,
}

/** 空状态插画占位（v2）：旧棋盘格占位素材退役，统一为「烧杯图标 + 浅灰面」（P-11/R-S2） */
export function Illo({ src, width = 200 }: { src?: string; width?: number }) {
  if (src) {
    return <img src={src} width={width} alt="" aria-hidden className="mx-auto rounded-[var(--radius-lg)] border border-border object-cover aspect-[4/3]" />
  }
  return (
    <div
      style={{ width }}
      className="mx-auto flex items-center justify-center rounded-[var(--radius-lg)] bg-surface-muted border border-dashed border-border-strong aspect-[4/3]"
    >
      <FlaskConical size={Math.min(46, width * 0.24)} className="text-ink-muted/70" aria-hidden />
    </div>
  )
}
