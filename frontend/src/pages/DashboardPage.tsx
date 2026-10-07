import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { DndContext, closestCenter, PointerSensor, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy, useSortable, arrayMove } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { GripVertical } from 'lucide-react'
import type { Alert, HealthState, KpiSummary, MachineSummary } from '../api/types'
import { api } from '../api/client'
import { KpiCards } from '../components/KpiCards'
import { MachineGrid } from '../components/MachineGrid'
import { AlertsPanel } from '../components/AlertsPanel'
import { useLiveEvents } from '../realtime/LiveEventsProvider'
import { cn } from '../lib/cn'
import { Button } from '../components/ui/button'
import { RiskHorizon } from '../components/dashboard/RiskHorizon'
import { FleetRings } from '../components/dashboard/FleetRings'
import { MachinesAtRisk } from '../components/dashboard/MachinesAtRisk'

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

/* Shimmering placeholders that hold the shape of the real content while the
   first fetch is in flight, so the layout doesn't jump when data lands. They
   are hidden from assistive tech — a screen reader gains nothing from a
   description of empty boxes. */
function CardSkeletons({ count, className }: { count: number; className: string }) {
  return (
    <div aria-hidden className={className}>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton h-[4.75rem] rounded-2xl" />
      ))}
    </div>
  )
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
        className="absolute right-1 top-1 z-10 cursor-grab touch-none rounded-md p-1 text-text-muted transition hover:bg-white/10 hover:text-accent-2 active:cursor-grabbing"
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

  // The first fetch resolves KPIs, machines, and alerts together, so a null
  // summary with no error means nothing has arrived yet.
  const loading = kpis === null && error === null

  const widgetContent: Record<WidgetId, ReactNode> = {
    kpis: kpis ? (
      <KpiCards summary={kpis} />
    ) : (
      loading && <CardSkeletons count={7} className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-7" />
    ),
    machines: (
      <section aria-label="Machine list">
        {/* Same eyebrow treatment as the widgets above, so the page has one
            section-label style rather than two. */}
        <h2 className="mb-2.5 text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Machines</h2>
        {loading ? (
          <CardSkeletons count={6} className="grid grid-cols-2 gap-3 sm:grid-cols-3" />
        ) : (
          <MachineGrid machines={machines} selectedId={null} onSelect={goToMachine} />
        )}
      </section>
    ),
    alerts: (
      <section aria-label="Open alerts">
        <h2 className="mb-2.5 text-[11px] font-medium uppercase tracking-[0.22em] text-text-muted">Open alerts</h2>
        <AlertsPanel
          alerts={alerts}
          onSelect={(alert) => goToMachine(alert.machine_id)}
          onExplain={(alert) => navigate(`/alerts/${alert.id}`)}
        />
      </section>
    ),
  }

  // Counted from the machine list rather than read off `kpis.health_state_counts`
  // so the rings can never disagree with the grid rendered directly below them.
  const healthCounts = machines.reduce<Partial<Record<HealthState, number>>>((acc, m) => {
    acc[m.health_state] = (acc[m.health_state] ?? 0) + 1
    return acc
  }, {})

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[1.75rem] font-bold leading-tight tracking-[-0.012em] text-text">Fleet overview</h1>
          <p className="mt-0.5 text-xs text-text-muted">Live health, risk, and open alerts across all machines</p>
        </div>
        <Button variant="outline" size="sm" onClick={loadFleet}>
          Refresh
        </Button>
      </div>

      {error && (
        <div className="glass rounded-2xl border-critical/40 p-4">
          <p className="text-sm font-semibold text-critical">Couldn’t load fleet data</p>
          <p className="mt-1 text-sm text-text-muted">{error}</p>
          <Button variant="outline" size="sm" className="mt-3" onClick={loadFleet}>
            Try again
          </Button>
        </div>
      )}

      {/* The fixed head of the dashboard: where the fleet is going, what it is
          made of, and who to look at first. Deliberately outside the sortable
          region below — this is the part you should always meet first, so it
          isn't something the user can accidentally drag to the bottom. */}
      {machines.length > 0 && (
        <div className="space-y-6">
          <RiskHorizon machines={machines} onSelect={goToMachine} />

          <div className="grid gap-6 lg:grid-cols-5">
            <div className="lg:col-span-2">
              <FleetRings counts={healthCounts} total={machines.length} />
            </div>
            <div className="lg:col-span-3">
              <MachinesAtRisk machines={machines} onSelect={goToMachine} />
            </div>
          </div>
        </div>
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
