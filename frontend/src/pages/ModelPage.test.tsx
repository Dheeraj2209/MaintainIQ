import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { AuthProvider } from '../auth/AuthContext'
import { ModelPage } from './ModelPage'
import { server } from '../test/server'

function harness() {
  return (
    <MemoryRouter initialEntries={['/model']}>
      <AuthProvider>
        <ModelPage />
      </AuthProvider>
    </MemoryRouter>
  )
}

describe('ModelPage', () => {
  it('renders model health and telemetry', async () => {
    render(harness())

    expect(await screen.findByText(/xjtu_rul_v1/i)).toBeInTheDocument()
    // health status label
    expect(screen.getByText(/healthy/i)).toBeInTheDocument()
    // telemetry: inference count and p95 latency
    expect(screen.getByText('128')).toBeInTheDocument()
    expect(screen.getByText('21.7')).toBeInTheDocument()
  })

  it('shows an error banner when health fails to load', async () => {
    server.use(http.get('/api/model/health', () => HttpResponse.json({ detail: 'model unavailable' }, { status: 500 })))
    render(harness())
    expect(await screen.findByText(/model unavailable/i)).toBeInTheDocument()
  })
})
