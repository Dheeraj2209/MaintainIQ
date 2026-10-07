// Work orders (design/2026-10-07-work-orders-escalation-design.md): every
// tracked repair job, filterable by status, assignee ("Mine") and machine,
// with a detail drawer at /work-orders/:id. The id lives in the path, not the
// query, because LoginPage restores only the pathname after a sign-in.
//
// One list request per load and no polling: every mutation is broadcast as
// work_order_created / work_order_updated, which triggers a re-fetch.
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { toast } from 'sonner'
import { api } from '../api/client'
import { isWorkOrderEvent } from '../api/types'
import type { MachineSummary, WorkOrder, WorkOrderStatus } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { CreateWorkOrderDialog } from '../components/CreateWorkOrderDialog'
import { WorkOrderDrawer } from '../components/WorkOrderDrawer'
import { workOrderPriorityTone, workOrderStatusLabel, workOrderStatusTone } from '../components/healthStyles'
import { Badge } from '../components/ui/badge'
import { Button } from '../components/ui/button'
import { Card } from '../components/ui/card'
import { Select } from '../components/ui/input'
import { formatRelative } from '../lib/telemetryFormat'
import { useLiveEvents } from '../realtime/LiveEventsProvider'

type StatusTab = WorkOrderStatus | 'active' | 'all'

const STATUS_TABS: { value: StatusTab; label: string }[] = [
  { value: 'active', label: 'Active' },
  { value: 'open', label: 'Open' },
  { value: 'assigned', label: 'Assigned' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'done', label: 'Done' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'all', label: 'All' },
]

interface Filters {
  status: StatusTab
  mine: boolean
  machineId: string
}

export function WorkOrdersPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const { user } = useAuth()
  const { lastEvent } = useLiveEvents()
  const [orders, setOrders] = useState<WorkOrder[]>([])
  const [machines, setMachines] = useState<MachineSummary[]>([])
  const [filters, setFilters] = useState<Filters>({ status: 'active', mine: false, machineId: '' })
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  // Filter changes and live events overlap; only the newest load may land.
  const loadSeq = useRef(0)

  const canSupervise = user?.role === 'admin' || user?.role === 'supervisor'
  // Only "Mine" depends on who is signed in, so the user resolving doesn't
  // re-fetch an unfiltered list.
  const assignedTo = filters.mine ? user?.id : undefined
  const { status, machineId } = filters
  const selectedId = id != null && /^\d+$/.test(id) ? Number(id) : null

  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const rows = await api.listWorkOrders({
        status: status === 'all' ? undefined : status,
        assignedTo,
        machineId: machineId || undefined,
      })
      if (seq !== loadSeq.current) return
      setOrders(rows)
      setError(null)
    } catch (e) {
      if (seq !== loadSeq.current) return
      setError(e instanceof Error ? e.message : 'Failed to load work orders')
    }
  }, [status, assignedTo, machineId])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    if (lastEvent && isWorkOrderEvent(lastEvent)) void load()
  }, [lastEvent, load])

  useEffect(() => {
    let cancelled = false
    api
      .getMachines()
      .then((m) => !cancelled && setMachines(m))
      .catch(() => !cancelled && setMachines([]))
    return () => {
      cancelled = true
    }
  }, [])

  const closeDrawer = useCallback(() => navigate('/work-orders'), [navigate])
  const closeDialog = useCallback(() => setCreating(false), [])

  function handleCreated(order: WorkOrder) {
    setCreating(false)
    toast.success(`Work order #${order.id} created`)
    void load()
    navigate(`/work-orders/${order.id}`)
  }

  return (
    <div className="rise-children space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-text">Work orders</h1>
          <p className="text-xs text-text-muted">{orders.length} work orders matching this filter</p>
        </div>
        {canSupervise && (
          <Button type="button" variant="accent" size="sm" onClick={() => setCreating(true)}>
            New work order
          </Button>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div role="tablist" aria-label="Work order status" className="flex flex-wrap gap-1 rounded-full bg-white/5 p-1">
          {STATUS_TABS.map((t) => (
            <button
              key={t.value}
              type="button"
              role="tab"
              aria-selected={filters.status === t.value}
              aria-controls="work-orders-panel"
              onClick={() => setFilters((f) => ({ ...f, status: t.value }))}
              className={
                filters.status === t.value
                  ? 'rounded-full bg-accent/15 px-3 py-1 text-xs font-medium text-accent ring-1 ring-inset ring-accent/30'
                  : 'rounded-full px-3 py-1 text-xs text-text-muted transition hover:text-text'
              }
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-3 text-sm">
          <button
            type="button"
            aria-pressed={filters.mine}
            onClick={() => setFilters((f) => ({ ...f, mine: !f.mine }))}
            className={
              filters.mine
                ? 'rounded-full bg-accent/15 px-3 py-1 text-xs font-medium text-accent ring-1 ring-inset ring-accent/30'
                : 'rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-text-muted transition hover:text-text'
            }
          >
            Mine
          </button>
          <label className="flex items-center gap-1 text-text-muted">
            Machine
            <Select
              value={filters.machineId}
              onChange={(e) => setFilters((f) => ({ ...f, machineId: e.target.value }))}
              className="py-1.5"
            >
              <option value="">All machines</option>
              {machines.map((m) => (
                <option key={m.machine_id} value={m.machine_id}>
                  {m.machine_id}
                </option>
              ))}
            </Select>
          </label>
        </div>
      </div>

      {error && (
        <div role="alert" className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">
          {error}
        </div>
      )}

      <Card id="work-orders-panel" role="tabpanel" className="panel-notch overflow-x-auto p-0">
        <table aria-label="Work orders" className="w-full text-left text-sm">
          <thead className="bg-white/[0.04]">
            <tr className="text-xs uppercase text-text-muted">
              <th className="px-3 py-2">#</th>
              <th className="px-3 py-2">Title</th>
              <th className="px-3 py-2">Machine</th>
              <th className="px-3 py-2">Priority</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2">Assignee</th>
              <th className="px-3 py-2">Alert</th>
              <th className="px-3 py-2">Updated</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-text-muted">
                  No work orders match this filter.
                </td>
              </tr>
            ) : (
              orders.map((w) => (
                <tr
                  key={w.id}
                  onClick={() => navigate(`/work-orders/${w.id}`)}
                  className={`cursor-pointer border-t border-white/10 transition hover:bg-white/[0.05] ${w.id === selectedId ? 'bg-accent/[0.06]' : ''}`}
                >
                  <td className="px-3 py-2 font-mono">
                    <Link
                      to={`/work-orders/${w.id}`}
                      onClick={(e) => e.stopPropagation()}
                      aria-label={`Open work order #${w.id}`}
                      className="text-accent hover:text-accent-hover"
                    >
                      #{w.id}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-text">{w.title}</td>
                  <td className="px-3 py-2 font-mono">
                    <Link
                      to={`/machines/${encodeURIComponent(w.machine_id)}`}
                      onClick={(e) => e.stopPropagation()}
                      className="text-text-muted hover:text-accent"
                    >
                      {w.machine_id}
                    </Link>
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant={workOrderPriorityTone(w.priority)}>{w.priority}</Badge>
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant={workOrderStatusTone(w.status)}>{workOrderStatusLabel(w.status)}</Badge>
                  </td>
                  <td className="px-3 py-2 text-text-muted">{w.assigned_to_name ?? '—'}</td>
                  <td className="px-3 py-2 font-mono text-text-muted">{w.alert_id != null ? `#${w.alert_id}` : '—'}</td>
                  <td className="px-3 py-2 text-text-muted" title={w.updated_at}>
                    {formatRelative(w.updated_at)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>

      {selectedId != null && <WorkOrderDrawer key={selectedId} workOrderId={selectedId} onClose={closeDrawer} onChanged={() => void load()} />}

      <CreateWorkOrderDialog open={creating} onClose={closeDialog} onCreated={handleCreated} />
    </div>
  )
}
