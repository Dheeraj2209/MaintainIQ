import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Radar } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { returnPath } from '../lib/returnPath'
import { Input, Label } from '../components/ui/input'
import { Button } from '../components/ui/button'

export function LoginPage() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // RequireAuth saved the whole location; returnPath restores path, search
  // and hash (a push tap or QR deep link), same-origin only.
  if (user) return <Navigate to={returnPath(location.state)} replace />

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
      navigate(returnPath(location.state), { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-aurora flex min-h-screen items-center justify-center px-4">
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: 'easeOut' }}
        className="glass w-full max-w-sm rounded-3xl p-8"
      >
        <div className="mb-7 flex flex-col items-center text-center">
          <span className="animate-settle mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-accent-2 via-accent to-accent shadow-[0_10px_30px_-8px_rgba(124,108,255,0.75)]">
            <Radar className="h-7 w-7 text-text" aria-hidden />
          </span>
          {/* Not `tracking-tight`: Tailwind's -0.025em was sized for the old
              grotesk and closes Zodiak's serifs into each other at 24px. */}
          <h1 className="text-2xl font-bold tracking-[-0.012em] text-text">
            Maintain<span className="text-accent text-glow">IQ</span>
          </h1>
          <p className="mt-1.5 text-sm text-text-muted">Predictive maintenance dashboard</p>
        </div>

        <form onSubmit={handleSubmit} className="grid gap-4">
          <Label>
            Email
            <Input
              type="email"
              required
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Label>
          <Label>
            Password
            <Input
              type="password"
              required
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Label>

          {error && (
            <p role="alert" className="text-sm text-critical">
              {error}
            </p>
          )}

          <Button type="submit" variant="accent" size="lg" disabled={busy} className="mt-2">
            {busy ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>
      </motion.div>
    </div>
  )
}
