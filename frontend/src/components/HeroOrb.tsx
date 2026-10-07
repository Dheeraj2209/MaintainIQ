import { useEffect, useRef, useState } from 'react'
import { useReducedMotion } from 'framer-motion'

/**
 * The landing page's signature element: a glossy iridescent sphere with two
 * glass stat cards floating over it.
 *
 * The sphere is pure CSS — a stack of radial-gradients (specular highlight,
 * three colour lobes, dark core) plus an overlay-blended sheen for the caustic
 * rim. Rendering it this way instead of with a WebGL/three.js sphere keeps the
 * bundle flat; nothing here ships a new dependency.
 *
 * Pointer parallax is written to CSS custom properties rather than React state
 * so mousemove never triggers a re-render — the browser just recomposites.
 */
export function HeroOrb() {
  const stageRef = useRef<HTMLDivElement>(null)
  const reduceMotion = useReducedMotion()
  const [visible, setVisible] = useState(false)

  // Fade/scale the orb in once, on mount.
  useEffect(() => {
    const id = requestAnimationFrame(() => setVisible(true))
    return () => cancelAnimationFrame(id)
  }, [])

  useEffect(() => {
    if (reduceMotion) return
    const stage = stageRef.current
    if (!stage) return

    let frame = 0
    function onMove(e: PointerEvent) {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const rect = stage!.getBoundingClientRect()
        // Normalised to roughly -1..1 around the stage centre.
        const px = (e.clientX - rect.left - rect.width / 2) / rect.width
        const py = (e.clientY - rect.top - rect.height / 2) / rect.height
        stage!.style.setProperty('--px', px.toFixed(3))
        stage!.style.setProperty('--py', py.toFixed(3))
      })
    }

    window.addEventListener('pointermove', onMove, { passive: true })
    return () => {
      window.removeEventListener('pointermove', onMove)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [reduceMotion])

  return (
    <div
      ref={stageRef}
      className="relative mt-4 h-[380px] sm:h-[480px] lg:h-[560px]"
      style={{ ['--px' as string]: 0, ['--py' as string]: 0 }}
      aria-hidden
    >
      {/* The sphere. */}
      <div
        className="absolute left-1/2 top-16 h-[540px] w-[540px] rounded-full transition-[opacity,transform] duration-[1200ms] ease-out sm:h-[660px] sm:w-[660px] lg:h-[720px] lg:w-[720px]"
        style={{
          transform: `translate(-50%, 0) translate3d(calc(var(--px) * 18px), calc(var(--py) * 14px), 0) scale(${visible ? 1 : 0.92})`,
          opacity: visible ? 1 : 0,
          backgroundImage: [
            'radial-gradient(circle at 33% 27%, rgba(255,255,255,0.95), rgba(255,255,255,0) 28%)',
            'radial-gradient(circle at 72% 76%, rgba(77,201,255,0.62), transparent 54%)',
            'radial-gradient(circle at 76% 22%, rgba(196,108,255,0.58), transparent 46%)',
            'radial-gradient(circle at 24% 74%, rgba(124,108,255,0.60), transparent 50%)',
            'radial-gradient(circle at 54% 56%, rgba(80,230,225,0.25), transparent 44%)',
            'radial-gradient(circle at 50% 50%, #17193a, #04050d 74%)',
          ].join(','),
          boxShadow: '0 60px 170px -40px rgba(0,0,0,0.9), inset 0 0 130px 24px rgba(0,0,0,0.55)',
          filter: 'saturate(1.2)',
        }}
      >
        {/* Caustic refraction rim + top sheen. */}
        <div
          className="absolute inset-0 rounded-full mix-blend-overlay"
          style={{
            backgroundImage: [
              'linear-gradient(180deg, rgba(255,255,255,0.11), transparent 38%)',
              'radial-gradient(60% 18% at 50% 88%, rgba(150,220,255,0.35), transparent 70%)',
            ].join(','),
          }}
        />
      </div>

      {/* Floating stat cards. They drift opposite the orb for depth. */}
      <div
        className="animate-rise stagger-4 glass absolute left-2 top-[46%] w-56 rounded-2xl p-4 text-left sm:left-[6%] sm:top-[52%]"
        style={{ transform: 'translate3d(calc(var(--px) * -26px), calc(var(--py) * -18px), 0)' }}
      >
        <div className="mb-2 flex items-center justify-between text-[11px] text-text-muted">
          <span>Fleet health</span>
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/90 text-[11px] text-bg">↗</span>
        </div>
        <p className="font-display text-xl font-bold leading-tight text-text">
          Unplanned
          <br />
          Downtime
        </p>
        <p className="mt-1 text-xs text-text-muted">▼ 46% average across fleets</p>
      </div>

      <div
        className="animate-rise stagger-5 glass absolute right-2 top-[62%] w-56 rounded-2xl p-4 text-left sm:right-[6%] sm:top-[68%]"
        style={{ transform: 'translate3d(calc(var(--px) * -20px), calc(var(--py) * -24px), 0)' }}
      >
        <div className="mb-2 flex items-center justify-between text-[11px] text-text-muted">
          <span>Model confidence</span>
          <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/90 text-[11px] text-bg">↗</span>
        </div>
        {/* Mono, like every other figure in the product. It was the display
            face back when that face was a grotesk and the difference was
            academic; a serif percentage sitting beside the mono readings in the
            card above would just look like a mistake. `font-semibold` because
            JetBrains Mono is loaded to 600 only. */}
        <p className="font-mono text-2xl font-semibold tabular-nums text-text">96%</p>
        <div className="mt-3 h-[3px] overflow-hidden rounded-full bg-white/14">
          <div
            className="h-full rounded-full bg-gradient-to-r from-accent-2 to-accent transition-[width] duration-1000 ease-out"
            style={{ width: visible ? '96%' : '0%' }}
          />
        </div>
      </div>
    </div>
  )
}
