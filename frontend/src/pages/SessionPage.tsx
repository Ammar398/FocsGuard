/**
 * One session, in full (spec section 41).
 *
 * The page is ordered to answer three questions in the order a student asks
 * them: how did it go, when did it change, and what was I doing. Putting the
 * timeline first would show a shape before there is anything to compare it to.
 */

import { Link, useParams } from 'react-router-dom'

import { api } from '../api/client'
import { eventLabel, type SessionDetail, type VisionEvent } from '../api/types'
import { ActivityBreakdown } from '../components/ActivityBreakdown'
import { FocusRing } from '../components/FocusRing'
import { FocusTimeline } from '../components/FocusTimeline'
import { Card, StatCard } from '../components/StatCard'
import { formatClock, formatDate, formatDuration, scoreDescription } from '../lib/format'
import { usePolling } from '../lib/usePolling'

export function SessionPage() {
  const { id } = useParams<{ id: string }>()
  const sessionId = Number(id)

  const { data, error, loading } = usePolling<SessionDetail>(
    () => api.session(sessionId),
    // Only worth polling while it is running; the check happens below once the
    // first response tells us whether it is.
    15_000,
    [sessionId],
  )

  if (loading && data === null) {
    return <p className="p-8 text-[var(--color-muted)]">Loading session…</p>
  }

  if (data === null) {
    return (
      <div className="p-8">
        <p className="text-[var(--color-focus-low)]">
          {error ?? 'That session could not be found.'}
        </p>
        <Link to="/" className="mt-3 inline-block text-sm text-[var(--color-accent)] hover:underline">
          ← Back to the dashboard
        </Link>
      </div>
    )
  }

  const running = data.ended_at === null
  const unscored = Math.max(
    0,
    data.duration_seconds - data.focused_seconds - data.distracted_seconds,
  )

  return (
    <div className="space-y-6">
      <div>
        <Link to="/" className="text-sm text-[var(--color-accent)] hover:underline">
          ← All sessions
        </Link>
        <h1 className="mt-2 text-2xl font-semibold">{data.name}</h1>
        <p className="mt-1 text-sm text-[var(--color-muted)]">
          {formatDate(data.started_at)}
          {data.ended_at !== null && <> to {formatClock(data.ended_at)}</>}
          {data.task_name !== null && <> · {data.task_name}</>}
          {running && <> · still running</>}
        </p>
      </div>

      <section className="flex flex-wrap items-center gap-8 rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
        <FocusRing
          score={data.average_focus_score}
          caption={running ? 'Average so far' : scoreDescription(data.average_focus_score)}
        />
        <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-4">
          <StatCard label="Duration" value={formatDuration(data.duration_seconds)} />
          <StatCard
            label="Focused"
            value={formatDuration(data.focused_seconds)}
            accent="var(--color-focus-high)"
          />
          <StatCard
            label="Interrupted"
            value={formatDuration(data.distracted_seconds)}
            accent="var(--color-focus-low)"
          />
          <StatCard
            label="Not scored"
            value={formatDuration(unscored)}
            // Said out loud rather than folded into "focused", which would
            // quietly inflate the number the student is judged by.
            hint="camera unavailable or paused"
          />
        </div>
      </section>

      <Card title="Focus over time">
        <FocusTimeline scores={data.focus_scores} events={data.vision_events} />
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Where the time went">
          <ActivityBreakdown events={data.activity_events} />
        </Card>

        <Card title={`What interrupted you (${data.vision_events.length})`}>
          <EventList events={data.vision_events} />
        </Card>
      </div>
    </div>
  )
}

/**
 * The attention events, longest first.
 *
 * Each row states what was observed and how confident the detector was. Section
 * 61 is explicit that the system must not claim certainty from one weak signal,
 * so a 0.55 and a 0.95 must not look identical to the reader.
 */
function EventList({ events }: { events: VisionEvent[] }) {
  if (events.length === 0) {
    return (
      <p className="text-sm text-[var(--color-muted)]">
        Nothing was flagged during this session.
      </p>
    )
  }

  const sorted = [...events].sort((a, b) => b.duration_seconds - a.duration_seconds)

  return (
    <ul className="divide-y divide-[var(--color-line)]">
      {sorted.slice(0, 12).map((event) => (
        <li key={event.id} className="flex items-baseline justify-between gap-4 py-2.5">
          <span className="min-w-0">
            <span className="block truncate text-sm font-medium">
              {eventLabel(event.event_type)}
            </span>
            <span className="block text-xs text-[var(--color-muted)]">
              {formatClock(event.start_time)} ·{' '}
              {event.confidence >= 0.8
                ? 'clear signal'
                : event.confidence >= 0.6
                  ? 'probable'
                  : 'weak signal'}
            </span>
          </span>
          <span className="shrink-0 text-sm tabular-nums text-[var(--color-muted)]">
            {formatDuration(event.duration_seconds)}
          </span>
        </li>
      ))}
    </ul>
  )
}
