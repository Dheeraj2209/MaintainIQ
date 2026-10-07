import { useEffect } from 'react'

/**
 * Centres each card's spotlight gradient on the cursor, for the one card the
 * cursor is actually inside.
 *
 * This replaced — and now replaces again — `useProximityLight`, which treated
 * the pointer as a lamp with a 195px reach and lit every card within that
 * radius, gradient centre and all, whether or not you were on them. That made
 * the page feel alive in motion but read as noise at rest: half a dashboard
 * breathing at once, and no clear answer to "what am I pointing at". Confining
 * the light to the hovered card puts the effect back to work as an affordance.
 *
 * The listener is delegated on `window` rather than attached per card. There
 * are ~30 cards on the dashboard and they mount and unmount with their data;
 * one handler that asks `closest('.hover-glow')` needs no registration, no
 * MutationObserver, and no teardown per card.
 *
 * Only `--mx`/`--my` are written here. Whether the light is *on* is decided in
 * CSS by `:hover` (see `.hover-glow` in index.css) — so the card the pointer
 * leaves goes dark on its own, and this hook never has to track or reset it.
 * Values are PERCENTAGES, which is what the `var(--mx, 50%)` fallbacks in that
 * file expect before the first pointer event of the session.
 *
 * Call once, at the app root.
 */
export function useSpotlight() {
  useEffect(() => {
    // Honour the OS setting by doing nothing. --mx/--my then stay unset, the
    // gradients centre themselves, and `:hover` still lights the card — just
    // without anything tracking the cursor.
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return

    let frame = 0
    let pending: PointerEvent | null = null

    // Coalesce to one write per frame. Pointer events can arrive faster than
    // the display refreshes, and every one of them costs a layout read.
    function apply() {
      frame = 0
      const event = pending
      pending = null
      if (!event) return

      const target = event.target
      if (!(target instanceof Element)) return
      const card = target.closest<HTMLElement>('.hover-glow')
      if (!card) return

      const rect = card.getBoundingClientRect()
      if (rect.width === 0 || rect.height === 0) return

      card.style.setProperty('--mx', `${(((event.clientX - rect.left) / rect.width) * 100).toFixed(2)}%`)
      card.style.setProperty('--my', `${(((event.clientY - rect.top) / rect.height) * 100).toFixed(2)}%`)
    }

    function onMove(event: PointerEvent) {
      pending = event
      if (frame) return
      frame = requestAnimationFrame(apply)
    }

    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      if (frame) cancelAnimationFrame(frame)
      window.removeEventListener('pointermove', onMove)
    }
  }, [])
}
