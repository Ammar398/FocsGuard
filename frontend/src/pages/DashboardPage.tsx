/**
 * The student's home screen (spec section 41).
 *
 * WHAT IT SHOWS FIRST
 * -------------------
 * If a session is running, that session - the ring, the timers, and what is on
 * screen right now. If not, the last one that finished. A dashboard that opens
 * on a list makes the student do a lookup before learning anything.
 *
 * WHY THERE IS NO "START SESSION" BUTTON
 * --------------------------------------
 * A web page cannot start a process on the machine, and pretending otherwise
 * would be a button that quietly does nothing. Monitoring is started from the
 * terminal, and the panel below says exactly how rather than hiding the fact.
 *
 * That is also the right boundary for a monitoring tool: the agent is started by
 * the person being monitored, on their own machine, deliberately. A dashboard
 * that could switch someone's webcam on remotely is a different product, and not
 * one this specification asks for.
 */

import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { SessionSummary } from '../api/types'
import { Card, StatCard } from '../components/StatCard'
import { FocusRing } from '../components/FocusRing'
import { ThisComputer } from '../components/ThisComputer'
import {
  formatDate,
  formatDuration,
  scoreColor,
  scoreDescription,
} from '../lib/format'
import { usePolling } from '../lib/usePolling'

/** Matched to the agent's upload interval; polling faster learns nothing new. */
const LIVE_POLL_MS = 15_000

function isRunning(session: SessionSummary): boolean {
  return session.ended_at === null
}

export function DashboardPage() {
  const { data, error, loading, refresh } = usePolling<SessionSummary[]>(
    () => api.sessions(50),
    // Polls faster while something is live. There is nothing to watch for
    // between sessions, so the idle rate is deliberately slow.
    LIVE_POLL_MS,
  )

  if (loading && data === null) {
    return <p className="p-8 text-[var(--color-muted)]">Loading…</p>
  }

  const sessions = data ?? []
  const live = sessions.find(isRunning)
  const current = live ?? sessions[0]

  return (
    <div className="space-y-6">
      <ThisComputer onSessionChange={refresh} />

      {error !== null && (
        <p
          role="status"
          className="rounded-lg border border-[var(--color-line)] px-4 py-2 text-sm text-[var(--color-muted)]"
        >
          Showing the last data received. {error}
        </p>
      )}

      {current === undefined ? (
        <NoSessionsYet />
      ) : (
        <CurrentSession session={current} live={live !== undefined} />
      )}

      <Card title={`Recent sessions (${sessions.length})`}>
        {sessions.length === 0 ? (
          <p className="text-sm text-[var(--color-muted)]">Nothing recorded yet.</p>
        ) : (
          <ul className="divide-y divide-[var(--color-line)]">
            {sessions.map((session) => (
              <SessionRow key={session.id} session={session} />
            ))}
          </ul>
        )}
      </Card>

      <CommandLine />
    </div>
  )
}

function CurrentSession({ session, live }: { session: SessionSummary; live: boolean }) {
  // A running session has no totals yet - the agent only computes them when the
  // session ends, because a number that is wrong now and corrected later is
  // worse than no number. So the ring shows the score, and the timers show what
  // is actually known.
  const focusedShare =
    session.duration_seconds > 0
      ? (session.focused_seconds / session.duration_seconds) * 100
      : 0

  return (
    <section className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-6">
      <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            {live && (
              <span className="flex items-center gap-1.5 text-xs font-medium text-[var(--color-focus-high)]">
                <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--color-focus-high)]" />
                LIVE
              </span>
            )}
            <h1 className="text-xl font-semibold">{session.name}</h1>
          </div>
          <p className="mt-1 text-sm text-[var(--color-muted)]">
            {session.task_name !== null && <>Working on {session.task_name} · </>}
            Started {formatDate(session.started_at)}
          </p>
        </div>

        <Link
          to={`/sessions/${session.id}`}
          className="rounded-lg border border-[var(--color-line)] px-3 py-1.5 text-sm font-medium hover:border-[var(--color-accent)]"
        >
          See the timeline →
        </Link>
      </header>

      <div className="flex flex-wrap items-center gap-8">
        <FocusRing
          score={session.average_focus_score}
          caption={
            live
              ? 'Average so far'
              : scoreDescription(session.average_focus_score)
          }
        />

        <div className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard label="Duration" value={formatDuration(session.duration_seconds)} />
          <StatCard
            label="Focused"
            value={formatDuration(session.focused_seconds)}
            hint={
              session.duration_seconds > 0 ? `${Math.round(focusedShare)}% of the session` : undefined
            }
            accent="var(--color-focus-high)"
          />
          <StatCard
            label="Interrupted"
            value={formatDuration(session.distracted_seconds)}
            accent="var(--color-focus-low)"
          />
        </div>
      </div>

      {live && (
        <p className="mt-6 border-t border-[var(--color-line)] pt-4 text-xs text-[var(--color-muted)]">
          Totals fill in when the session ends. The agent uploads every 30 seconds,
          so figures here can trail the last half-minute of work.
        </p>
      )}
    </section>
  )
}

function SessionRow({ session }: { session: SessionSummary }) {
  return (
    <li>
      <Link
        to={`/sessions/${session.id}`}
        className="flex items-center gap-4 py-3 hover:opacity-80"
      >
        <span
          aria-hidden="true"
          className="h-9 w-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: scoreColor(session.average_focus_score) }}
        />

        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{session.name}</span>
          <span className="block text-xs text-[var(--color-muted)]">
            {formatDate(session.started_at)} · {formatDuration(session.duration_seconds)}
            {isRunning(session) && ' · running'}
          </span>
        </span>

        <span
          className="shrink-0 text-lg font-semibold tabular-nums"
          style={{ color: scoreColor(session.average_focus_score) }}
        >
          {Math.round(session.average_focus_score)}
        </span>
      </Link>
    </li>
  )
}

function NoSessionsYet() {
  return (
    <section className="rounded-xl border border-dashed border-[var(--color-line)] p-10 text-center">
      <h1 className="text-lg font-semibold">No sessions yet</h1>
      <p className="mx-auto mt-2 max-w-md text-sm text-[var(--color-muted)]">
        Connect this computer above and start a session; it appears here. Everything
        is recorded on your computer first, so nothing is lost if you work offline.
      </p>
    </section>
  )
}

/**
 * For anyone who prefers a terminal. Collapsed: the panel above does all of
 * this with buttons, and most people never need it.
 */
function CommandLine() {
  return (
    <details className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4 text-sm">
      <summary className="cursor-pointer font-medium text-[var(--color-muted)]">
        Prefer the command line?
      </summary>
      <pre className="mt-3 overflow-x-auto rounded-lg bg-[var(--color-canvas)] p-3 text-xs">
        <code>{`python -m focusguard_agent.agent --name "Study session"   # start
python -m focusguard_agent.tools.stop                      # stop`}</code>
      </pre>
    </details>
  )
}
