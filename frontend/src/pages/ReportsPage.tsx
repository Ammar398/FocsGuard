/**
 * Daily and weekly reports, with suggestions and export (spec sections 42, 43).
 *
 * WHY ONE PAGE AND NOT TWO
 * ------------------------
 * "Daily" and "weekly" differ only in the window, so they are a control on one
 * page rather than two routes. Two pages would be two places to fix a bug, and
 * the student would have to learn which one to open.
 *
 * WHY BARS AND NOT A LINE
 * -----------------------
 * A line implies continuity between points. Days are discrete, and a week off
 * should read as gaps, not as a slope drawn through nothing. The backend returns
 * empty days explicitly for the same reason.
 */

import { useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ApiError, api } from '../api/client'
import type { DayTotals, RecommendationItem, ReportSummary } from '../api/types'
import { Card, StatCard } from '../components/StatCard'
import { RecommendationCard } from '../components/RecommendationCard'
import { formatDuration, scoreColor, scoreDescription } from '../lib/format'
import { usePolling } from '../lib/usePolling'

const WINDOWS = [
  { days: 1, label: 'Today' },
  { days: 7, label: 'This week' },
  { days: 30, label: 'This month' },
]

export function ReportsPage() {
  const [days, setDays] = useState(7)

  const report = usePolling<ReportSummary>(() => api.report(days), null, [days])
  const advice = usePolling<RecommendationItem[]>(
    () => api.recommendations(days),
    null,
    [days],
  )

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <h1 className="text-2xl font-semibold">Your report</h1>
        <div className="flex gap-1 rounded-lg border border-[var(--color-line)] p-1">
          {WINDOWS.map((option) => (
            <button
              key={option.days}
              type="button"
              onClick={() => setDays(option.days)}
              aria-pressed={days === option.days}
              className={
                days === option.days
                  ? 'rounded-md bg-[var(--color-accent)] px-3 py-1.5 text-sm font-medium text-white'
                  : 'rounded-md px-3 py-1.5 text-sm font-medium hover:opacity-70'
              }
            >
              {option.label}
            </button>
          ))}
        </div>
      </header>

      {report.loading && report.data === null ? (
        <p className="p-8 text-[var(--color-muted)]">Loading…</p>
      ) : report.data === null ? (
        <p className="text-[var(--color-focus-low)]">
          {report.error ?? 'Could not load the report.'}
        </p>
      ) : (
        <>
          <Totals summary={report.data} />
          <Card title="Focus by day">
            <DailyChart daily={report.data.daily} />
          </Card>
        </>
      )}

      <section>
        <h2 className="mb-3 text-sm font-semibold tracking-wide text-[var(--color-muted)] uppercase">
          Suggestions
        </h2>
        {advice.data === null ? (
          <p className="text-sm text-[var(--color-muted)]">Loading…</p>
        ) : advice.data.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">
            Nothing stands out in this period.
          </p>
        ) : (
          <div className="space-y-3">
            {advice.data.map((item) => (
              <RecommendationCard key={item.rule_id} item={item} />
            ))}
          </div>
        )}
      </section>

      <ExportPanel days={days} />
    </div>
  )
}

function Totals({ summary }: { summary: ReportSummary }) {
  const focusedShare =
    summary.monitored_seconds > 0
      ? (summary.focused_seconds / summary.monitored_seconds) * 100
      : 0

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard label="Sessions" value={summary.sessions} />
      <StatCard label="Monitored" value={formatDuration(summary.monitored_seconds)} />
      <StatCard
        label="Focused"
        value={formatDuration(summary.focused_seconds)}
        hint={summary.monitored_seconds > 0 ? `${Math.round(focusedShare)}%` : undefined}
        accent="var(--color-focus-high)"
      />
      <StatCard
        label="Average focus"
        value={Math.round(summary.average_focus_score)}
        hint={
          summary.sessions > 0 ? scoreDescription(summary.average_focus_score) : 'no data'
        }
        accent={scoreColor(summary.average_focus_score)}
      />
    </div>
  )
}

function DailyChart({ daily }: { daily: DayTotals[] }) {
  if (daily.every((day) => day.sessions === 0)) {
    return (
      <p className="py-10 text-center text-sm text-[var(--color-muted)]">
        No sessions recorded in this period.
      </p>
    )
  }

  const data = daily.map((day) => ({
    ...day,
    minutes: day.monitored_seconds / 60,
    focusedMinutes: day.focused_seconds / 60,
    label: new Date(`${day.day}T00:00:00`).toLocaleDateString(undefined, {
      weekday: 'short',
      day: 'numeric',
    }),
  }))

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: -20 }}>
          {/* Solid hairline grid: dashed lines read as thresholds (dataviz skill). */}
          <CartesianGrid stroke="var(--color-grid)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 12, fill: 'var(--color-muted)' }}
            axisLine={{ stroke: 'var(--color-line)' }}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 12, fill: 'var(--color-muted)' }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(value: number) => `${Math.round(value)}m`}
          />
          <Tooltip content={<DayTooltip />} cursor={{ fill: 'var(--color-grid)', opacity: 0.6 }} />
          {/* One series, one colour. The day's focus score is in the tooltip and
              the table below, not painted onto the bar: colouring bars by value
              double-encodes and burns the colour channel (dataviz skill). */}
          <Bar
            dataKey="minutes"
            fill="var(--color-focus-high)"
            radius={[4, 4, 0, 0]}
            maxBarSize={36}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

interface DayTooltipProps {
  active?: boolean
  payload?: { payload: DayTotals & { minutes: number; focusedMinutes: number } }[]
}

function DayTooltip({ active, payload }: DayTooltipProps) {
  if (!active || !payload?.length) return null
  const day = payload[0].payload

  if (day.sessions === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-xs shadow-lg">
        <p className="font-medium">{day.day}</p>
        <p className="text-[var(--color-muted)]">No sessions</p>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-xs shadow-lg">
      <p className="font-medium">{day.day}</p>
      <p className="mt-1">
        {day.sessions} session{day.sessions === 1 ? '' : 's'}
      </p>
      <p className="text-[var(--color-muted)]">
        {formatDuration(day.monitored_seconds)} monitored
      </p>
      <p className="text-[var(--color-muted)]">
        {formatDuration(day.focused_seconds)} focused
      </p>
      <p className="mt-1" style={{ color: scoreColor(day.average_focus_score) }}>
        Average focus {Math.round(day.average_focus_score)}
      </p>
    </div>
  )
}

/**
 * Export (spec section 43).
 *
 * Framed as taking your data with you rather than as a feature, because that is
 * what it is: a person being measured should be able to keep the measurements,
 * check them, and still have them if they stop using the tool.
 */
function ExportPanel({ days }: { days: number }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function download() {
    setBusy(true)
    setError(null)
    try {
      await api.exportCsv(days)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Export failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Your data">
      <p className="text-sm text-[var(--color-muted)]">
        Download every session in this period as a spreadsheet. It is your data,
        and this works whether or not you keep using FocusGuard.
      </p>
      <p className="mt-2 text-xs text-[var(--color-muted)]">
        Sessions and their totals. No webcam data, no screenshots, no web
        addresses — none of that is ever recorded, so none of it can be exported.
      </p>

      <button
        type="button"
        onClick={() => void download()}
        disabled={busy}
        className="mt-3 rounded-lg border border-[var(--color-line)] px-3 py-1.5 text-sm font-medium hover:border-[var(--color-accent)] disabled:opacity-50"
      >
        {busy ? 'Preparing…' : 'Download CSV'}
      </button>
      <button
        type="button"
        onClick={() => window.print()}
        className="no-print mt-3 ml-2 rounded-lg border border-[var(--color-line)] px-3 py-1.5 text-sm font-medium hover:border-[var(--color-accent)]"
      >
        Print / Save as PDF
      </button>

      {error !== null && (
        <p role="alert" className="mt-2 text-sm text-[var(--color-focus-low)]">
          {error}
        </p>
      )}
    </Card>
  )
}
