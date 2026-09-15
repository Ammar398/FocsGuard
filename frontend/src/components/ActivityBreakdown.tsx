/**
 * Where the time went: applications and domains, longest first.
 *
 * WHAT IS SHOWN, AND WHAT CANNOT BE
 * ---------------------------------
 * An application name, or a bare domain. Never a page title, never a full
 * address, never a search term - the browser extension minimises a URL to its
 * domain *before* it leaves the browser (sections 20, 35), and nothing
 * downstream ever had more than that.
 *
 * CLASSIFICATION IS SHOWN, NOT ASSERTED
 * -------------------------------------
 * Each row carries the rule that classified it, because "YouTube was
 * distracting" is a judgement a student is entitled to disagree with - and
 * YouTube genuinely is work for some tasks (section 21). Showing the source
 * turns an accusation into a statement about a rule the user can change.
 */

import { useMemo } from 'react'

import type { ActivityEvent } from '../api/types'
import { activityLabel, classificationColor, formatDuration } from '../lib/format'

interface Row {
  key: string
  label: string
  seconds: number
  classification: string
  ruleSource: string
}

const RULE_SOURCE_LABELS: Record<string, string> = {
  TASK_RULE: 'your task settings',
  USER_RULE: 'your own rule',
  DEFAULT_CATALOG: 'the default catalogue',
  CATEGORY_DEFAULT: 'its category',
  UNKNOWN: 'no matching rule',
}

function ruleSourceLabel(source: string): string {
  return RULE_SOURCE_LABELS[source] ?? source.replace(/_/g, ' ').toLowerCase()
}

export function ActivityBreakdown({ events }: { events: ActivityEvent[] }) {
  const rows = useMemo<Row[]>(() => {
    const totals = new Map<string, Row>()

    for (const event of events) {
      const label = activityLabel(event.application_name, event.domain)
      const existing = totals.get(label)

      if (existing) {
        existing.seconds += event.duration_seconds
      } else {
        totals.set(label, {
          key: label,
          label,
          seconds: event.duration_seconds,
          classification: event.classification,
          ruleSource: event.rule_source,
        })
      }
    }

    return [...totals.values()].sort((a, b) => b.seconds - a.seconds)
  }, [events])

  if (rows.length === 0) {
    return (
      <p className="text-sm text-[var(--color-muted)]">
        No application activity was recorded for this session.
      </p>
    )
  }

  const longest = rows[0].seconds

  return (
    <ul className="space-y-3">
      {rows.slice(0, 8).map((row) => (
        <li key={row.key}>
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="truncate font-medium">{row.label}</span>
            <span className="shrink-0 tabular-nums text-[var(--color-muted)]">
              {formatDuration(row.seconds)}
            </span>
          </div>

          <div
            className="mt-1 h-2 overflow-hidden rounded-full bg-[var(--color-line)]"
            role="presentation"
          >
            <div
              className="h-full rounded-full"
              style={{
                // Relative to the longest row, so the shape of the session is
                // readable even when everything is short.
                width: `${Math.max(2, (row.seconds / longest) * 100)}%`,
                backgroundColor: classificationColor(row.classification),
              }}
            />
          </div>

          <p className="mt-1 text-xs text-[var(--color-muted)]">
            {row.classification.toLowerCase()} · from {ruleSourceLabel(row.ruleSource)}
          </p>
        </li>
      ))}
    </ul>
  )
}
