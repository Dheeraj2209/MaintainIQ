import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../../auth/AuthContext'
import { LiveEventsProvider } from '../../realtime/LiveEventsProvider'
import { server } from '../../test/server'
import { alertExplanation } from '../../test/fixtures'
import { MobileAlertDetailPage } from './MobileAlertDetailPage'

function harness(path = '/m/alerts/2') {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/m/alerts" element={<div>alerts list</div>} />
            <Route path="/m/alerts/:id" element={<MobileAlertDetailPage />} />
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

function serveExplanation() {
  server.use(
    http.get('/api/alerts/:id/explanation', () => {
      const body = structuredClone(alertExplanation)
      return HttpResponse.json({
        ...body,
        alert: { ...body.alert, acknowledged_at: null, acknowledged_by: null, active_work_order_id: null },
      })
    }),
  )
}

describe('MobileAlertDetailPage', () => {
  it('leads with the alert summary, then the explanation in #why', async () => {
    serveExplanation()
    render(harness())

    expect(await screen.findByRole('heading', { level: 1, name: /m1/ })).toBeInTheDocument()
    const why = await screen.findByRole('region', { name: /why this alert/i })
    expect(why).toHaveAttribute('id', 'why')
    expect(await within(why).findByRole('heading', { name: 'Similar past incidents' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /desktop view/i })).toHaveAttribute('href', '/alerts/2')
  })

  it('acknowledges from the action bar', async () => {
    serveExplanation()
    let posted = 0
    server.use(
      http.post('/api/alerts/:id/acknowledge', ({ params }) => {
        posted += 1
        const body = structuredClone(alertExplanation)
        return HttpResponse.json({
          ...body.alert,
          id: Number(params.id),
          acknowledged_at: '2026-10-07T12:00:00Z',
          acknowledged_by: 1,
        })
      }),
    )
    render(harness())

    await userEvent.click(await screen.findByRole('button', { name: 'Acknowledge alert #2' }))

    await waitFor(() => expect(posted).toBe(1))
    expect(await screen.findByText('Acknowledged')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Acknowledge alert #2' })).not.toBeInTheDocument()
  })

  it('says so for an unknown alert, with a way back', async () => {
    render(harness('/m/alerts/999'))

    expect(await screen.findByText('Alert #999 not found')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('link', { name: /back to alerts/i }))
    expect(await screen.findByText('alerts list')).toBeInTheDocument()
  })
})
