/**
 * Edit or remove an employee: name, sign-in email, department, portal
 * password and office hours. Used from the Employees list and the employee page.
 */

import { useState } from 'react'

import { ApiError, api } from '../../api/client'
import type { Department, Employee } from '../../api/managerTypes'
import type { Schedule } from '../../lib/schedule'
import { DEFAULT_SCHEDULE, ScheduleFields, scheduleProblem } from './ScheduleEditor'
import { Button, ErrorBanner, SelectField, TextField } from './ui'

export function EmployeeEditor({
  employee,
  departments,
  onSaved,
  onClose,
  onRemoved,
}: {
  employee: Employee
  departments: Department[]
  onSaved: () => void
  onClose: () => void
  /** After removal; defaults to onSaved then onClose. */
  onRemoved?: () => void
}) {
  const [name, setName] = useState(employee.full_name ?? '')
  const [email, setEmail] = useState(employee.email)
  const [department, setDepartment] = useState(
    employee.department_id ? String(employee.department_id) : '',
  )
  const [password, setPassword] = useState('')
  const [hours, setHours] = useState<Schedule | null>(employee.office_hours ?? null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const hoursProblem = hours ? scheduleProblem(hours) : null
  const passwordProblem =
    password && password.length < 8 ? 'A password needs at least 8 characters.' : null

  async function save() {
    setBusy(true)
    setError(null)
    try {
      await api.manager.updateEmployee(employee.id, {
        full_name: name.trim() || undefined,
        email: email.trim() !== employee.email ? email.trim() : undefined,
        ...(department ? { department_id: Number(department) } : { clear_department: true }),
        ...(password ? { password } : {}),
        ...(hours ? { office_hours: hours } : { clear_office_hours: true }),
      })
      onSaved()
      onClose()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not save.')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      await api.manager.deleteEmployee(employee.id)
      if (onRemoved) {
        onRemoved()
      } else {
        onSaved()
        onClose()
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not remove.')
      setBusy(false)
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Edit ${employee.full_name ?? employee.email}`}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/60 p-4 sm:items-center"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onClose()
      }}
    >
      <div className="w-full max-w-xl rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-5 shadow-[var(--shadow-card)] sm:p-6">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-lg font-semibold">Edit employee</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-sm text-[var(--color-muted)] hover:text-[var(--color-ink)]"
          >
            Close
          </button>
        </div>

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <TextField label="Full name" value={name} onChange={setName} />
          <TextField label="Sign-in email" type="email" value={email} onChange={setEmail} />
          <SelectField label="Department" value={department} onChange={setDepartment}>
            <option value="">No department</option>
            {departments.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </SelectField>
          <TextField
            label={employee.can_sign_in ? 'New portal password' : 'Give portal access (password)'}
            type="password"
            value={password}
            onChange={setPassword}
            hint="Leave empty to keep the current one. At least 8 characters."
          />
        </div>

        <div className="mt-5 rounded-xl border border-[var(--color-line)] p-3.5">
          <label className="flex items-start gap-2.5 text-sm">
            <input
              type="checkbox"
              checked={hours !== null}
              onChange={(event) => setHours(event.target.checked ? DEFAULT_SCHEDULE : null)}
              className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
            />
            <span>
              <span className="font-medium">Office hours: start focus monitoring by itself</span>
              <span className="block text-xs text-[var(--color-muted)]">
                At these times the employee's computer starts a focus session on its own. They
                can close it; you are then told and can ask for it to start again.
              </span>
            </span>
          </label>
          {hours && (
            <div className="mt-3">
              <ScheduleFields value={hours} onChange={setHours} />
            </div>
          )}
        </div>

        <div className="mt-4">
          <ErrorBanner message={error ?? passwordProblem} />
        </div>

        <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
          {confirmDelete ? (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-[var(--color-focus-low)]">
                Remove for good, with all their sessions?
              </span>
              <Button variant="danger" disabled={busy} onClick={() => void remove()}>
                Yes, remove
              </Button>
              <Button onClick={() => setConfirmDelete(false)}>No</Button>
            </div>
          ) : (
            <Button variant="danger" onClick={() => setConfirmDelete(true)}>
              Remove employee
            </Button>
          )}
          <Button
            variant="primary"
            disabled={busy || hoursProblem !== null || passwordProblem !== null || !email.trim()}
            onClick={() => void save()}
          >
            {busy ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </div>
    </div>
  )
}
