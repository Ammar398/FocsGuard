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
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { api } from '../../api/client'
import type { ManagerDashboard } from '../../api/managerTypes'
import { Aperture } from '../../components/Aperture'
import { Card, StatCard } from '../../components/StatCard'
import {
  IconAlert,
  IconBolt,
  IconChart,
  IconCheck,
  IconPhone,
  IconTasks,
  IconUsers,
} from '../../components/icons'
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
import { formatDuration } from '../../lib/format'
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
      <header className="fg-rise flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-[var(--color-muted)]">Team overview, {windowLabel}</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight sm:text-3xl">
            {data.organization.name}
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <WindowToggle days={days} onChange={setDays} />
          <Button onClick={() => void api.manager.exportEmployees(days)}>Export CSV</Button>
          <PrintButton />
        </div>
      </header>

      <ErrorBanner message={error} />

      <div className="grid gap-4 lg:grid-cols-[1.1fr_2fr]">
        {/* The one hero on this view: the team's focus, as the aperture. */}
        <section className="flex items-center gap-6 rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
          <Aperture
            size={132}
            score={totals.average_focus_score}
            animate
            label="Team average focus"
          />
          <div>
            <p className="text-sm text-[var(--color-muted)]">Team average focus</p>
            <p className="mt-1 text-6xl leading-none font-[750] [font-stretch:112%]">
              {totals.average_focus_score === null ? '-' : Math.round(totals.average_focus_score)}
            </p>
            <p className="mt-3 text-sm text-[var(--color-muted)]">
              over {formatDuration(totals.monitored_seconds)} of monitored work, {windowLabel}
            </p>
            <dl className="mt-4 flex gap-6 text-sm">
              <div>
                <dt className="text-[var(--color-muted)]">Visual attention</dt>
                <dd className="mt-0.5 text-lg font-semibold">
                  {totals.visual_attention_score === null
                    ? '-'
                    : Math.round(totals.visual_attention_score)}
                </dd>
              </div>
              <div>
                <dt className="text-[var(--color-muted)]">Digital productivity</dt>
                <dd className="mt-0.5 text-lg font-semibold">
                  {totals.digital_productivity_score === null
                    ? '-'
                    : Math.round(totals.digital_productivity_score)}
                </dd>
              </div>
            </dl>
          </div>
        </section>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard
            label="Employees"
            value={data.total_employees}
            icon={<IconUsers size={16} />}
          />
          <StatCard
            label="Working now"
            value={data.employees_online}
            hint={`${data.employees_offline} offline`}
            icon={<IconBolt size={16} />}
          />
          <StatCard
            label="Active tasks"
            value={data.active_tasks}
            hint={`${data.open_tasks} open, ${data.completed_tasks_in_window} completed`}
            icon={<IconTasks size={16} />}
          />
          <StatCard
            label="Productive time"
            value={formatDuration(totals.productive_seconds)}
            icon={<IconCheck size={16} />}
          />
          <StatCard
            label="Distracting time"
            value={formatDuration(totals.distracting_seconds)}
            icon={<IconAlert size={16} />}
          />
          <StatCard
            label="Phone in view"
            value={formatDuration(totals.phone_seconds)}
            hint="webcam estimate"
            icon={<IconPhone size={16} />}
          />
        </div>
      </div>

      <Card
        title="Where the time went"
        subtitle="Computer activity, classified against each task's rules"
        icon={<IconChart size={18} />}
      >
        <ClassificationBar
          productive={totals.productive_seconds}
          neutral={totals.neutral_seconds}
          distracting={totals.distracting_seconds}
        />
        <p className="mt-3 text-xs text-[var(--color-muted)]">
          A site can be productive for one task and distracting for another - YouTube for a
          lecture versus during an API task.
        </p>
      </Card>

      <Card
        title="Employees"
        // The API fills each row with the selected window's totals, so the
        // caption follows the window rather than always saying "today".
        subtitle={`Monitored time and focus, ${windowLabel}`}
        icon={<IconUsers size={18} />}
      >
        {data.employees.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            No employees yet. <Link className="text-[var(--color-accent)]" to="/employees">Add one</Link>.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className={`${tableClass} rtable`}>
              <thead>
                <tr>
                  <th className={thClass}>Name</th>
                  <th className={thClass}>Status</th>
                  <th className={thClass}>Current task</th>
                  <th className={thClass}>Monitored</th>
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
                      <td data-label="" className={tdClass}>
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
                      <td data-label="Status" className={tdClass}>
                        <OnlineDot online={employee.online} />
                      </td>
                      <td data-label="Current task" className={tdClass}>
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
                      <td data-label="Monitored" className={`${tdClass} tabular-nums`}>
                        {formatDuration(today.monitored_seconds)}
                      </td>
                      <td data-label="Focus" className={tdClass}>
                        <Score value={today.average_focus_score} />
                      </td>
                      <td data-label="Productive" className={`${tdClass} tabular-nums`}>
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
                <CartesianGrid stroke="var(--color-grid)" vertical={false} />
                <XAxis
                  dataKey="day"
                  tickFormatter={(day: string) => day.slice(5)}
                  tick={{ fontSize: 12, fill: 'var(--color-muted)' }}
                  axisLine={{ stroke: 'var(--color-line)' }}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 100]}
                  ticks={[0, 25, 50, 75, 100]}
                  tick={{ fontSize: 12, fill: 'var(--color-muted)' }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: 'var(--color-grid)', opacity: 0.6 }}
                  formatter={(value) => [Math.round(Number(value)), 'Average focus']}
                  contentStyle={{
                    background: 'var(--color-surface)',
                    border: '1px solid var(--color-line)',
                    borderRadius: 12,
                    boxShadow: 'var(--shadow-lift)',
                  }}
                />
                <Bar
                  dataKey="average_focus_score"
                  fill="var(--color-focus-high)"
                  radius={[4, 4, 0, 0]}
                  maxBarSize={36}
                />
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
        <table className={`${tableClass} rtable`}>
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
                <td data-label="" className={tdClass}>{department.name}</td>
                <td data-label="People" className={tdClass}>{department.employees}</td>
                <td data-label="Monitored" className={`${tdClass} tabular-nums`}>
                  {formatDuration(department.monitored_seconds)}
                </td>
                <td data-label="Productive" className={`${tdClass} tabular-nums`}>
                  {formatDuration(department.productive_seconds)}
                </td>
                <td data-label="Distracting" className={`${tdClass} tabular-nums`}>
                  {formatDuration(department.distracting_seconds)}
                </td>
                <td data-label="Focus" className={tdClass}>
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
