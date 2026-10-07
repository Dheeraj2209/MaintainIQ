import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { QrCode } from 'lucide-react'
import { toast } from 'sonner'
import type { Alert, WorkOrder } from '../api/types'
import { AlertCloseDialog } from '../components/AlertCloseDialog'
import { AlertExplanationPanel } from '../components/AlertExplanationPanel'
import type { AlertCloseResult } from '../components/AlertCloseForm'
import { CreateWorkOrderDialog } from '../components/CreateWorkOrderDialog'
import { MachineDetail } from '../components/MachineDetail'
import { MachineQrLabelDialog } from '../components/MachineQrLabelDialog'
import { Button } from '../components/ui/button'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

export function MachineDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { lastEvent } = useLiveEvents()
  const [reloadCount, setReloadCount] = useState(0)
  // The work-order dialog lives here rather than in MachineDetail, which is
  // remounted below on every live event for the machine and would take a
  // half-filled form with it. `alert` null = a free-standing order.
  const [workOrderFor, setWorkOrderFor] = useState<{ alert: Alert | null } | null>(null)
  const closeWorkOrder = useCallback(() => setWorkOrderFor(null), [])
  // The close / record-outcome dialog, kept here for the same reason.
  const [outcomeAlert, setOutcomeAlert] = useState<Alert | null>(null)
  const closeOutcome = useCallback(() => setOutcomeAlert(null), [])
  // The "Why this alert?" panel, kept here for the same reason.
  const [explainAlertId, setExplainAlertId] = useState<number | null>(null)
  const closeExplain = useCallback(() => setExplainAlertId(null), [])
  // The printable QR label (mobile operator view), kept here for the same reason.
  const [qrOpen, setQrOpen] = useState(false)
  const closeQr = useCallback(() => setQrOpen(false), [])

  // MachineDetail fetches internally on its machineId prop; force a fresh
  // fetch (via remount) when a live event lands for this specific machine.
  // Both alert and device events carry machine_id; an unassigned node's
  // events have machine_id null and so match no machine.
  useEffect(() => {
    if (lastEvent && lastEvent.machine_id === id) setReloadCount((n) => n + 1)
  }, [lastEvent, id])

  if (!id) return null

  function handleWorkOrderCreated(order: WorkOrder) {
    setWorkOrderFor(null)
    toast.success(`Work order #${order.id} created`)
    setReloadCount((n) => n + 1)
  }

  function handleOutcomeSaved(result: AlertCloseResult) {
    setOutcomeAlert(null)
    toast.success(result.closed ? `Alert #${result.alert.id} closed` : 'Outcome recorded')
    setReloadCount((n) => n + 1)
  }

  return (
    <div className="rise-children space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Link to="/machines" className="text-sm text-accent hover:text-accent-hover">
          ← Back to machines
        </Link>
        <Button variant="outline" size="sm" onClick={() => setQrOpen(true)}>
          <QrCode className="h-3.5 w-3.5" aria-hidden />
          QR label
        </Button>
      </div>
      <MachineDetail
        key={`${id}-${reloadCount}`}
        machineId={id}
        onCreateWorkOrder={(alert) => setWorkOrderFor({ alert })}
        onRecordOutcome={setOutcomeAlert}
        onExplain={(alert) => setExplainAlertId(alert.id)}
      />
      <CreateWorkOrderDialog
        open={workOrderFor != null}
        alert={workOrderFor?.alert ?? null}
        machineId={id}
        onClose={closeWorkOrder}
        onCreated={handleWorkOrderCreated}
      />
      <AlertCloseDialog open={outcomeAlert != null} alert={outcomeAlert} onClose={closeOutcome} onSaved={handleOutcomeSaved} />
      <MachineQrLabelDialog open={qrOpen} machineId={id} onClose={closeQr} />
      {explainAlertId != null && (
        <AlertExplanationPanel key={explainAlertId} alertId={explainAlertId} onClose={closeExplain} />
      )}
    </div>
  )
}
