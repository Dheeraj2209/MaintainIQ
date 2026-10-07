import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { LiveEventsProvider } from '../realtime/LiveEventsProvider'
import { MockWebSocket } from '../test/mockWebSocket'
import { MobileShell } from './MobileShell'

function harness(path = '/m/alerts') {
  return (
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <LiveEventsProvider>
          <Routes>
            <Route path="/m" element={<MobileShell />}>
              <Route path="alerts" element={<div>alerts tab</div>} />
              <Route path="scan" element={<div>scan tab</div>} />
            </Route>
          </Routes>
        </LiveEventsProvider>
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('MobileShell', () => {
  it('has four labelled bottom tabs, each a full-height touch target', async () => {
    render(harness())
    expect(await screen.findByText('alerts tab')).toBeInTheDocument()

    const nav = within(screen.getByRole('navigation', { name: 'Mobile' }))
    const tabs = nav.getAllByRole('link')
    expect(tabs.map((t) => t.textContent)).toEqual(['Alerts', 'Scan', 'Machines', 'Settings'])
    expect(tabs.map((t) => t.getAttribute('href'))).toEqual(['/m/alerts', '/m/scan', '/m/machines', '/m/settings'])
    tabs.forEach((t) => expect(t).toHaveClass('min-h-14'))
  })

  it('marks the current tab with aria-current', async () => {
    render(harness('/m/scan'))
    await screen.findByText('scan tab')
    const nav = within(screen.getByRole('navigation', { name: 'Mobile' }))
    expect(nav.getByRole('link', { name: 'Scan' })).toHaveAttribute('aria-current', 'page')
    expect(nav.getByRole('link', { name: 'Alerts' })).not.toHaveAttribute('aria-current')
  })

  it('has no desktop sidebar and shows the live connection state', async () => {
    render(harness())
    await screen.findByText('alerts tab')
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument()

    await waitFor(() => expect(MockWebSocket.instances).toHaveLength(1))
    MockWebSocket.instances[0].emitOpen()
    expect(await screen.findByText(/^live$/i)).toBeInTheDocument()
  })
})
