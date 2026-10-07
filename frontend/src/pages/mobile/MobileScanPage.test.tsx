import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { installPwaStubs } from '../../test/pwaStubs'
import { MobileScanPage } from './MobileScanPage'

function MachinePlaceholder() {
  return <div>machine page {useParams().id}</div>
}

function harness() {
  return (
    <MemoryRouter initialEntries={['/m/scan']}>
      <Routes>
        <Route path="/m/scan" element={<MobileScanPage />} />
        <Route path="/m/machines/:id" element={<MachinePlaceholder />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('MobileScanPage', () => {
  it('without BarcodeDetector offers manual entry and the camera-app route', async () => {
    render(harness())

    expect(await screen.findByText(/open your phone's camera app/i)).toBeInTheDocument()
    expect(screen.queryByLabelText(/camera preview/i)).not.toBeInTheDocument()

    await userEvent.type(screen.getByLabelText('Machine id'), 'm1')
    await userEvent.click(screen.getByRole('button', { name: 'Open' }))

    expect(await screen.findByText('machine page m1')).toBeInTheDocument()
  })

  it('reads a same-origin label with the camera, opens the machine and stops the camera', async () => {
    const detect = vi.fn(async () => [{ rawValue: `${window.location.origin}/m/machines/m2` }])
    const stubs = installPwaStubs({ barcode: { formats: ['qr_code', 'ean_13'], detect }, camera: true })
    render(harness())

    expect(await screen.findByText('machine page m2', {}, { timeout: 3000 })).toBeInTheDocument()
    expect(stubs.getUserMedia).toHaveBeenCalledWith({ video: { facingMode: 'environment' } })
    expect(detect).toHaveBeenCalled()
    expect(stubs.tracks).toHaveLength(1)
    expect(stubs.tracks[0].stop).toHaveBeenCalled()
  })

  it('rejects a foreign code, stays on the page and keeps scanning', async () => {
    const detect = vi.fn(async () => [{ rawValue: 'https://evil.example/m/machines/m2' }])
    installPwaStubs({ barcode: { formats: ['qr_code'], detect }, camera: true })
    render(harness())

    expect(
      await screen.findByText("That QR code isn't a MaintainIQ machine label for this site.", {}, { timeout: 3000 }),
    ).toBeInTheDocument()
    const calls = detect.mock.calls.length
    await waitFor(() => expect(detect.mock.calls.length).toBeGreaterThan(calls), { timeout: 3000 })
    expect(screen.queryByText(/machine page/)).not.toBeInTheDocument()
  })

  it('falls back to manual entry when camera permission is denied', async () => {
    installPwaStubs({
      barcode: { formats: ['qr_code'], detect: async () => [] },
      camera: async () => {
        throw new DOMException('denied', 'NotAllowedError')
      },
    })
    render(harness())

    expect(await screen.findByText(/camera permission was denied/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Machine id')).toBeInTheDocument()
    expect(screen.queryByLabelText(/camera preview/i)).not.toBeInTheDocument()
  })

  it('stops the camera when the page is left', async () => {
    const stubs = installPwaStubs({ barcode: { formats: ['qr_code'], detect: async () => [] }, camera: true })
    const view = render(harness())
    expect(await screen.findByLabelText(/camera preview/i)).toBeInTheDocument()

    view.unmount()

    expect(stubs.tracks[0].stop).toHaveBeenCalled()
  })

  it('uses manual entry when the detector cannot read QR codes', async () => {
    const stubs = installPwaStubs({ barcode: { formats: ['ean_13'], detect: async () => [] }, camera: true })
    render(harness())
    expect(await screen.findByText(/open your phone's camera app/i)).toBeInTheDocument()
    expect(stubs.getUserMedia).not.toHaveBeenCalled()
  })

  it('stops the camera in the background and restarts it on return', async () => {
    const stubs = installPwaStubs({ barcode: { formats: ['qr_code'], detect: async () => [] }, camera: true })
    render(harness())
    expect(await screen.findByLabelText(/camera preview/i)).toBeInTheDocument()

    function setVisibility(state: DocumentVisibilityState) {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
      document.dispatchEvent(new Event('visibilitychange'))
    }
    try {
      setVisibility('hidden')
      expect(stubs.tracks[0].stop).toHaveBeenCalled()
      expect(await screen.findByText(/camera stopped while the app was in the background/i)).toBeInTheDocument()
      expect(screen.queryByLabelText(/camera preview/i)).not.toBeInTheDocument()

      setVisibility('visible')
      expect(await screen.findByLabelText(/camera preview/i)).toBeInTheDocument()
      expect(stubs.getUserMedia).toHaveBeenCalledTimes(2)
      expect(screen.queryByText(/camera stopped/i)).not.toBeInTheDocument()
    } finally {
      // Back to jsdom's own getter.
      delete (document as unknown as { visibilityState?: unknown }).visibilityState
    }
  })
})
