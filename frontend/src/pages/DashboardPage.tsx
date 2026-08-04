import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy, useSortable, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical } from 'lucide-react'
import type { Alert, KpiSummary, MachineSummary } from '../api/types'
import { api } from '../api/client'
import { KpiCards } from '../components/KpiCards'
import { MachineGrid } from '../components/MachineGrid'
import { AlertsPanel } from '../components/AlertsPanel'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { cn } from '../lib/cn'
import { Button } from '../components/ui/button'
import { Tracker } from '../components/tremor/tracker'
import { healthClasses, healthLabel } from '../components/healthStyles'

type WidgetId = 'kpis' | 'machines' | 'alerts'

const DEFAULT_ORDER: WidgetId[] = ['kpis', 'machines', 'alerts']
const STORAGE_KEY = 'miq:dashboard-layout'

function loadOrder(): WidgetId[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_ORDER
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.length === DEFAULT_ORDER.length && DEFAULT_ORDER.every((id) => parsed.includes(id))) {
      return parsed as WidgetId[]
    }
  } catch {
    // malformed storage — fall back to default order
  }
  return DEFAULT_ORDER
}

function SortableWidget({ id, children }: { id: WidgetId; children: ReactNode }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn('relative rounded-lg', isDragging && 'z-10 opacity-90 ring-2 ring-accent-2')}
    >
      <button
        type="button"
        aria-label="Drag to reorder widget"
        className="absolute right-1 top-1 z-10 cursor-grab touch-none rounded-md p-1 text-text-muted transition hover:bg-surface-hover hover:text-accent-2 active:cursor-grabbing"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" aria-hidden />
      </button>
      {children}
    </div>
  )
}

export function DashboardPage() {
  const [kpis, setKpis] = useState<KpiSummary | null>(null)
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [error, setError] = useState<string | null>(null)
  const [order, setOrder] = useState<WidgetId[]>(loadOrder)
  const navigate = useNavigate()
  const { lastEvent } = useLiveEvents()
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))

  const loadFleet = useCallback(() => {
    setError(null)
    Promise.all([api.getKpiSummary(), api.getMachines(), api.getAlerts('open')])
      .then(([k, m, a]) => {
        setKpis(k)
        setMachines(m)
        setAlerts(a)
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load fleet data'))
  }, [])

  useEffect(() => {
    loadFleet()
  }, [loadFleet])

  // Any alert lifecycle event means the fleet summary is stale — refetch
  // rather than trying to patch the KPI/machine-grid state by hand.
  useEffect(() => {
    if (lastEvent) loadFleet()
  }, [lastEvent, loadFleet])

  const goToMachine = (id: string) => navigate(`/machines/${encodeURIComponent(id)}`)

  function handleDragEnd(event: DragEndEvent) {
    const { active, over } = event
    if (!over || active.id === over.id) return
    setOrder((prev) => {
      const next = arrayMove(prev, prev.indexOf(active.id as WidgetId), prev.indexOf(over.id as WidgetId))
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }

  const widgetContent: Record<WidgetId, ReactNode> = {
    kpis: kpis && <KpiCards summary={kpis} />,
    machines: (
      <section aria-label="Machine list">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Machines</h2>
        <MachineGrid machines={machines} selectedId={null} onSelect={goToMachine} />
      </section>
    ),
    alerts: (
      <section aria-label="Open alerts">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Open alerts</h2>
        <AlertsPanel alerts={alerts} onSelect={goToMachine} />
      </section>
    ),
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-text">Fleet overview</h1>
          <p className="mt-0.5 text-xs text-text-muted">Live health, risk, and open alerts across all machines</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadFleet}>
          Refresh
        </Button>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}

      {machines.length > 0 && (
        <section aria-label="Fleet pulse" className="glass rounded-2xl p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Fleet pulse</h2>
          <Tracker
            cells={machines.map((m) => ({
              key: m.machine_id,
              className: healthClasses(m.health_state).dot,
              tooltip: `${m.machine_id} — ${healthLabel(m.health_state)}`,
              onClick: () => goToMachine(m.machine_id),
            }))}
          />
        </section>
      )}

      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        <SortableContext items={order} strategy={verticalListSortingStrategy}>
          <div className="space-y-6">
            {order.map((id) => (
              <SortableWidget key={id} id={id}>
                {widgetContent[id]}
              </SortableWidget>
            ))}
          </div>
        </SortableContext>
      </DndContext>
    </div>
  )
}
