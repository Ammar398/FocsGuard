/**
 * Tasks: the list, and creating one with assignee suggestions (spec 28, 31).
 *
 * WHEN SUGGESTIONS APPEAR
 * -----------------------
 * As soon as a category is chosen, the form asks the backend who has the
 * strongest completed-task record in that category and shows the ranking with
 * every reason attached. Clicking a candidate fills the assignee field - it does
 * not submit anything. The manager makes the choice (section 31), and people
 * with too little history are listed separately and never ranked.
 */

import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { ApiError, api } from '../../api/client'
import {
  TASK_CATEGORIES,
  TASK_PRIORITIES,
  TASK_STATUSES,
  categoryLabel,
  statusLabel,
  type Employee,
  type Project,
  type Suggestions,
  type Task,
} from '../../api/managerTypes'
import { Card } from '../../components/StatCard'
import {
  Button,
  ErrorBanner,
  Score,
  SelectField,
  StatusPill,
  TextArea,
  TextField,
} from '../../components/manager/ui'
import { parseRules, tableClass, tdClass, thClass } from '../../components/manager/helpers'
import { formatDate } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'

export function TasksPage() {
  const [status, setStatus] = useState('')
  const [creating, setCreating] = useState(false)
  const tasks = usePolling<Task[]>(() => api.manager.tasks(status || undefined), 30_000, [status])

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Tasks</h1>
        <div className="flex items-center gap-2">
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-1.5 text-sm"
            aria-label="Filter by status"
          >
            <option value="">All statuses</option>
            {TASK_STATUSES.map((value) => (
              <option key={value} value={value}>
                {statusLabel(value)}
              </option>
            ))}
          </select>
          <Button onClick={() => void api.manager.exportTasks()}>Export CSV</Button>
          <Button variant="primary" onClick={() => setCreating((value) => !value)}>
            {creating ? 'Close' : 'New task'}
          </Button>
        </div>
      </header>

      {creating && <CreateTask />}

      <ErrorBanner message={tasks.error} />

      <Card title={`Tasks (${tasks.data?.length ?? 0})`}>
        {tasks.data === null ? (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        ) : tasks.data.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">No tasks.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Task</th>
                  <th className={thClass}>Assignee</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}>Deadline</th>
                  <th className={thClass}>Focus</th>
                  <th className={thClass}>Productivity</th>
                </tr>
              </thead>
              <tbody>
                {tasks.data.map((task) => (
                  <tr key={task.id}>
                    <td className={tdClass}>
                      <Link to={`/tasks/${task.id}`} className="font-medium hover:text-[var(--color-accent)]">
                        {task.title}
                      </Link>
                      <div className="text-xs text-[var(--color-muted)]">
                        {categoryLabel(task.category)} · {task.priority.toLowerCase()}
                        {task.project_name ? ` · ${task.project_name}` : ''}
                      </div>
                    </td>
                    <td className={tdClass}>{task.assignee_name ?? '—'}</td>
                    <td className={tdClass}>
                      <StatusPill status={task.status} />
                    </td>
                    <td className={`${tdClass} text-xs`}>
                      {task.deadline ? formatDate(task.deadline) : '—'}
                      {task.on_time === false && (
                        <span className="ml-1 text-[var(--color-focus-low)]">late</span>
                      )}
                    </td>
                    <td className={tdClass}>
                      <Score value={task.scores.task_focus_score} />
                    </td>
                    <td className={tdClass}>
                      <Score value={task.scores.task_productivity_score} />
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

function CreateTask() {
  const navigate = useNavigate()
  const employees = usePolling<Employee[]>(() => api.manager.employees(), null)
  const projects = usePolling<Project[]>(() => api.manager.projects(), null)

  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState('')
  const [priority, setPriority] = useState('MEDIUM')
  const [project, setProject] = useState('')
  const [assignee, setAssignee] = useState('')
  const [deadline, setDeadline] = useState('')
  const [expected, setExpected] = useState('')
  const [productive, setProductive] = useState('')
  const [neutral, setNeutral] = useState('slack, outlook')
  const [distracting, setDistracting] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const suggestions = usePolling<Suggestions | null>(
    () => (category ? api.manager.suggestions(category) : Promise.resolve(null)),
    null,
    [category],
  )

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const task = await api.manager.createTask({
        title,
        description: description || null,
        category,
        priority,
        project_id: project ? Number(project) : null,
        assignee_id: assignee ? Number(assignee) : null,
        // datetime-local has no zone; Date() reads it as local time, which is
        // what the manager typed it in.
        deadline: deadline ? new Date(deadline).toISOString() : null,
        expected_minutes: expected ? Number(expected) : null,
        productive_rules: parseRules(productive),
        neutral_rules: parseRules(neutral),
        distracting_rules: parseRules(distracting),
      })
      navigate(`/tasks/${task.id}`)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not create the task.')
    } finally {
      setBusy(false)
    }
  }

  const activeEmployees = (employees.data ?? []).filter((employee) => employee.is_active)

  return (
    <Card title="New task">
      <form onSubmit={submit} className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <TextField label="Title" value={title} onChange={setTitle} required />
          </div>
          <div className="sm:col-span-2">
            <TextArea label="Description" value={description} onChange={setDescription} />
          </div>
          <SelectField label="Category" value={category} onChange={setCategory}>
            <option value="">Choose a category…</option>
            {TASK_CATEGORIES.map((value) => (
              <option key={value} value={value}>
                {categoryLabel(value)}
              </option>
            ))}
          </SelectField>
          <SelectField label="Priority" value={priority} onChange={setPriority}>
            {TASK_PRIORITIES.map((value) => (
              <option key={value} value={value}>
                {categoryLabel(value)}
              </option>
            ))}
          </SelectField>
          <SelectField label="Assignee" value={assignee} onChange={setAssignee}>
            <option value="">Unassigned</option>
            {activeEmployees.map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.full_name ?? employee.email}
              </option>
            ))}
          </SelectField>
          <SelectField label="Project" value={project} onChange={setProject}>
            <option value="">No project</option>
            {(projects.data ?? [])
              .filter((item) => item.is_active)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </SelectField>
          <TextField label="Deadline" type="datetime-local" value={deadline} onChange={setDeadline} />
          <TextField
            label="Expected duration (minutes)"
            type="number"
            value={expected}
            onChange={setExpected}
          />
          <div className="sm:col-span-2">
            <TextArea
              label="Productive apps and sites"
              value={productive}
              onChange={setProductive}
              hint="Comma or line separated, e.g. figma.com, Visual Studio Code. The category already counts its usual tools as productive."
            />
          </div>
          <TextArea label="Neutral" value={neutral} onChange={setNeutral} />
          <TextArea
            label="Distracting"
            value={distracting}
            onChange={setDistracting}
            hint="e.g. netflix.com, instagram.com"
          />
          <div className="sm:col-span-2">
            <ErrorBanner message={error} />
          </div>
          <div>
            <Button type="submit" variant="primary" disabled={busy || !category}>
              {busy ? 'Creating…' : 'Create task'}
            </Button>
          </div>
        </div>

        <aside className="rounded-lg border border-[var(--color-line)] p-4">
          <h3 className="text-xs font-medium tracking-wide text-[var(--color-muted)] uppercase">
            Suggested assignees
          </h3>
          {!category ? (
            <p className="mt-2 text-sm text-[var(--color-muted)]">
              Choose a category to see who has the strongest completed-task record for it.
            </p>
          ) : suggestions.data === null ? (
            <p className="mt-2 text-sm text-[var(--color-muted)]">Loading…</p>
          ) : (
            <SuggestionList
              suggestions={suggestions.data}
              selected={assignee}
              onPick={(id) => setAssignee(String(id))}
            />
          )}
        </aside>
      </form>
    </Card>
  )
}

function SuggestionList({
  suggestions,
  selected,
  onPick,
}: {
  suggestions: Suggestions
  selected: string
  onPick: (id: number) => void
}) {
  return (
    <div className="mt-2 space-y-3">
      {suggestions.ranked.length === 0 && (
        <p className="text-sm text-[var(--color-muted)]">
          Nobody has {suggestions.minimum_tasks}+ completed {categoryLabel(suggestions.category).toLowerCase()}{' '}
          tasks yet, so there is no ranking.
        </p>
      )}
      {suggestions.ranked.map((candidate) => (
        <button
          key={candidate.user_id}
          type="button"
          onClick={() => onPick(candidate.user_id)}
          className={`block w-full rounded-lg border p-3 text-left text-sm transition ${
            selected === String(candidate.user_id)
              ? 'border-[var(--color-accent)]'
              : 'border-[var(--color-line)] hover:border-[var(--color-accent)]'
          }`}
        >
          <span className="flex items-baseline justify-between gap-2 font-medium">
            <span>
              {candidate.rank}. {candidate.name}
            </span>
            <Score value={candidate.historical_score} />
          </span>
          <ul className="mt-1 space-y-0.5 text-xs text-[var(--color-muted)]">
            {candidate.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </button>
      ))}

      {suggestions.insufficient_data.length > 0 && (
        <div>
          <p className="text-xs font-medium text-[var(--color-muted)]">
            Not enough history to rank
          </p>
          <ul className="mt-1 space-y-1 text-xs">
            {suggestions.insufficient_data.map((candidate) => (
              <li key={candidate.user_id}>
                <button
                  type="button"
                  onClick={() => onPick(candidate.user_id)}
                  className="hover:text-[var(--color-accent)]"
                >
                  {candidate.name}
                </button>{' '}
                <span className="text-[var(--color-muted)]">
                  · {candidate.completed_tasks} completed · {candidate.open_tasks} open
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-xs text-[var(--color-muted)]">{suggestions.notice}</p>
    </div>
  )
}
