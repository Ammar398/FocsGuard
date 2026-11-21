/**
 * The manager overview (spec section 26).
 *
 * Who is working, what the team's time went on, and how focused it was - as
 * durations, categories and averages. Nothing here shows a screen, a page title
 * or a full address, because none of it was ever collected (section 62).
 *
 * Refreshes every 30 seconds, matching the agent's upload and heartbeat cadence;
 * polling faster would show the same numbers more often.
 */

import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../../api/client'
import type { ManagerDashboard } from '../../api/managerTypes'
import { Card, StatCard } from '../../components/StatCard'
import {
  Button,
  ClassificationBar,
  ErrorBanner,
  OnlineDot,
  PrintButton,
  Score,
  UsageList,
  WindowToggle,
} from '../../components/manager/ui'
import { tableClass, tdClass, thClass } from '../../components/manager/helpers'
import { formatDuration, scoreColor } from '../../lib/format'
import { usePolling } from '../../lib/usePolling'

export function ManagerDashboardPage() {
  const [days, setDays] = useState(1)
  const { data, error, loading } = usePolling<ManagerDashboard>(
    () => api.manager.dashboard(days),
    30_000,
    [days],
  )

  if (loading && data === null) {
    return <p className="p-8 text-[var(--color-muted)]">Loading…</p>
  }
  if (data === null) {
    return <ErrorBanner message={error ?? 'Could not load the dashboard.'} />
  }

  const totals = data.totals
  const windowLabel = days === 1 ? 'today' : `last ${days} days`

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold">{data.organization.name}</h1>
          <p className="text-sm text-[var(--color-muted)]">Team overview, {windowLabel}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <WindowToggle days={days} onChange={setDays} />
          <Button onClick={() => void api.manager.exportEmployees(days)}>Export CSV</Button>
          <PrintButton />
        </div>
      </header>

      <ErrorBanner message={error} />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Employees" value={data.total_employees} />
        <StatCard
          label="Working now"
          value={data.employees_online}
          hint={`${data.employees_offline} offline`}
          accent="var(--color-focus-high)"
        />
        <StatCard
          label="Active tasks"
          value={data.active_tasks}
          hint={`${data.open_tasks} open · ${data.completed_tasks_in_window} completed ${windowLabel}`}
        />
        <StatCard
          label="Average focus"
          value={<Score value={totals.average_focus_score} />}
          hint={`over ${formatDuration(totals.monitored_seconds)} monitored`}
        />
        <StatCard
          label="Productive time"
          value={formatDuration(totals.productive_seconds)}
          accent="var(--color-focus-high)"
        />
        <StatCard
          label="Distracting time"
          value={formatDuration(totals.distracting_seconds)}
          accent="var(--color-focus-low)"
        />
        <StatCard
          label="Visual attention"
          value={<Score value={totals.visual_attention_score} />}
          hint="from webcam events"
        />
        <StatCard
          label="Digital productivity"
          value={<Score value={totals.digital_productivity_score} />}
          hint="from app and site use"
        />
      </div>

      <Card title="Where the time went">
        <ClassificationBar
          productive={totals.productive_seconds}
          neutral={totals.neutral_seconds}
          distracting={totals.distracting_seconds}
        />
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          Classified against each task's own rules, so a site can be productive for one task and
          distracting for another.
        </p>
      </Card>

      <Card title="Employees">
        {data.employees.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            No employees yet. <Link className="text-[var(--color-accent)]" to="/employees">Add one</Link>.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Name</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}>Current task</th>
                  <th className={thClass}>Today</th>
                  <th className={thClass}>Focus</th>
                  <th className={thClass}>Productive</th>
                </tr>
              </thead>
              <tbody>
                {data.employees.map((employee) => {
                  const today = employee.today
                  const classified =
                    today.productive_seconds + today.neutral_seconds + today.distracting_seconds
                  return (
                    <tr key={employee.id}>
                      <td className={tdClass}>
                        <Link
                          to={`/employees/${employee.id}`}
                          className="font-medium hover:text-[var(--color-accent)]"
                        >
                          {employee.full_name ?? employee.email}
                        </Link>
                        <div className="text-xs text-[var(--color-muted)]">
                          {employee.department_name ?? 'No department'}
                        </div>
                      </td>
                      <td className={tdClass}>
                        <OnlineDot online={employee.online} />
                      </td>
                      <td className={tdClass}>
                        {employee.current_task ? (
                          <Link
                            to={`/tasks/${employee.current_task.id}`}
                            className="hover:text-[var(--color-accent)]"
                          >
                            {employee.current_task.title}
                          </Link>
                        ) : (
                          <span className="text-[var(--color-muted)]">—</span>
                        )}
                      </td>
                      <td className={`${tdClass} tabular-nums`}>
                        {formatDuration(today.monitored_seconds)}
                      </td>
                      <td className={tdClass}>
                        <Score value={today.average_focus_score} />
                      </td>
                      <td className={`${tdClass} tabular-nums`}>
                        {classified > 0
                          ? `${Math.round((today.productive_seconds / classified) * 100)}%`
                          : '—'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {data.daily.length > 1 && (
        <Card title="Focus by day">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.daily}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" vertical={false} />
                <XAxis
                  dataKey="day"
                  tickFormatter={(day: string) => day.slice(5)}
                  tick={{ fontSize: 12, fill: 'var(--color-muted)' }}
                />
                <YAxis domain={[0, 100]} tick={{ fontSize: 12, fill: 'var(--color-muted)' }} />
                <Tooltip
                  formatter={(value) => [Math.round(Number(value)), 'Average focus']}
                  contentStyle={{
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-line)',
                  }}
                />
                <Bar dataKey="average_focus_score" radius={[4, 4, 0, 0]}>
                  {data.daily.map((day) => (
                    <Cell
                      key={day.day}
                      fill={
                        day.monitored_seconds > 0
                          ? scoreColor(day.average_focus_score)
                          : 'var(--color-line)'
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Top applications">
          <UsageList rows={data.top_applications} empty="No application use recorded." />
        </Card>
        <Card title="Top domains">
          <UsageList rows={data.top_domains} empty="No website use recorded." />
        </Card>
      </div>

      <Card title="Teams">
        <table className={tableClass}>
          <thead>
            <tr>
              <th className={thClass}>Department</th>
              <th className={thClass}>People</th>
              <th className={thClass}>Monitored</th>
              <th className={thClass}>Productive</th>
              <th className={thClass}>Distracting</th>
              <th className={thClass}>Focus</th>
            </tr>
          </thead>
          <tbody>
            {data.departments.map((department) => (
              <tr key={department.department_id ?? 'none'}>
                <td className={tdClass}>{department.name}</td>
                <td className={tdClass}>{department.employees}</td>
                <td className={`${tdClass} tabular-nums`}>
                  {formatDuration(department.monitored_seconds)}
                </td>
                <td className={`${tdClass} tabular-nums`}>
                  {formatDuration(department.productive_seconds)}
                </td>
                <td className={`${tdClass} tabular-nums`}>
                  {formatDuration(department.distracting_seconds)}
                </td>
                <td className={tdClass}>
                  <Score value={department.average_focus_score} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <p className="text-xs text-[var(--color-muted)]">
        FocusGuard reports durations, categories and scores. It does not record screens, page
        contents, keystrokes or webcam images.
      </p>
    </div>
  )
}
