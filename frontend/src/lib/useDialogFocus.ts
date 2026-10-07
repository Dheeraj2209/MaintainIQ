import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

// Shared modal behaviour for the work-order dialog and drawer: while `open`,
// Escape calls onClose, focus moves into the panel (its `[data-autofocus]`
// element, else the panel itself, which needs tabIndex={-1}), and when it
// closes focus goes back to whatever opened it.
export function useDialogFocus(open: boolean, onClose: () => void, panelRef: RefObject<HTMLElement | null>) {
  // Latest onClose without re-running the effect (and so re-stealing focus)
  // every time the parent re-renders with a new closure.
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const panel = panelRef.current
    const target = panel?.querySelector<HTMLElement>('[data-autofocus]') ?? panel
    target?.focus()

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      opener?.focus()
    }
  }, [open, panelRef])
}
