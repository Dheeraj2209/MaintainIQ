// /m/scan (design/2026-10-07-mobile-operator-pwa-design.md, decision 17).
// Reads a machine's QR label with the camera where the browser has a QR
// BarcodeDetector (Chrome/Edge on Android). Everywhere else (iOS Safari,
// Firefox, a denied permission, no camera) the phone's own camera app opens
// the label's URL directly, and a manual "Machine id" field is always there.
// Only same-origin /m/machines/ links are followed (parseMachineQr).
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { ScanLine } from 'lucide-react'
import { Input, Label } from '../../components/ui/input'
import { parseMachineQr } from '../../lib/machineQr'

const SCAN_INTERVAL_MS = 250
const FOREIGN_CODE = "That QR code isn't a MaintainIQ machine label for this site."

// Not in TypeScript's DOM lib yet.
interface BarcodeDetectorLike {
  detect(source: HTMLVideoElement): Promise<{ rawValue: string }[]>
}
interface BarcodeDetectorClass {
  new (options?: { formats: string[] }): BarcodeDetectorLike
  getSupportedFormats(): Promise<string[]>
}

type Mode = 'starting' | 'camera' | 'manual'

async function qrDetector(): Promise<BarcodeDetectorLike | null> {
  const Detector = (window as unknown as { BarcodeDetector?: BarcodeDetectorClass }).BarcodeDetector
  if (!Detector || !navigator.mediaDevices?.getUserMedia) return null
  const formats = await Detector.getSupportedFormats().catch(() => [] as string[])
  return formats.includes('qr_code') ? new Detector({ formats: ['qr_code'] }) : null
}

export function MobileScanPage() {
  const navigate = useNavigate()
  const videoRef = useRef<HTMLVideoElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [stream, setStream] = useState<MediaStream | null>(null)
  const [mode, setMode] = useState<Mode>('starting')
  const [note, setNote] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [manualId, setManualId] = useState('')
  // Bumped to start the camera again (back from the background). Same-route
  // taps on the Scan tab don't remount the page, so this is the only restart.
  const [cameraRun, setCameraRun] = useState(0)
  // The camera was stopped by the visibility handler, not by the user.
  const pausedRef = useRef(false)

  const stopCamera = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setStream(null)
  }, [])

  const openMachine = useCallback(
    (id: string) => {
      stopCamera()
      navigate(`/m/machines/${encodeURIComponent(id)}`)
    },
    [navigate, stopCamera],
  )

  // Start the camera (when the browser can read QR codes at all).
  useEffect(() => {
    let cancelled = false
    let interval: ReturnType<typeof setInterval> | undefined

    async function start() {
      setMode('starting')
      const detector = await qrDetector()
      if (cancelled) return
      if (!detector) return setMode('manual')
      let media: MediaStream
      try {
        media = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
      } catch (e) {
        if (cancelled) return
        setNote(
          e instanceof DOMException && e.name === 'NotAllowedError'
            ? 'Camera permission was denied. Enter the machine id instead.'
            : "Couldn't start the camera. Enter the machine id instead.",
        )
        return setMode('manual')
      }
      if (cancelled) {
        media.getTracks().forEach((t) => t.stop())
        return
      }
      streamRef.current = media
      setStream(media)
      setMode('camera')
      setStatus('Point the camera at a machine label.')

      let busy = false
      interval = setInterval(async () => {
        const video = videoRef.current
        if (busy || !video || !streamRef.current) return
        busy = true
        try {
          const codes = await detector.detect(video)
          for (const { rawValue } of codes) {
            const id = parseMachineQr(rawValue, window.location.origin)
            if (id) {
              clearInterval(interval)
              return openMachine(id)
            }
            setStatus(FOREIGN_CODE)
          }
        } catch {
          // A frame the detector can't read yet (video still starting).
        } finally {
          busy = false
        }
      }, SCAN_INTERVAL_MS)
    }

    void start()
    return () => {
      cancelled = true
      clearInterval(interval)
      stopCamera()
    }
  }, [openMachine, stopCamera, cameraRun])

  // Never leave the camera running in the background; start it again when
  // the app comes back to the front.
  useEffect(() => {
    function onVisibility() {
      if (document.visibilityState === 'hidden' && streamRef.current) {
        stopCamera()
        pausedRef.current = true
        setMode('manual')
        setNote('Camera stopped while the app was in the background. It restarts when you come back.')
      } else if (document.visibilityState === 'visible' && pausedRef.current) {
        pausedRef.current = false
        setNote(null)
        setCameraRun((n) => n + 1)
      }
    }
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [stopCamera])

  useEffect(() => {
    if (videoRef.current && stream) videoRef.current.srcObject = stream
  }, [stream, mode])

  function handleManual(e: FormEvent) {
    e.preventDefault()
    const id = manualId.trim()
    if (id) openMachine(id)
  }

  return (
    <div className="space-y-4">
      <h1 className="flex items-center gap-2 text-xl font-bold text-text">
        <ScanLine className="h-5 w-5 text-accent" aria-hidden />
        Scan a machine
      </h1>

      {mode === 'camera' && (
        <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-black">
          <video
            ref={videoRef}
            aria-label="Camera preview"
            autoPlay
            playsInline
            muted
            className="aspect-[3/4] w-full object-cover"
          />
          {/* Aiming frame. */}
          <div aria-hidden className="pointer-events-none absolute inset-[18%] rounded-2xl border-2 border-accent-2/80" />
        </div>
      )}

      <p aria-live="polite" className="min-h-5 text-sm text-text-muted">
        {mode === 'starting' ? 'Starting…' : status}
      </p>

      {note && (
        <p role="note" className="rounded-xl border border-degrading/40 bg-degrading/10 p-3 text-sm text-degrading">
          {note}
        </p>
      )}

      {mode !== 'camera' && mode !== 'starting' && (
        <p className="text-sm text-text-muted">
          Or open your phone's camera app and point it at the machine's QR label — it opens the machine page directly.
        </p>
      )}

      <form onSubmit={handleManual} className="glass flex items-end gap-2 rounded-2xl p-3">
        <Label className="flex-1">
          Machine id
          <Input
            value={manualId}
            onChange={(e) => setManualId(e.target.value)}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            className="min-h-11"
          />
        </Label>
        <button
          type="submit"
          disabled={!manualId.trim()}
          className="min-h-11 min-w-11 rounded-xl border border-accent/50 bg-accent/20 px-5 text-sm font-medium text-text disabled:opacity-50"
        >
          Open
        </button>
      </form>
    </div>
  )
}
