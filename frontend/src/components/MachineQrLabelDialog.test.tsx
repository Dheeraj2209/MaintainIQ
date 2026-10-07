import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MachineQrLabelDialog } from './MachineQrLabelDialog'

describe('MachineQrLabelDialog', () => {
  it('renders a QR image for the absolute mobile deep link, with the id and URL as caption', async () => {
    render(<MachineQrLabelDialog open machineId="m1" onClose={() => {}} />)

    expect(screen.getByRole('dialog', { name: /qr label/i })).toBeInTheDocument()
    const img = await screen.findByRole('img', { name: 'QR code for machine m1' })
    expect(img.getAttribute('src')).toMatch(/^data:image\/svg\+xml/)
    expect(decodeURIComponent(img.getAttribute('src')!)).toContain('<svg')
    expect(screen.getByText('http://localhost:3000/m/machines/m1')).toBeInTheDocument()
    expect(screen.getByText('m1')).toBeInTheDocument()
  })

  it('warns that a label printed from localhost points at localhost', () => {
    render(<MachineQrLabelDialog open machineId="m1" onClose={() => {}} />)
    expect(screen.getByRole('note')).toHaveTextContent(/points at localhost/i)
  })

  it('prints with window.print and closes on Escape', async () => {
    const print = vi.spyOn(window, 'print').mockImplementation(() => {})
    const onClose = vi.fn()
    render(<MachineQrLabelDialog open machineId="m1" onClose={onClose} />)
    await screen.findByRole('img', { name: /qr code/i })

    await userEvent.click(screen.getByRole('button', { name: /print/i }))
    expect(print).toHaveBeenCalledTimes(1)

    await userEvent.keyboard('{Escape}')
    expect(onClose).toHaveBeenCalled()
    print.mockRestore()
  })

  it('renders nothing when closed', () => {
    render(<MachineQrLabelDialog open={false} machineId="m1" onClose={() => {}} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
