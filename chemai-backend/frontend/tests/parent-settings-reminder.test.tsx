/* 家长设置页「每日练习提醒」（§11 B4）：开关与时点是真偏好——每次改动都 PUT
 * 到 /api/parent/notify-prefs，payload 里带 practice_reminder。 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import * as apiMod from '../src/lib/api'

vi.mock('../src/lib/api', () => ({
  api: { get: vi.fn(), put: vi.fn(), post: vi.fn() },
}))

import { SettingsPane } from '../src/pages/mobile/parent/settings-pane'

const apiGet = vi.mocked(apiMod.api.get) as unknown as ReturnType<typeof vi.fn>
const apiPut = vi.mocked(apiMod.api.put) as unknown as ReturnType<typeof vi.fn>

const PREFS = {
  types: { practice_completed: true, artifact_shared: true, streak_milestone: true },
  quiet_hours: { enabled: true, start: '22:00', end: '07:00' },
  practice_reminder: { enabled: false, hour: 19 },
}

async function mount() {
  apiGet.mockImplementation((url?: string) => {
    const u = String(url ?? '')
    if (u.includes('notify-prefs')) return Promise.resolve({ prefs: PREFS })
    if (u.includes('notify-channel')) return Promise.resolve({ channel: '', webhook_masked: '' })
    return Promise.resolve({ frequency: 'weekly' })
  })
  apiPut.mockResolvedValue({ success: true })
  render(<SettingsPane />)
  await screen.findByText('每日练习提醒')
}

describe('家长设置页的每日练习提醒', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPut.mockReset()
  })

  it('默认关：不显示时点下拉', async () => {
    await mount()
    expect(screen.queryByText('提醒时间（当天练习没做完才发）')).toBeNull()
  })

  it('打开开关就把 enabled=true 落库', async () => {
    await mount()
    const row = screen.getByText('每日练习提醒').parentElement!
    fireEvent.click(row.querySelector('button[aria-pressed]')!)
    await waitFor(() => expect(apiPut).toHaveBeenCalled())
    const [url, payload] = apiPut.mock.calls[0]
    expect(url).toBe('/api/parent/notify-prefs')
    expect(payload.practice_reminder).toEqual({ enabled: true, hour: 19 })
  })

  it('开关打开后能改时点，且一并落库', async () => {
    await mount()
    const row = screen.getByText('每日练习提醒').parentElement!
    fireEvent.click(row.querySelector('button[aria-pressed]')!)
    const select = await screen.findByLabelText('提醒时间（当天练习没做完才发）') as HTMLSelectElement
    fireEvent.change(select, { target: { value: '21' } })
    await waitFor(() => expect(apiPut.mock.calls.length).toBeGreaterThan(1))
    const last = apiPut.mock.calls[apiPut.mock.calls.length - 1][1]
    expect(last.practice_reminder).toEqual({ enabled: true, hour: 21 })
  })
})