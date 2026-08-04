import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { AuthProvider } from '../auth/AuthContext'
import { AdminUsersPage } from './AdminUsersPage'
import { server } from '../test/server'

function harness() {
  return (
    <MemoryRouter initialEntries={['/admin/users']}>
      <AuthProvider>
        <AdminUsersPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

function rowFor(email: string) {
  const cell = screen.getByText(email)
  const row = cell.closest('tr')
  if (!row) throw new Error(`row for ${email} not found`)
  return within(row)
}

describe('AdminUsersPage', () => {
  it('loads and renders all users, marking the signed-in admin as read-only', async () => {
    render(harness())
    expect(await screen.findByText('Sam Supervisor')).toBeInTheDocument()
    expect(screen.getByText('Ollie Operator')).toBeInTheDocument()

    const adminRow = rowFor('admin@maintainiq.local')
    expect(adminRow.getByText('(you)')).toBeInTheDocument()
    expect(adminRow.getByRole('combobox')).toBeDisabled()
    expect(adminRow.getByRole('button', { name: /deactivate/i })).toBeDisabled()

    const supervisorRow = rowFor('supervisor@maintainiq.local')
    expect(supervisorRow.getByRole('combobox')).toBeEnabled()
    expect(supervisorRow.getByRole('button', { name: /deactivate/i })).toBeEnabled()
  })

  it('creates a new user and adds it to the table', async () => {
    render(harness())
    await screen.findByText('Sam Supervisor')

    await userEvent.type(screen.getByLabelText(/name/i), 'Nora Newbie')
    await userEvent.type(screen.getByLabelText(/email/i), 'nora@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'hunter2')
    await userEvent.click(screen.getByRole('button', { name: /create user/i }))

    expect(await screen.findByText('Nora Newbie')).toBeInTheDocument()
    expect(screen.getByText('nora@maintainiq.local')).toBeInTheDocument()
  })

  it("changes another user's role", async () => {
    render(harness())
    await screen.findByText('Sam Supervisor')

    const supervisorRow = rowFor('supervisor@maintainiq.local')
    await userEvent.selectOptions(supervisorRow.getByRole('combobox'), 'admin')

    await waitFor(() => expect(supervisorRow.getByRole('combobox')).toHaveValue('admin'))
  })

  it('deactivates another user', async () => {
    render(harness())
    await screen.findByText('Ollie Operator')

    const operatorRow = rowFor('operator@maintainiq.local')
    expect(operatorRow.getByText('Active')).toBeInTheDocument()
    await userEvent.click(operatorRow.getByRole('button', { name: /deactivate/i }))

    expect(await operatorRow.findByText('Inactive')).toBeInTheDocument()
    expect(operatorRow.getByRole('button', { name: /reactivate/i })).toBeInTheDocument()
  })

  it('shows an error banner when the user list fails to load', async () => {
    server.use(http.get('/api/users', () => HttpResponse.json({ detail: 'users unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/users unavailable/i)).toBeInTheDocument()
  })

  it('shows an error when creating a user fails', async () => {
    server.use(http.post('/api/users', () => HttpResponse.json({ detail: 'email already in use' }, { status: 400 })))
    render(harness())
    await screen.findByText('Sam Supervisor')

    await userEvent.type(screen.getByLabelText(/name/i), 'Nora Newbie')
    await userEvent.type(screen.getByLabelText(/email/i), 'nora@maintainiq.local')
    await userEvent.type(screen.getByLabelText(/password/i), 'hunter2')
    await userEvent.click(screen.getByRole('button', { name: /create user/i }))

    expect(await screen.findByText(/email already in use/i)).toBeInTheDocument()
  })
})
