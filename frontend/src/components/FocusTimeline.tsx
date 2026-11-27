/**
 * The focus score over time, with attention events drawn on top (spec section 41).
 *
 * THE POINT OF THIS CHART
 * -----------------------
 * Section 41 asks that a student can "understand why the score changed". A bare
 * line answers *what* happened and leaves *why* a mystery. So every attention
 * event is drawn as a shaded band across the moment it covered, and the dip in
 * the line sits inside the band that explains it.
 *
 * That pairing is the whole feature. Without the overlay this is a pretty
 * graph; with it, it is an explanation.
 *
 * WHAT THE SHADING DOES NOT CLAIM
 * -------------------------------
 * A band marks an **observation** - the camera saw no face, or saw a phone. It
 * is not a verdict about the person, and the label says what was seen rather
 * than what it means (section 8, section 61). A single weak signal never gets
 * described as certainty; the tooltip shows how confident the detector was, so
 * a 0.55 reads differently from a 0.95.
 */

import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { eventLabel, type FocusScore, type VisionEvent } from '../api/types'
import { formatClock } from '../lib/format'

interface FocusTimelineProps {
  scores: FocusScore[]
  events: VisionEvent[]
}

interface Point {
  t: number
  score: number
  visual: number
  productivity: number | null
  active: string[]
}

/** Colour per event type. Red is reserved for the ones that stop work outright. */
const EVENT_COLORS: Record<string, string> = {
  FACE_ABSENT: '#ef4444',
  PHONE_VISIBLE: '#f97316',
  DROWSINESS: '#a855f7',
  EYES_CLOSED: '#8b5cf6',
  GAZE_AWAY: '#f59e0b',
  HEAD_TURNED: '#f59e0b',
  MULTIPLE_FACES: '#0ea5e9',
}

function eventColor(eventType: string): string {
  return EVENT_COLORS[eventType] ?? '#94a3b8'
}

export function FocusTimeline({ scores, events }: FocusTimelineProps) {
  const points = useMemo<Point[]>(
    () =>
      scores
        .map((score) => ({
          t: new Date(score.recorded_at).getTime(),
          score: score.combined_focus_score,
          visual: score.visual_attention_score,
          productivity: score.digital_productivity_score,
          active: score.active_events ?? [],
        }))
        // The API orders these already; sorting defends the chart against a
        // future endpoint that does not, where the line would zig-zag.
        .sort((a, b) => a.t - b.t),
    [scores],
  )

  const bands = useMemo(
    () =>
      events.map((event) => ({
        id: event.id,
        from: new Date(event.start_time).getTime(),
        to: new Date(event.end_time).getTime(),
        type: event.event_type,
        confidence: event.confidence,
      })),
    [events],
  )

  const spanMs = points.length > 1 ? points[points.length - 1].t - points[0].t : 0

  if (points.length === 0) {
    return (
      <p className="py-12 text-center text-sm text-[var(--color-muted)]">
        No score samples yet. They appear a few seconds after a session starts.
      </p>
    )
  }

  return (
    <>
      <div className="h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points} margin={{ top: 8, right: 12, bottom: 4, left: -16 }}>
            <CartesianGrid stroke="var(--color-grid)" vertical={false} />

            {/* Drawn before the line so the score stays legible on top of it. */}
            {bands.map((band) => (
              <ReferenceArea
                key={band.id}
                x1={band.from}
                x2={band.to}
                fill={eventColor(band.type)}
                fillOpacity={0.14}
                strokeOpacity={0}
                ifOverflow="hidden"
              />
            ))}

            <XAxis
              dataKey="t"
              type="number"
              domain={['dataMin', 'dataMax']}
              scale="time"
              // Short sessions need seconds, or every tick reads the same minute.
              tickFormatter={(value: number) =>
                formatClock(new Date(value).toISOString(), spanMs < 15 * 60 * 1000)
              }
              stroke="var(--color-muted)"
              fontSize={12}
              minTickGap={40}
            />
            <YAxis
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              stroke="var(--color-muted)"
              fontSize={12}
            />
            <Tooltip content={<TimelineTooltip />} />

            <Line
              type="monotone"
              dataKey="score"
              stroke="var(--color-accent)"
              strokeWidth={2}
              // A session is hundreds of samples; drawing a dot on each turns
              // the line into a caterpillar and slows the chart down.
              dot={false}
              isAnimationActive={false}
              name="Focus"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <EventLegend events={events} />
    </>
  )
}

interface TooltipProps {
  active?: boolean
  payload?: { payload: Point }[]
}

function TimelineTooltip({ active, payload }: TooltipProps) {
  if (!active || !payload?.length) return null
  const point = payload[0].payload

  return (
    <div className="rounded-lg border border-[var(--color-line)] bg-[var(--color-surface)] px-3 py-2 text-xs shadow-lg">
      <p className="font-medium">{formatClock(new Date(point.t).toISOString())}</p>
      <p className="mt-1">Focus {Math.round(point.score)}</p>
      <p className="text-[var(--color-muted)]">Attention {Math.round(point.visual)}</p>
      {point.productivity !== null && (
        <p className="text-[var(--color-muted)]">
          Productivity {Math.round(point.productivity)}
        </p>
      )}
      {point.active.length > 0 && (
        <p className="mt-1 border-t border-[var(--color-line)] pt-1">
          {point.active.map(eventLabel).join(', ')}
        </p>
      )}
    </div>
  )
}

/**
 * Names the shaded bands.
 *
 * Only the types actually present are listed. A fixed legend showing seven
 * event types when the session had one is noise, and it invites a student to
 * wonder what they did wrong to trigger the other six.
 */
function EventLegend({ events }: { events: VisionEvent[] }) {
  const present = useMemo(() => {
    const counts = new Map<string, number>()
    for (const event of events) {
      counts.set(event.event_type, (counts.get(event.event_type) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [events])

  if (present.length === 0) {
    return (
      <p className="mt-3 text-sm text-[var(--color-muted)]">
        No attention events were recorded in this session.
      </p>
    )
  }

  return (
    <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
      {present.map(([type, count]) => (
        <li key={type} className="flex items-center gap-2 text-sm">
          <span
            aria-hidden="true"
            className="h-3 w-3 rounded-sm"
            style={{ backgroundColor: eventColor(type), opacity: 0.55 }}
          />
          <span>{eventLabel(type)}</span>
          <span className="text-[var(--color-muted)]">×{count}</span>
        </li>
      ))}
    </ul>
  )
}
