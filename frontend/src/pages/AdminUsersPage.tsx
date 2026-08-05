import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { Role, UserOut } from '../api/types'
import { api } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { Input, Label, Select } from '../components/ui/input'
import { Button } from '../components/ui/button'
import { Badge } from '../components/ui/badge'

const ROLES: Role[] = ['admin', 'supervisor', 'operator']

export function AdminUsersPage() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<UserOut[]>([])
  const [error, setError] = useState<string | null>(null)
  const [rowError, setRowError] = useState<string | null>(null)

  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<Role>('operator')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)

  const load = useCallback(() => {
    setError(null)
    api
      .listUsers()
      .then(setUsers)
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load users'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    setCreating(true)
    setCreateError(null)
    try {
      const created = await api.createUser({ email, name, password, role })
      setUsers((prev) => [...prev, created])
      setEmail('')
      setName('')
      setPassword('')
      setRole('operator')
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create user')
    } finally {
      setCreating(false)
    }
  }

  async function changeRole(u: UserOut, nextRole: Role) {
    setRowError(null)
    try {
      const updated = await api.updateUser(u.id, { role: nextRole })
      setUsers((prev) => prev.map((row) => (row.id === updated.id ? updated : row)))
    } catch (err) {
      setRowError(err instanceof Error ? err.message : 'Failed to update role')
    }
  }

  async function toggleActive(u: UserOut) {
    setRowError(null)
    try {
      const updated = await api.updateUser(u.id, { is_active: !u.is_active })
      setUsers((prev) => prev.map((row) => (row.id === updated.id ? updated : row)))
    } catch (err) {
      setRowError(err instanceof Error ? err.message : 'Failed to update user')
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-text">Users</h1>
        <p className="text-xs text-text-muted">Manage accounts, roles, and access</p>
      </div>

      {error && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{error}</div>
      )}
      {rowError && (
        <div className="rounded-2xl border border-critical/40 bg-critical/10 p-3 text-sm text-critical backdrop-blur">{rowError}</div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="panel-notch glass overflow-x-auto rounded-2xl lg:col-span-2">
          <table className="w-full text-left text-sm">
            <thead className="bg-white/[0.04]">
              <tr className="text-xs uppercase text-text-muted">
                <th className="px-3 py-2">Name</th>
                <th className="px-3 py-2">Email</th>
                <th className="px-3 py-2">Role</th>
                <th className="px-3 py-2">Status</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-white/10 transition hover:bg-white/[0.03]">
                  <td className="px-3 py-2 text-text">
                    {u.name}
                    {me?.id === u.id && <span className="ml-1 text-xs text-text-muted">(you)</span>}
                  </td>
                  <td className="px-3 py-2 text-text-muted">{u.email}</td>
                  <td className="px-3 py-2">
                    <Select value={u.role} onChange={(e) => changeRole(u, e.target.value as Role)} disabled={me?.id === u.id}>
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </Select>
                  </td>
                  <td className="px-3 py-2">
                    <Badge variant={u.is_active ? 'healthy' : 'unknown'}>{u.is_active ? 'Active' : 'Inactive'}</Badge>
                  </td>
                  <td className="px-3 py-2 text-right">
                    <Button type="button" variant="outline" size="sm" onClick={() => toggleActive(u)} disabled={me?.id === u.id}>
                      {u.is_active ? 'Deactivate' : 'Reactivate'}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="panel-notch glass rounded-2xl p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-text-muted">Add user</h2>
          <form onSubmit={handleCreate} className="grid gap-2">
            <Label>
              Name
              <Input type="text" required value={name} onChange={(e) => setName(e.target.value)} />
            </Label>
            <Label>
              Email
              <Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </Label>
            <Label>
              Password
              <Input type="password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </Label>
            <Label>
              Role
              <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </Label>

            {createError && <p className="text-xs text-critical">{createError}</p>}

            <Button type="submit" variant="accent" size="sm" disabled={creating} className="mt-1 justify-self-start">
              {creating ? 'Creating…' : 'Create user'}
            </Button>
          </form>
        </section>
      </div>
    </div>
  )
}
