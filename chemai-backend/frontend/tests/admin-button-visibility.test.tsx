/* admin-button-visibility.test.tsx — admin-console-p0 任务 4.1：
 * 受限按钮按能力显隐（R-I 系列）—— 无能力时按钮不可见，而非点击后 403。
 * StudentsPage 抽屉里的「重置密码/转班」= student:update 门（教师 403 的中间态是本任务要清的账）。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { AntdProvider } from '../src/components/antd/AntdProvider'
import { ToastProvider } from '../src/components/ui'
import { setCaps, setToken, type LoginUser } from '../src/lib/api'
import { StudentsPage } from '../src/pages/teacher/StudentsPage'

const STUDENTS = [
  { student_id: '202610201', name: '李世睿', class_id: 'class_2025_1', class_name: '2419班', exercises_completed: 50, accuracy: 0.72, barrier_type: {}, last_exercise_at: '2026-08-14T10:42:54' },
]

function mockApi() {
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const u = String(url)
    if (u.includes('/api/classes')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ classes: [{ class_id: 'class_2025_1', name: '2419班' }] }) })
    }
    if (u.includes('/api/users/students')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ students: STUDENTS }) })
    }
    if (u.includes('/api/users/student/')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ success: true, weak_knowledge_points: [], recent_activity: [] }) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  }))
}

const TEACHER: LoginUser = {
  id: 'T_chem', username: 'hai', name: '海老师', role: 'teacher', role_label: '任课教师',
  permissions: ['student:read'], menus: ['students'],
}
const ACADEMIC_ADMIN: LoginUser = {
  ...TEACHER, id: 'AA1', role: 'academic_admin', role_label: '教务管理员',
  permissions: ['student:create', 'student:read', 'student:update', 'student:delete', 'class:create', 'class:read'],
}

function renderPage() {
  render(
    <AntdProvider>
      <ToastProvider>
        <MemoryRouter>
          <StudentsPage />
        </MemoryRouter>
      </ToastProvider>
    </AntdProvider>,
  )
}

async function openDrawerAndQuery() {
  await waitFor(() => { expect(document.querySelector('.ant-table')).not.toBeNull() })
  fireEvent.click(screen.getByText('李世睿'))
  await waitFor(() => { expect(document.querySelector('.ant-drawer')).not.toBeNull() })
}

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
})

describe('受限按钮按能力显隐（admin-console-p0 4.1）', () => {
  it('教师（仅 student:read）：抽屉里看不到 重置密码 / 转班', async () => {
    mockApi()
    setToken('t'); setCaps(TEACHER)
    renderPage()
    await openDrawerAndQuery()
    expect(screen.queryByText('重置密码')).toBeNull()
    expect(screen.queryByText('转班')).toBeNull()
  })

  it('教务管理员（student:update）：重置密码 / 转班 可见', async () => {
    mockApi()
    setToken('t'); setCaps(ACADEMIC_ADMIN)
    renderPage()
    await openDrawerAndQuery()
    expect(screen.getByText('重置密码')).toBeTruthy()
    expect(screen.getByText('转班')).toBeTruthy()
  })
})
