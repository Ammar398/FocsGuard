/**
 * A task's weekly work time: on these days, at this time, for this long, the
 * employee's computer starts the task's session by itself. The employee still
 * sees the FocusGuard window and can close it to stop.
 */

import { useState } from 'react'

import { WEEKDAYS, endTime, formatSchedule, type Schedule } from '../../lib/schedule'
import { Button } from './ui'

const inputClass =
  'mt-1.5 w-full rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-sm outline-none transition focus:border-[var(--color-accent)] focus:ring-4 focus:ring-[var(--color-accent)]/15'

export const DEFAULT_SCHEDULE: Schedule = { days: [0, 1, 2, 3, 4], start: '09:00', minutes: 240 }

/** Why a schedule cannot be saved, or null when it can. */
export function scheduleProblem(schedule: Schedule): string | null {
  if (schedule.days.length === 0) return 'Pick at least one day.'
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(schedule.start)) return 'Choose a start time.'
  if (!Number.isFinite(schedule.minutes) || schedule.minutes < 5) {
    return 'A scheduled session lasts at least 5 minutes.'
  }
  const [hour, minute] = schedule.start.split(':').map(Number)
  if (hour * 60 + minute + schedule.minutes > 24 * 60) return 'It has to end by midnight.'
  return null
}

export function ScheduleFields({
  value,
  onChange,
}: {
  value: Schedule
  onChange: (value: Schedule) => void
}) {
  const hours = Math.floor(value.minutes / 60)
  const minutes = value.minutes % 60
  const problem = scheduleProblem(value)

  return (
    <div className="space-y-3">
      <div>
        <span className="text-sm font-medium">Days</span>
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {WEEKDAYS.map((label, day) => {
            const on = value.days.includes(day)
            return (
              <button
                key={label}
                type="button"
                aria-pressed={on}
                onClick={() =>
                  onChange({
                    ...value,
                    days: on ? value.days.filter((d) => d !== day) : [...value.days, day].sort(),
                  })
                }
                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition ${
                  on
                    ? 'border-[var(--color-accent)] bg-[var(--color-accent-soft)] text-[var(--color-accent)]'
                    : 'border-[var(--color-line)] text-[var(--color-muted)] hover:border-[var(--color-accent)]'
                }`}
              >
                {label}
              </button>
            )
          })}
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <label className="block">
          <span className="text-sm font-medium">Starts at</span>
          <input
            type="time"
            value={value.start}
            onChange={(event) => onChange({ ...value, start: event.target.value })}
            className={inputClass}
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Hours</span>
          <input
            type="number"
            min={0}
            max={12}
            value={hours}
            onChange={(event) =>
              onChange({ ...value, minutes: Number(event.target.value || 0) * 60 + minutes })
            }
            className={inputClass}
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium">Minutes</span>
          <select
            value={minutes}
            onChange={(event) =>
              onChange({ ...value, minutes: hours * 60 + Number(event.target.value) })
            }
            className={inputClass}
          >
            {[0, 5, 10, 15, 20, 30, 40, 45, 50].map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p
        className={`text-xs ${problem ? 'text-[var(--color-focus-low)]' : 'text-[var(--color-muted)]'}`}
      >
        {problem ??
          `${formatSchedule(value)}, until ${endTime(value.start, value.minutes)}. Times are on the employee's computer clock.`}
      </p>
    </div>
  )
}

/** Create form: a switch plus the fields. */
export function ScheduleToggle({
  value,
  onChange,
}: {
  value: Schedule | null
  onChange: (value: Schedule | null) => void
}) {
  return (
    <div className="rounded-xl border border-[var(--color-line)] p-3.5">
      <label className="flex items-start gap-2.5 text-sm">
        <input
          type="checkbox"
          checked={value !== null}
          onChange={(event) => onChange(event.target.checked ? DEFAULT_SCHEDULE : null)}
          className="mt-0.5 h-4 w-4 accent-[var(--color-accent)]"
        />
        <span>
          <span className="font-medium">Start automatically at a set work time</span>
          <span className="block text-xs text-[var(--color-muted)]">
            The employee's computer starts this task's session by itself and stops it when the
            time is up. They see the FocusGuard window and can close it to stop early.
          </span>
        </span>
      </label>
      {value && (
        <div className="mt-3">
          <ScheduleFields value={value} onChange={onChange} />
        </div>
      )}
    </div>
  )
}

/** Task page: shows the schedule and edits it in place. */
export function ScheduleCard({
  schedule,
  onSave,
}: {
  schedule: Schedule | null
  onSave: (value: Schedule | null) => Promise<void>
}) {
  const [editing, setEditing] = useState<Schedule | null>(null)
  const [busy, setBusy] = useState(false)

  async function save(value: Schedule | null) {
    setBusy(true)
    try {
      await onSave(value)
      setEditing(null)
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <div className="space-y-3">
        <ScheduleFields value={editing} onChange={setEditing} />
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            disabled={busy || scheduleProblem(editing) !== null}
            onClick={() => void save(editing)}
          >
            {busy ? 'Saving…' : 'Save work time'}
          </Button>
          <Button onClick={() => setEditing(null)}>Cancel</Button>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-2 text-sm">
      {schedule ? (
        <p>
          <strong>{formatSchedule(schedule)}</strong>, until{' '}
          {endTime(schedule.start, schedule.minutes)}. The assignee's computer starts this task by
          itself at that time.
        </p>
      ) : (
        <p className="text-[var(--color-muted)]">
          No set work time: the employee starts this task from the portal when they begin.
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => setEditing(schedule ?? DEFAULT_SCHEDULE)}>
          {schedule ? 'Change' : 'Set a work time'}
        </Button>
        {schedule && (
          <Button variant="danger" disabled={busy} onClick={() => void save(null)}>
            Remove
          </Button>
        )}
      </div>
    </div>
  )
}
