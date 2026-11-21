/**
 * Employees: the list, and adding someone (spec section 24).
 *
 * A portal password is optional. Without one the account exists only as the
 * person an agent reports for. With one, the employee can sign in and see their
 * own sessions - the same views a student has - which is the transparency
 * section 62 asks for.
 */

import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'

import { ApiError, api } from '../../api/client'
import type { Department, Employee } from '../../api/managerTypes'
import { Card } from '../../components/StatCard'
import {
  Button,
  ErrorBanner,
  OnlineDot,
  Score,
  SelectField,
  TextField,
} from '../../components/manager/ui'
import { tableClass, tdClass, thClass } from '../../components/manager/helpers'
import { formatDuration } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'

export function EmployeesPage() {
  const employees = usePolling<Employee[]>(() => api.manager.employees(), 30_000)
  const departments = usePolling<Department[]>(() => api.manager.departments(), null)
  const [adding, setAdding] = useState(false)

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Employees</h1>
        <Button variant="primary" onClick={() => setAdding((value) => !value)}>
          {adding ? 'Close' : 'Add employee'}
        </Button>
      </header>

      {adding && (
        <AddEmployee
          departments={departments.data ?? []}
          onAdded={() => {
            setAdding(false)
            employees.refresh()
          }}
        />
      )}

      <ErrorBanner message={employees.error} />

      <Card title={`Team (${employees.data?.length ?? 0})`}>
        {employees.data === null ? (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        ) : employees.data.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No employees yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Name</th>
                  <th className={thClass}>Department</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}>Open tasks</th>
                  <th className={thClass}>Today</th>
                  <th className={thClass}>Focus today</th>
                  <th className={thClass}>Portal</th>
                </tr>
              </thead>
              <tbody>
                {employees.data.map((employee) => (
                  <tr key={employee.id} className={employee.is_active ? '' : 'opacity-50'}>
                    <td className={tdClass}>
                      <Link
                        to={`/employees/${employee.id}`}
                        className="font-medium hover:text-[var(--color-accent)]"
                      >
                        {employee.full_name ?? employee.email}
                      </Link>
                      <div className="text-xs text-[var(--color-muted)]">{employee.email}</div>
                    </td>
                    <td className={tdClass}>{employee.department_name ?? '—'}</td>
                    <td className={tdClass}>
                      {employee.is_active ? <OnlineDot online={employee.online} /> : 'Deactivated'}
                    </td>
                    <td className={tdClass}>{employee.open_tasks}</td>
                    <td className={`${tdClass} tabular-nums`}>
                      {formatDuration(employee.today.monitored_seconds)}
                    </td>
                    <td className={tdClass}>
                      <Score value={employee.today.average_focus_score} />
                    </td>
                    <td className={`${tdClass} text-xs text-[var(--color-muted)]`}>
                      {employee.can_sign_in ? 'Can sign in' : 'Agent only'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}

function AddEmployee({
  departments,
  onAdded,
}: {
  departments: Department[]
  onAdded: () => void
}) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [department, setDepartment] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.manager.createEmployee({
        full_name: name,
        email,
        department_id: department ? Number(department) : null,
        password: password || null,
      })
      onAdded()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not add the employee.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Add employee">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <TextField label="Full name" value={name} onChange={setName} required />
        <TextField label="Email" type="email" value={email} onChange={setEmail} required />
        <SelectField label="Department" value={department} onChange={setDepartment}>
          <option value="">No department</option>
          {departments.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </SelectField>
        <TextField
          label="Portal password (optional)"
          type="password"
          value={password}
          onChange={setPassword}
          hint="Leave empty for an agent-only account. With a password, the employee can sign in and see their own data."
        />
        <div className="sm:col-span-2">
          <ErrorBanner message={error} />
        </div>
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Adding…' : 'Add employee'}
          </Button>
        </div>
      </form>
    </Card>
  )
}
