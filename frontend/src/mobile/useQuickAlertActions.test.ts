import { act, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import type { Alert, WorkOrder } from '../api/types'
import { openAlerts } from '../test/fixtures'
import { PENDING_WORK_ORDER, useQuickAlertActions } from './useQuickAlertActions'

function alert(id: number): Alert {
  return { ...structuredClone(openAlerts[0]), id, acknowledged_at: null, acknowledged_by: null, active_work_order_id: null }
}

afterEach(() => vi.restoreAllMocks())

describe('useQuickAlertActions', () => {
  it('removing a closed alert keeps other cards’ pending edits out of the server list', async () => {
    let fail!: (e: unknown) => void
    vi.spyOn(api, 'createWorkOrderFromAlert').mockReturnValue(
      new Promise<WorkOrder>((_, reject) => (fail = reject)),
    )
    const { result } = renderHook(() => useQuickAlertActions({ userId: 3 }))
    act(() => result.current.setServerAlerts([alert(5), alert(2)]))

    let request!: Promise<void>
    act(() => {
      request = result.current.createWorkOrder(result.current.alerts[0])
    })
    expect(result.current.alerts[0].active_work_order_id).toBe(PENDING_WORK_ORDER)

    // Alert #2 is closed while #5's request is still in flight.
    act(() => result.current.removeAlert(2))
    await act(async () => {
      fail(new ApiError(500, 'boom'))
      await request
    })

    expect(result.current.alerts.map((a) => a.id)).toEqual([5])
    expect(result.current.alerts[0].active_work_order_id).toBeNull()
    expect(result.current.alerts[0].acknowledged_at).toBeNull()
  })
})
