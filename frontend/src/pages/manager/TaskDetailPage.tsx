/**
 * One task: what was asked, what monitoring recorded, how it ended (spec 28, 29).
 *
 * The two task scores are shown with the explanation the backend generated, so
 * "productivity 62" always comes with the minutes and the rules behind it. The
 * quality rating is entered here at completion; it is the single heaviest input
 * to strength analytics, because it is the only measure of the work itself.
 */

import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApiError, api } from '../../api/client'
import {
  categoryLabel,
  type Employee,
  type TaskDetail,
  type TaskInput,
} from '../../api/managerTypes'
import { eventLabel } from '../../api/types'
import { Card, StatCard } from '../../components/StatCard'
import {
  Button,
  ClassificationBar,
  ErrorBanner,
  PrintButton,
  Score,
  SelectField,
  StatusPill,
  UsageList,
} from '../../components/manager/ui'
import { percent } from '../../components/manager/helpers'
import { formatDate, formatDuration } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'

export function TaskDetailPage() {
  const id = Number(useParams().id)
  const { data, error, refresh } = usePolling<TaskDetail>(() => api.manager.task(id), 30_000, [id])
  const employees = usePolling<Employee[]>(() => api.manager.employees(), null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [rating, setRating] = useState('4')
  const [rework, setRework] = useState('0')

  if (data === null) {
    return error ? (
      <ErrorBanner message={error} />
    ) : (
      <p className="p-8 text-[var(--color-muted)]">Loading…</p>
    )
  }

  async function update(body: TaskInput) {
    setActionError(null)
    try {
      await api.manager.updateTask(id, body)
      refresh()
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : 'Could not update the task.')
    }
  }

  const scores = data.scores
  const events = Object.entries(data.event_seconds).sort((a, b) => b[1] - a[1])

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/tasks" className="no-print text-sm text-[var(--color-muted)]">
            ← Tasks
          </Link>
          <h1 className="text-2xl font-semibold">{data.title}</h1>
          <p className="flex flex-wrap items-center gap-3 text-sm text-[var(--color-muted)]">
            <StatusPill status={data.status} />
            <span>{categoryLabel(data.category)}</span>
            <span>{data.priority.toLowerCase()} priority</span>
            {data.project_name && <span>{data.project_name}</span>}
            {data.assignee_id && (
              <Link to={`/employees/${data.assignee_id}`} className="text-[var(--color-accent)]">
                {data.assignee_name}
              </Link>
            )}
          </p>
        </div>
        <PrintButton />
      </header>

      {data.description && <p className="text-sm leading-relaxed">{data.description}</p>}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          label="Task focus"
          value={<Score value={scores.task_focus_score} />}
          hint="visual attention while working"
        />
        <StatCard
          label="Task productivity"
          value={<Score value={scores.task_productivity_score} />}
          hint="apps and sites, under this task's rules"
        />
        <StatCard
          label="Monitored time"
          value={formatDuration(scores.monitored_seconds)}
          hint={
            data.expected_minutes
              ? `${percent(scores.duration_ratio)} of ${data.expected_minutes} min estimate`
              : 'no estimate set'
          }
        />
        <StatCard
          label="Outcome"
          value={data.quality_rating !== null ? `${data.quality_rating}/5` : '—'}
          hint={
            data.deadline
              ? `${data.on_time === null ? 'due' : data.on_time ? 'on time ·' : 'late ·'} ${formatDate(data.deadline)}`
              : 'no deadline'
          }
        />
      </div>

      <Card title="How the scores were reached">
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {scores.explanation.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <div className="mt-4">
          <ClassificationBar
            productive={scores.productive_seconds}
            neutral={scores.neutral_seconds}
            distracting={scores.distracting_seconds}
          />
        </div>
      </Card>

      <div className="no-print">
        <Card title="Actions">
          <div className="flex flex-wrap items-end gap-3">
            {data.status === 'TODO' && (
              <Button onClick={() => void update({ status: 'IN_PROGRESS' })}>Mark in progress</Button>
            )}
            {data.status !== 'COMPLETED' && data.status !== 'CANCELLED' && (
              <>
                <SelectField label="Quality rating" value={rating} onChange={setRating}>
                  {['5', '4.5', '4', '3.5', '3', '2.5', '2', '1.5', '1'].map((value) => (
                    <option key={value} value={value}>
                      {value} / 5
                    </option>
                  ))}
                </SelectField>
                <SelectField label="Rework rounds" value={rework} onChange={setRework}>
                  {['0', '1', '2', '3', '4', '5'].map((value) => (
                    <option key={value} value={value}>
                      {value}
                    </option>
                  ))}
                </SelectField>
                <Button
                  variant="primary"
                  onClick={() =>
                    void update({
                      status: 'COMPLETED',
                      quality_rating: Number(rating),
                      rework_count: Number(rework),
                    })
                  }
                >
                  Complete and rate
                </Button>
                <Button variant="danger" onClick={() => void update({ status: 'CANCELLED' })}>
                  Cancel task
                </Button>
              </>
            )}
            {(data.status === 'COMPLETED' || data.status === 'CANCELLED') && (
              <Button onClick={() => void update({ status: 'IN_PROGRESS' })}>Reopen</Button>
            )}
            <SelectField
              label="Assignee"
              value={data.assignee_id ? String(data.assignee_id) : ''}
              onChange={(value) =>
                void update(value ? { assignee_id: Number(value) } : { clear_assignee: true })
              }
            >
              <option value="">Unassigned</option>
              {(employees.data ?? [])
                .filter((employee) => employee.is_active)
                .map((employee) => (
                  <option key={employee.id} value={employee.id}>
                    {employee.full_name ?? employee.email}
                  </option>
                ))}
            </SelectField>
          </div>
          <div className="mt-3">
            <ErrorBanner message={actionError} />
          </div>
          <p className="mt-3 text-xs text-[var(--color-muted)]">
            Completion is a manager decision. Only completed, rated tasks count toward an
            employee's task-category history.
          </p>
        </Card>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Applications during this task">
          <UsageList rows={data.top_applications} empty="No application use recorded." />
        </Card>
        <Card title="Domains during this task">
          <UsageList rows={data.top_domains} empty="No website use recorded." />
        </Card>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Attention events">
          {events.length === 0 ? (
            <p className="text-sm text-[var(--color-muted)]">None recorded.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {events.map(([type, seconds]) => (
                <li key={type} className="flex justify-between gap-4">
                  <span>{eventLabel(type)}</span>
                  <span className="tabular-nums text-[var(--color-muted)]">
                    {formatDuration(seconds)} · ×{data.event_counts[type] ?? 0}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card title="Rules for this task">
          <dl className="space-y-2 text-sm">
            {(
              [
                ['Productive', data.productive_rules],
                ['Neutral', data.neutral_rules],
                ['Distracting', data.distracting_rules],
              ] as const
            ).map(([label, rules]) => (
              <div key={label}>
                <dt className="text-xs font-medium text-[var(--color-muted)]">{label}</dt>
                <dd>{rules.length ? rules.join(', ') : '—'}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-[var(--color-muted)]">
            Tools that belong to the {categoryLabel(data.category).toLowerCase()} category also
            count as productive. The employee sees these rules before starting.
          </p>
        </Card>
      </div>

      <Card title={`Task sessions (${data.sessions.length})`}>
        {data.sessions.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            No monitored sessions yet. The employee starts one with{' '}
            <code className="text-xs">python -m focusguard_agent.agent --assigned-task {data.id}</code>
          </p>
        ) : (
          <ul className="divide-y divide-[var(--color-line)]">
            {data.sessions.map((session) => (
              <li key={session.id} className="flex items-center justify-between gap-4 py-2 text-sm">
                <Link to={`/sessions/${session.id}`} className="hover:text-[var(--color-accent)]">
                  {formatDate(session.started_at)}
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
    </div>
  )
}
