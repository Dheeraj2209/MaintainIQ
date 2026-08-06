import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { ReportsPage } from './ReportsPage'
import { server } from '../test/server'
import { api } from '../api/client'
import { operatorUser } from '../test/fixtures'

function harness() {
  return (
    <MemoryRouter initialEntries={['/reports']}>
      <AuthProvider>
        <ReportsPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('ReportsPage', () => {
  it('lists existing reports', async () => {
    render(harness())
    expect(await screen.findByText(/fleet_summary/i)).toBeInTheDocument()
    expect(screen.getByText(/machine_prognostic/i)).toBeInTheDocument()
  })

  it('creates a report from the form', async () => {
    const spy = vi.spyOn(api, 'createReport')
    render(harness())
    await screen.findByText(/fleet_summary/i)

    await userEvent.selectOptions(screen.getByLabelText(/report type/i), 'fleet_summary')
    await userEvent.clear(screen.getByLabelText(/scope/i))
    await userEvent.type(screen.getByLabelText(/scope/i), 'fleet')
    await userEvent.click(screen.getByRole('button', { name: /generate/i }))

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ report_type: 'fleet_summary', scope: 'fleet' }),
      ),
    )
    spy.mockRestore()
  })

  it('downloads a report', async () => {
    const spy = vi.spyOn(api, 'downloadReport')
    const createObjectURL = vi.fn(() => 'blob:x')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
    Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    render(harness())
    await screen.findByText(/fleet_summary/i)
    await userEvent.click(screen.getAllByRole('button', { name: /download/i })[0])

    await waitFor(() => expect(spy).toHaveBeenCalled())
    expect(clickSpy).toHaveBeenCalled()
    spy.mockRestore()
    clickSpy.mockRestore()
  })

  it('restricts operators to the machine_prognostic report type', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness())
    await screen.findByText(/fleet_summary/i)

    const typeSelect = within(screen.getByLabelText(/report type/i).closest('label')!)
    expect(typeSelect.getByRole('option', { name: /machine_prognostic/i })).toBeInTheDocument()
    expect(typeSelect.queryByRole('option', { name: /fleet_summary/i })).not.toBeInTheDocument()
  })
})
