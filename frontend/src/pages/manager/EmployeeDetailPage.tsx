/**
 * One employee (spec sections 27, 30).
 *
 * Read top to bottom it answers the manager's question in the order section 69
 * puts it: how the time was used, how the work went, and - only from completed,
 * rated tasks - what the history suggests about which kinds of work have gone
 * best. The strength section always shows the minimum-sample rule and the
 * weights, so the number can be taken apart rather than taken on trust.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '../../api/client'
import {
  categoryLabel,
  type Department,
  type EmployeeDetail,
  type EnrollmentToken,
} from '../../api/managerTypes'
import { eventLabel } from '../../api/types'
import { Card, StatCard } from '../../components/StatCard'
import {
  Button,
  ClassificationBar,
  ErrorBanner,
  OnlineDot,
  PrintButton,
  Score,
  SelectField,
  StatusPill,
  TextField,
  UsageList,
  WindowToggle,
} from '../../components/manager/ui'
import { percent, tableClass, tdClass, thClass } from '../../components/manager/helpers'
import { formatDate, formatDuration } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'

export function EmployeeDetailPage() {
  const id = Number(useParams().id)
  const [days, setDays] = useState(7)
  const { data, error, refresh } = usePolling<EmployeeDetail>(
    () => api.manager.employee(id, days),
    30_000,
    [id, days],
  )

  if (data === null) {
    return error ? (
      <ErrorBanner message={error} />
    ) : (
      <p className="p-8 text-[var(--color-muted)]">Loading…</p>
    )
  }

  const stats = data.window
  const windowLabel = days === 1 ? 'today' : `last ${days} days`

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/employees" className="no-print text-sm text-[var(--color-muted)]">
            ← Employees
          </Link>
          <h1 className="text-2xl font-semibold">{data.full_name ?? data.email}</h1>
          <p className="flex flex-wrap items-center gap-3 text-sm text-[var(--color-muted)]">
            <span>{data.department_name ?? 'No department'}</span>
            <OnlineDot online={data.online} />
            {data.current_task && (
              <span>
                Working on{' '}
                <Link to={`/tasks/${data.current_task.id}`} className="text-[var(--color-accent)]">
                  {data.current_task.title}
                </Link>
              </span>
            )}
            {!data.is_active && <span>Deactivated</span>}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <WindowToggle days={days} onChange={setDays} />
          <PrintButton />
        </div>
      </header>

      <ErrorBanner message={error} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Work time" value={formatDuration(stats.monitored_seconds)} hint={windowLabel} />
        <StatCard label="Average focus" value={<Score value={stats.average_focus_score} />} />
        <StatCard label="Visual attention" value={<Score value={stats.visual_attention_score} />} />
        <StatCard
          label="Computer productivity"
          value={<Score value={stats.digital_productivity_score} />}
        />
        <StatCard label="Phone in view" value={formatDuration(stats.phone_seconds)} />
        <StatCard label="Looking away" value={formatDuration(stats.looking_away_seconds)} />
        <StatCard label="Away from desk" value={formatDuration(stats.face_absent_seconds)} />
        <StatCard label="Tiredness events" value={stats.drowsiness_events} />
        <StatCard label="Eyes closed" value={formatDuration(stats.eyes_closed_seconds)} />
        <StatCard label="Head down" value={formatDuration(stats.head_down_seconds)} />
        <StatCard label="Someone else in frame" value={formatDuration(stats.multiple_faces_seconds)} />
      </div>

      <Card title="Productive, neutral and distracting time">
        <ClassificationBar
          productive={stats.productive_seconds}
          neutral={stats.neutral_seconds}
          distracting={stats.distracting_seconds}
        />
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Most-used applications">
          <UsageList rows={data.top_applications} empty="No application use recorded." />
        </Card>
        <Card title="Most-used domains">
          <UsageList rows={data.top_domains} empty="No website use recorded." />
        </Card>
      </div>

      <StrengthSection data={data} />

      <Card title={`Tasks (${data.tasks.length})`}>
        {data.tasks.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No tasks assigned.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className={`${tableClass} rtable`}>
              <thead>
                <tr>
                  <th className={thClass}>Task</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}>Focus</th>
                  <th className={thClass}>Productivity</th>
                  <th className={thClass}>Rating</th>
                  <th className={thClass}>On time</th>
                </tr>
              </thead>
              <tbody>
                {data.tasks.map((task) => (
                  <tr key={task.id}>
                    <td data-label="" className={tdClass}>
                      <Link to={`/tasks/${task.id}`} className="font-medium hover:text-[var(--color-accent)]">
                        {task.title}
                      </Link>
                      <div className="text-xs text-[var(--color-muted)]">
                        {categoryLabel(task.category)}
                      </div>
                    </td>
                    <td data-label="Status" className={tdClass}>
                      <StatusPill status={task.status} />
                    </td>
                    <td data-label="Focus" className={tdClass}>
                      <Score value={task.scores.task_focus_score} />
                    </td>
                    <td data-label="Productivity" className={tdClass}>
                      <Score value={task.scores.task_productivity_score} />
                    </td>
                    <td data-label="Rating" className={tdClass}>
                      {task.quality_rating !== null ? `${task.quality_rating}/5` : '—'}
                    </td>
                    <td data-label="On time" className={tdClass}>
                      {task.on_time === null ? '—' : task.on_time ? 'Yes' : 'No'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Recent sessions">
        {data.recent_sessions.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No sessions uploaded yet.</p>
        ) : (
          <ul className="divide-y divide-[var(--color-line)]">
            {data.recent_sessions.map((session) => (
              <li key={session.id} className="flex items-center justify-between gap-4 py-2 text-sm">
                <Link to={`/sessions/${session.id}`} className="hover:text-[var(--color-accent)]">
                  {session.name}
                  <span className="ml-2 text-xs text-[var(--color-muted)]">
                    {formatDate(session.started_at)}
                  </span>
                </Link>
                <span className="flex gap-4 tabular-nums">
                  <span className="text-[var(--color-muted)]">
                    {formatDuration(session.duration_seconds)}
                  </span>
                  <Score value={session.ended_at ? session.average_focus_score : null} />
                </span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <DevicesCard data={data} onChange={refresh} />
        <ManageCard data={data} onChange={refresh} />
      </div>
    </div>
  )
}

function StrengthSection({ data }: { data: EmployeeDetail }) {
  const report = data.strengths
  return (
    <Card title="Task-category history">
      <ul className="mb-4 space-y-2">
        {report.statements.map((statement) => (
          <li key={statement} className="text-sm leading-relaxed">
            {statement}
          </li>
        ))}
      </ul>

      {report.categories.length > 0 && (
        <div className="overflow-x-auto">
          <table className={`${tableClass} rtable`}>
            <thead>
              <tr>
                <th className={thClass}>Category</th>
                <th className={thClass}>Completed</th>
                <th className={thClass}>Score</th>
                <th className={thClass}>Quality</th>
                <th className={thClass}>On time</th>
                <th className={thClass}>Time vs estimate</th>
                <th className={thClass}>Focus</th>
                <th className={thClass}>Productivity</th>
              </tr>
            </thead>
            <tbody>
              {report.categories.map((item) => (
                <tr key={item.category}>
                  <td data-label="" className={tdClass}>{categoryLabel(item.category)}</td>
                  <td data-label="Completed" className={tdClass}>{item.completed_tasks}</td>
                  <td data-label="Score" className={tdClass}>
                    {item.sufficient_data ? (
                      <Score value={item.historical_score} />
                    ) : (
                      <span className="text-xs text-[var(--color-muted)]">insufficient data</span>
                    )}
                  </td>
                  <td data-label="Quality" className={tdClass}>
                    {item.average_quality !== null ? `${item.average_quality.toFixed(1)}/5` : '—'}
                  </td>
                  <td data-label="On time" className={tdClass}>{percent(item.on_time_rate)}</td>
                  <td data-label="Time vs estimate" className={tdClass}>{percent(item.average_duration_ratio)}</td>
                  <td data-label="Focus" className={tdClass}>
                    <Score value={item.average_focus} />
                  </td>
                  <td data-label="Productivity" className={tdClass}>
                    <Score value={item.average_productivity} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="mt-4 text-xs leading-relaxed text-[var(--color-muted)]">
        Based only on completed tasks. A score appears after {report.minimum_tasks} completed tasks
        in a category. Weights:{' '}
        {Object.entries(report.weights)
          .map(([name, weight]) => `${name.replace('_', ' ')} ${Math.round(weight * 100)}%`)
          .join(', ')}
        . This is evidence for a decision, not a judgement of the person.
      </p>
    </Card>
  )
}

function DevicesCard({ data, onChange }: { data: EmployeeDetail; onChange: () => void }) {
  const [token, setToken] = useState<EnrollmentToken | null>(null)
  const [deviceName, setDeviceName] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function enrol() {
    setError(null)
    try {
      setToken(await api.manager.enrolDevice(data.id, deviceName || 'Workstation'))
      setDeviceName('')
      onChange()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not create a token.')
    }
  }

  async function revoke(deviceId: number) {
    if (!window.confirm('Revoke this device? It will stop uploading. Its history is kept.')) return
    await api.manager.revokeDevice(deviceId)
    onChange()
  }

  return (
    <Card title="Devices and consent">
      <ul className="space-y-2 text-sm">
        {data.devices.length === 0 && (
          <li className="text-[var(--color-muted)]">No devices enrolled.</li>
        )}
        {data.devices.map((device) => (
          <li key={device.id} className="flex items-center justify-between gap-3">
            <span>
              {device.device_name}
              <span className="ml-2 text-xs text-[var(--color-muted)]">
                {device.revoked
                  ? 'revoked'
                  : device.enrolled
                    ? `${device.platform ?? ''}, agent ${device.agent_version ?? '?'}`
                    : 'waiting for enrolment'}
              </span>
            </span>
            {!device.revoked && (
              <span className="flex items-center gap-3">
                <OnlineDot online={device.online} />
                <Button variant="danger" onClick={() => void revoke(device.id)}>
                  Revoke
                </Button>
              </span>
            )}
          </li>
        ))}
      </ul>

      <div className="no-print mt-4 flex items-end gap-2">
        <div className="grow">
          <TextField label="New device name" value={deviceName} onChange={setDeviceName} placeholder="Workstation" />
        </div>
        <Button onClick={() => void enrol()}>Create enrolment token</Button>
      </div>
      <p className="no-print mt-2 text-xs text-[var(--color-muted)]">
        Easier: give the employee a portal password. They sign in on their own computer and click
        <em> Connect this computer</em>; no token needed.
      </p>
      <ErrorBanner message={error} />

      {token && (
        <div className="mt-4 rounded-lg border border-[var(--color-accent)]/40 p-3 text-sm">
          <p className="font-medium">Enrolment token — shown once, valid for one hour</p>
          <code className="mt-2 block break-all rounded bg-[var(--color-canvas)] p-2 text-xs">
            {token.enrollment_token}
          </code>
          <p className="mt-2 text-xs text-[var(--color-muted)]">On the employee's computer:</p>
          <code className="mt-1 block break-all rounded bg-[var(--color-canvas)] p-2 text-xs">
            python -m focusguard_agent.tools.enroll --token {token.enrollment_token}
          </code>
          <p className="mt-2 text-xs text-[var(--color-muted)]">
            The employee is shown exactly what is and is not collected, and must agree before the
            device is enrolled.
          </p>
        </div>
      )}

      <h3 className="mt-5 text-xs font-medium text-[var(--color-muted)]">
        Consent records
      </h3>
      {data.consents.length === 0 ? (
        <p className="mt-1 text-sm text-[var(--color-muted)]">None recorded yet.</p>
      ) : (
        <ul className="mt-1 space-y-1 text-sm">
          {data.consents.map((consent) => (
            <li key={`${consent.consent_version}-${consent.granted_at}`}>
              Notice {consent.consent_version} accepted {formatDate(consent.granted_at)}
              {consent.revoked_at && `, withdrawn ${formatDate(consent.revoked_at)}`}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function ManageCard({ data, onChange }: { data: EmployeeDetail; onChange: () => void }) {
  const departments = usePolling<Department[]>(() => api.manager.departments(), null)
  const [password, setPassword] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function update(body: Parameters<typeof api.manager.updateEmployee>[1], done: string) {
    setError(null)
    setMessage(null)
    try {
      await api.manager.updateEmployee(data.id, body)
      setMessage(done)
      onChange()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not save.')
    }
  }

  return (
    <Card title="Manage">
      <div className="no-print space-y-4">
        <SelectField
          label="Department"
          value={data.department_id ? String(data.department_id) : ''}
          onChange={(value) =>
            void update(
              value ? { department_id: Number(value) } : { clear_department: true },
              'Department updated.',
            )
          }
        >
          <option value="">No department</option>
          {(departments.data ?? []).map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </SelectField>

        <div className="flex items-end gap-2">
          <div className="grow">
            <TextField
              label={data.can_sign_in ? 'Reset portal password' : 'Give portal access'}
              type="password"
              value={password}
              onChange={setPassword}
              hint="Lets the employee sign in and see their own data."
            />
          </div>
          <Button
            onClick={() => {
              void update({ password }, 'Password set.')
              setPassword('')
            }}
            disabled={password.length < 8}
          >
            Save
          </Button>
        </div>

        <Button
          variant={data.is_active ? 'danger' : 'secondary'}
          onClick={() =>
            void update(
              { is_active: !data.is_active },
              data.is_active ? 'Employee deactivated.' : 'Employee reactivated.',
            )
          }
        >
          {data.is_active ? 'Deactivate employee' : 'Reactivate employee'}
        </Button>

        {message && <p className="text-sm text-[var(--color-focus-high)]">{message}</p>}
        <ErrorBanner message={error} />
      </div>

      <p className="mt-4 text-xs text-[var(--color-muted)]">
        {(['PHONE_VISIBLE', 'GAZE_AWAY', 'FACE_ABSENT', 'EYES_CLOSED', 'HEAD_DOWN', 'MULTIPLE_FACES', 'DROWSINESS'] as const).map(eventLabel).join(', ')}
        : each is a webcam estimate recorded with a confidence, not a certainty.
      </p>
    </Card>
  )
}
