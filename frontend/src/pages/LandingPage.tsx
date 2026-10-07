import { Link } from 'react-router-dom'
import type { CSSProperties } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { Activity, BarChart3, Boxes, FileCheck2, ShieldCheck, Waves } from 'lucide-react'
import { HeroOrb } from '../components/HeroOrb'
import { Button } from '../components/ui/button'

/**
 * Public marketing page at `/`. Deliberately has no app chrome and no data
 * fetching — it must render for signed-out visitors, so it can't depend on the
 * auth context or the API client.
 */

const FEATURES = [
  {
    icon: Activity,
    title: 'Real-time RUL scoring',
    body: 'Remaining-useful-life estimates stream in over WebSockets as new sensor data lands — no polling, no stale dashboards.',
  },
  {
    icon: Waves,
    title: 'Alerting that respects your team',
    body: 'Threshold and anomaly alerts route to the right shift, with an acknowledge workflow so nothing gets double-worked.',
  },
  {
    icon: BarChart3,
    title: 'Model transparency',
    body: 'See exactly which model version scored a machine, when it was retrained, and how confident it is — no black box.',
  },
  {
    icon: Boxes,
    title: 'Fleet-wide overview',
    body: "A single pulse view across every machine's health state, with drill-down to raw sensor traces in two clicks.",
  },
  {
    icon: ShieldCheck,
    title: 'Role-aware access',
    body: 'Operators, supervisors, and admins each see the console shaped for their job — nothing more, nothing less.',
  },
  {
    icon: FileCheck2,
    title: 'Audit-ready reporting',
    body: 'Export maintenance history and model decisions for compliance reviews without digging through raw logs.',
  },
]

/**
 * The severity ramp, shown on the marketing page because it is a genuine
 * differentiator: severity is encoded by intensity rather than by
 * red/amber/green, so it stays readable in greyscale and for colorblind users.
 */
const RAMP = [
  { label: 'Nominal', count: 17, swatch: 'bg-healthy', fill: 18 },
  { label: 'Watch', count: 2, swatch: 'bg-degrading', fill: 45 },
  { label: 'Elevated', count: 3, swatch: 'bg-faulty', fill: 72 },
  { label: 'Urgent', count: 2, swatch: 'bg-critical', fill: 94 },
]

export function LandingPage() {
  const reduceMotion = useReducedMotion()

  // Scroll-reveal preset. With reduced motion we render the end state directly
  // so nothing animates but the layout is identical.
  const reveal = reduceMotion
    ? {}
    : {
        initial: { opacity: 0, y: 18 },
        whileInView: { opacity: 1, y: 0 },
        viewport: { once: true, margin: '-80px' },
        transition: { duration: 0.55, ease: [0.22, 1, 0.36, 1] as const },
      }

  return (
    <div className="min-h-screen overflow-x-hidden">
      {/* ---------------------------------------------------------------- nav */}
      <header className="mx-auto max-w-7xl px-6 pt-6">
        <nav className="glass-panel flex items-center justify-between rounded-full px-6 py-3.5">
          {/* Every display heading on this page is `font-bold`, not
              `font-semibold`, because Zodiak ships 400 and 700 with nothing in
              between. Asking for 600 doesn't get you 600 — the browser walks up
              to the nearest available weight and hands back 700 anyway, so the
              class would describe something the page never renders. Tracking
              comes from the base layer; at 16px Zodiak wants no more than that. */}
          <Link to="/" className="flex items-center gap-2.5 font-display text-base font-bold text-text">
            <span
              className="h-2.5 w-2.5 rounded-full bg-accent shadow-[0_0_12px_2px_rgba(124,108,255,0.65)]"
              aria-hidden
            />
            MaintainIQ
          </Link>
          <div className="hidden items-center gap-7 text-sm text-text-muted md:flex">
            <a href="#features" className="transition-colors hover:text-text">
              Platform
            </a>
            <a href="#severity" className="transition-colors hover:text-text">
              Severity model
            </a>
            <a href="#features" className="transition-colors hover:text-text">
              Docs
            </a>
          </div>
          <div className="flex items-center gap-2.5">
            <Button asChild variant="ghost" size="sm">
              <Link to="/login">Log in</Link>
            </Button>
            <Button asChild size="sm">
              <Link to="/login">Get started</Link>
            </Button>
          </div>
        </nav>
      </header>

      {/* -------------------------------------------------------------- hero */}
      <section className="relative mx-auto max-w-7xl px-6 pt-20 text-center">
        <span className="animate-rise inline-flex items-center gap-2 rounded-full border border-accent-2/25 bg-accent-2/8 px-3.5 py-1.5 text-xs text-accent-2">
          <span className="live-dot" aria-hidden />
          Live sensor telemetry, on every line
        </span>

        {/* Display type wants negative tracking that scales with the size —
            what looks tight at 3rem looks loose at 4.5rem. The values here are
            roughly half what the old grotesk took: a serif's serifs already
            bridge the gap between letters, so pulling in as hard as Clash wanted
            would collide the terminals at 72px. */}
        <h1 className="animate-rise stagger-1 mx-auto mt-6 max-w-4xl text-balance font-display text-5xl font-bold leading-[1.02] tracking-[-0.018em] text-text sm:text-6xl sm:tracking-[-0.022em] lg:text-7xl lg:leading-[0.98]">
          Predict Failures
          <br />
          Before They <span className="text-iridescent">Happen</span>
        </h1>

        <p className="animate-rise stagger-2 mx-auto mt-6 max-w-2xl text-pretty text-base leading-[1.65] text-text-muted sm:text-lg">
          MaintainIQ turns raw vibration, temperature, and pressure signals into a live risk score for every machine on
          your floor — so maintenance stops being a guessing game.
        </p>

        <div className="animate-rise stagger-3 mt-9">
          <Button asChild size="lg" variant="lens">
            <Link to="/login">Start Monitoring →</Link>
          </Button>
        </div>

        <HeroOrb />
      </section>

      {/* ------------------------------------------------------------- logos */}
      <motion.section {...reveal} className="mx-auto max-w-7xl px-6 py-16 text-center">
        <p className="text-[11px] uppercase tracking-[0.16em] text-text-muted">Trusted on production lines at</p>
        {/* `font-normal`, not `font-medium`: 500 is the other weight Zodiak
            doesn't have, and here the browser resolves it downward to 400.
            Letter-spacing stays positive — tracking out is what makes a row of
            caps read as a lockup rather than as a word. */}
        <div className="mt-6 flex flex-wrap items-center justify-center gap-x-14 gap-y-4 font-display text-sm font-normal tracking-[0.14em] text-text-muted/55">
          <span>ATLAS FOUNDRY</span>
          <span>NORTHWIND MFG</span>
          <span>ORION STEEL</span>
          <span>VECTOR PLANT</span>
        </div>
      </motion.section>

      {/* ---------------------------------------------------------- severity */}
      <motion.section {...reveal} id="severity" className="mx-auto max-w-7xl px-6 pb-20">
        <div className="panel-notch glass rounded-3xl p-8 sm:p-10">
          <h2 className="text-balance font-display text-xl font-bold tracking-[-0.01em] text-text sm:text-2xl">
            Severity you can read at a glance — without traffic lights
          </h2>
          <p className="mt-2 max-w-3xl text-pretty text-sm leading-[1.6] text-text-muted">
            Health escalates by <em>intensity</em>, not by red/amber/green. Every state carries a written label, so the
            meaning survives greyscale printing and colour-vision differences alike.
          </p>
          <dl className="mt-7 grid gap-3.5 sm:grid-cols-2 lg:grid-cols-4">
            {RAMP.map((step) => (
              <div key={step.label} className="rounded-2xl border border-white/10 bg-white/4 p-4">
                <dt className="flex items-center gap-2 text-xs font-semibold text-text">
                  <span className={`h-2.5 w-2.5 rounded-full ${step.swatch}`} aria-hidden />
                  {step.label}
                </dt>
                {/* Counts are data, so they take the mono face and tabular
                    figures like every other number in the product. */}
                <dd className="mt-2 font-mono text-2xl font-medium tabular-nums text-text">{step.count}</dd>
                <div className="mt-2.5 h-1 overflow-hidden rounded-full bg-white/12">
                  <div className={`h-full rounded-full ${step.swatch}`} style={{ width: `${step.fill}%` }} />
                </div>
              </div>
            ))}
          </dl>
        </div>
      </motion.section>

      {/* ---------------------------------------------------------- features */}
      <section id="features" className="mx-auto max-w-7xl px-6 pb-24">
        <motion.div {...reveal} className="text-center">
          <h2 className="font-display text-3xl font-bold tracking-[-0.016em] text-text sm:text-4xl">
            One console. Every machine.
          </h2>
          <p className="mx-auto mt-3 max-w-xl text-pretty leading-[1.6] text-text-muted">
            From raw ingestion to a technician&rsquo;s next work order — MaintainIQ covers the whole
            predictive-maintenance loop.
          </p>
        </motion.div>

        <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, body }, i) => (
            <motion.div
              key={title}
              {...(reduceMotion
                ? {}
                : {
                    initial: { opacity: 0, y: 18 },
                    whileInView: { opacity: 1, y: 0 },
                    viewport: { once: true, margin: '-60px' },
                    transition: { duration: 0.5, delay: i * 0.06, ease: [0.22, 1, 0.36, 1] as const },
                  })}
              // Alternating spotlight tone so a hover sweep across the grid
              // shifts violet → azure instead of repeating one colour. Purely
              // decorative: these cards carry no status.
              style={{ '--spot-tone': i % 2 ? 'var(--color-accent-2)' : 'var(--color-accent)' } as CSSProperties}
              className="hover-glow glass rounded-2xl p-6"
            >
              <div className="mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 text-accent-2">
                <Icon className="h-5 w-5" aria-hidden />
              </div>
              <h3 className="font-display text-base font-bold text-text">{title}</h3>
              <p className="mt-2 text-pretty text-sm leading-[1.62] text-text-muted">{body}</p>
            </motion.div>
          ))}
        </div>
      </section>

      {/* --------------------------------------------------------------- cta */}
      <motion.section {...reveal} className="mx-auto max-w-7xl px-6 pb-28">
        <div className="glass rounded-3xl px-10 py-16 text-center">
          <h2 className="text-balance font-display text-2xl font-bold tracking-[-0.013em] text-text sm:text-3xl">
            Bring predictive maintenance to your floor
          </h2>
          <p className="mx-auto mt-3 max-w-lg text-pretty leading-[1.6] text-text-muted">
            Set up your first machine in minutes — no hardware changes required.
          </p>
          <div className="mt-7">
            <Button asChild size="lg" variant="lens">
              <Link to="/login">Sign Up &amp; Monitor</Link>
            </Button>
          </div>
        </div>
      </motion.section>

      <footer className="border-t border-white/6 py-8 text-center text-xs text-text-muted">
        MaintainIQ — predictive maintenance for industrial fleets
      </footer>
    </div>
  )
}
