import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { toast } from 'sonner'
import { AuthProvider } from '../../auth/AuthContext'
import { server } from '../../test/server'
import { operatorUser, pushConfigDisabled } from '../../test/fixtures'
import { installPwaStubs } from '../../test/pwaStubs'
import { MobileSettingsPage } from './MobileSettingsPage'

function harness() {
  return (
    <MemoryRouter initialEntries={['/m/settings']}>
      <AuthProvider>
        <MobileSettingsPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

// usePush settles through several awaits (registration, config, permission,
// subscription); give it room when the whole suite loads the machine.
const SETTLE = { timeout: 5000 }

describe('MobileSettingsPage', () => {
  it('shows who is signed in and links back to the desktop console', async () => {
    server.use(http.get('/api/auth/me', () => HttpResponse.json(operatorUser)))
    render(harness())

    expect(await screen.findByText(operatorUser.name)).toBeInTheDocument()
    expect(screen.getByText(operatorUser.email)).toBeInTheDocument()
    expect(screen.getByText('operator')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /desktop view/i })).toHaveAttribute('href', '/dashboard')
    expect(screen.getByRole('button', { name: /log out/i })).toBeInTheDocument()
  })

  it('turns push on from the switch, then sends a test notification', async () => {
    const stubs = installPwaStubs()
    const success = vi.spyOn(toast, 'success')
    render(harness())

    const toggle = await screen.findByRole('switch', { name: /push notifications on this device/i })
    await waitFor(() => expect(toggle).toBeEnabled(), SETTLE)
    expect(toggle).toHaveAttribute('aria-checked', 'false')

    await userEvent.click(toggle)

    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'), SETTLE)
    expect(stubs.requestPermission).toHaveBeenCalled()

    await userEvent.click(screen.getByRole('button', { name: 'Send test' }))
    await waitFor(() => expect(success).toHaveBeenCalledWith('Test notification sent to 1 device'))
    success.mockRestore()
  })

  it('explains a server without push keys and disables the switch', async () => {
    server.use(http.get('/api/push/vapid-public-key', () => HttpResponse.json(pushConfigDisabled)))
    render(harness())

    expect(await screen.findByText("Push isn't configured on this server.", {}, SETTLE)).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /push notifications/i })).toBeDisabled()
  })

  it('explains an insecure origin', async () => {
    installPwaStubs({ secure: false })
    render(harness())
    expect(await screen.findByText(/push needs https/i, {}, SETTLE)).toBeInTheDocument()
  })
})
