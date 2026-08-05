import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { MachineDetail } from '../components/MachineDetail'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

export function MachineDetailPage() {
  const { id } = useParams<{ id: string }>()
  const { lastEvent } = useLiveEvents()
  const [reloadCount, setReloadCount] = useState(0)

  // MachineDetail fetches internally on its machineId prop; force a fresh
  // fetch (via remount) when a live event lands for this specific machine.
  useEffect(() => {
    if (lastEvent && lastEvent.machine_id === id) setReloadCount((n) => n + 1)
  }, [lastEvent, id])

  if (!id) return null

  return (
    <div className="space-y-4">
      <Link to="/machines" className="text-sm text-accent hover:text-accent-hover">
        ← Back to machines
      </Link>
      <MachineDetail key={`${id}-${reloadCount}`} machineId={id} />
    </div>
  )
}
