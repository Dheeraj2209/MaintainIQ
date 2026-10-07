// Printable QR label for a machine (design/2026-10-07-mobile-operator-pwa-design.md,
// decision 16). The code is a plain deep link to the mobile machine page,
// behind normal sign-in; a phone's camera app opens it directly. The page owns
// the open state (MachineDetailPage), because MachineDetail remounts on every
// live event for the machine.
import { useEffect, useRef, useState } from 'react'
import { Printer, QrCode } from 'lucide-react'
import { machineDeepLink } from '../lib/machineQr'
import { useDialogFocus } from '../lib/useDialogFocus'
import { Button } from './ui/button'

interface Props {
  open: boolean
  machineId: string
  onClose: () => void
}

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])

export function MachineQrLabelDialog(props: Props) {
  if (!props.open) return null
  return <DialogBody key={props.machineId} {...props} />
}

function DialogBody({ machineId, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  useDialogFocus(true, onClose, panelRef)
  // Whatever origin the desktop is on is what the label encodes.
  const url = machineDeepLink(window.location.origin, machineId)
  const [svg, setSvg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // Loaded on demand so the encoder stays out of the main bundle; SVG
    // output needs no canvas.
    import('qrcode')
      .then((QRCode) => QRCode.toString(url, { type: 'svg', errorCorrectionLevel: 'M', margin: 2 }))
      .then((markup) => {
        if (!cancelled) setSvg(markup)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Could not draw the QR code')
      })
    return () => {
      cancelled = true
    }
  }, [url])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-bg/70 p-4 backdrop-blur-sm" onClick={onClose}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="qr-label-heading"
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className="glass-panel max-h-[calc(100dvh-2rem)] w-full max-w-sm overflow-y-auto rounded-2xl border p-5 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <h2 id="qr-label-heading" className="flex items-center gap-2 text-lg font-semibold text-text">
            <QrCode className="h-5 w-5 text-accent" aria-hidden />
            QR label
          </h2>
          <button type="button" data-autofocus onClick={onClose} aria-label="Dismiss" className="text-text-muted hover:text-text">
            ×
          </button>
        </div>

        {LOCAL_HOSTS.has(window.location.hostname) && (
          <p role="note" className="mt-3 rounded-xl border border-degrading/40 bg-degrading/10 p-3 text-xs text-degrading">
            This label points at localhost — print it from the address phones use.
          </p>
        )}

        {/* The only thing that prints (@media print in index.css): black on
            white, whatever the theme. */}
        <div className="print-label mt-4 flex flex-col items-center gap-2 rounded-xl bg-white p-4 text-black">
          {svg ? (
            <img
              src={`data:image/svg+xml;utf8,${encodeURIComponent(svg)}`}
              alt={`QR code for machine ${machineId}`}
              className="h-48 w-48"
            />
          ) : (
            <div className="flex h-48 w-48 items-center justify-center text-xs text-neutral-500">
              {error ?? 'Drawing…'}
            </div>
          )}
          <p className="text-2xl font-bold">{machineId}</p>
          <p className="text-xs font-semibold uppercase tracking-[0.2em]">MaintainIQ</p>
          <p className="break-all text-center font-mono text-[10px]">{url}</p>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
          <Button variant="accent" onClick={() => window.print()} disabled={!svg}>
            <Printer className="h-4 w-4" aria-hidden />
            Print
          </Button>
        </div>
      </div>
    </div>
  )
}
