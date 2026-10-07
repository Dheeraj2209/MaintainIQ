// "Restart health tracking" (docs/superpowers/plans/2026-10-07-ratchet-maintenance-reset.md,
// Task 12): one wording for the maintenance form and the work-order
// completion form, so both say what a reset does.
export function RestartHealthCheckbox({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-start gap-2 text-xs text-text">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 accent-[var(--color-accent)]"
      />
      <span>
        Component replaced / fault fixed — restart health tracking (closes the open alert, relearns baseline over the
        next 20 readings)
      </span>
    </label>
  )
}
