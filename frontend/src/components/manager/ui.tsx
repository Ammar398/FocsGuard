/**
 * Small building blocks for the manager portal.
 *
 * They reuse the student palette on purpose: productive / neutral / distracting
 * are the same three colours everywhere, so a manager and the employee reading
 * their own report see the same thing mean the same thing.
 */

import type { ReactNode } from 'react'

import type { UsageRow } from '../../api/managerTypes'
import { WINDOWS } from './helpers'
import { classificationColor, formatDuration, scoreColor } from '../../lib/format'


export function WindowToggle({
  days,
  onChange,
}: {
  days: number
  onChange: (days: number) => void
}) {
  return (
    <div className="no-print flex gap-1 rounded-full border border-[var(--color-line)] bg-[var(--color-surface)] p-1 shadow-[var(--shadow-card)]">
      {WINDOWS.map((option) => (
        <button
          key={option.days}
          type="button"
          onClick={() => onChange(option.days)}
          aria-pressed={days === option.days}
          className={`rounded-full px-3.5 py-1 text-sm font-medium transition ${
            days === option.days
              ? 'text-white'
              : 'text-[var(--color-muted)] hover:text-[var(--color-ink)]'
          }`}
          style={days === option.days ? { background: 'var(--brand-gradient)' } : undefined}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** A score, or an honest dash when nothing was measured. */
export function Score({ value, suffix = '' }: { value: number | null | undefined; suffix?: string }) {
  if (value === null || value === undefined) {
    return <span className="text-[var(--color-muted)]">—</span>
  }
  return (
    <span className="tabular-nums" style={{ color: scoreColor(value) }}>
      {Math.round(value)}
      {suffix}
    </span>
  )
}

export function OnlineDot({ online }: { online: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-muted)]">
      <span
        className="inline-block h-2 w-2 rounded-full"
        style={{ backgroundColor: online ? 'var(--color-focus-high)' : 'var(--color-line)' }}
        aria-hidden
      />
      {online ? 'Working' : 'Offline'}
    </span>
  )
}

/**
 * Productive / neutral / distracting: the dataviz skill's tall segmented bar.
 *
 * One whole split into parts - 28 px, each part its own block with 4 px corners
 * and a 2 px gap, its share printed inside when the block is wide enough. The
 * legend names the parts in block order with their durations, and the bar's
 * accessible name reads every part, so colour never carries meaning alone.
 * Productive is blue and distracting red: a validated pair that stays apart
 * under colour blindness, unlike green and red.
 */
export function ClassificationBar({
  productive,
  neutral,
  distracting,
}: {
  productive: number
  neutral: number
  distracting: number
}) {
  const total = productive + neutral + distracting
  if (total <= 0) {
    return <p className="text-sm text-[var(--color-muted)]">No computer activity recorded.</p>
  }
  const parts = [
    { label: 'Productive', seconds: productive, color: 'var(--color-focus-high)', ink: '#ffffff' },
    { label: 'Neutral', seconds: neutral, color: 'var(--color-neutral-data)', ink: '#0b0b0b' },
    { label: 'Distracting', seconds: distracting, color: 'var(--color-focus-low)', ink: '#ffffff' },
  ].filter((part) => part.seconds > 0)

  const share = (seconds: number) => Math.round((seconds / total) * 100)
  const summary = parts
    .map((part) => `${part.label} ${formatDuration(part.seconds)}, ${share(part.seconds)}%`)
    .join('; ')

  return (
    <div>
      <div className="flex h-7 gap-[2px]" role="img" aria-label={summary}>
        {parts.map((part) => (
          <div
            key={part.label}
            className="flex items-center overflow-hidden rounded-[4px] px-2 text-xs font-semibold"
            style={{
              flexGrow: part.seconds,
              flexBasis: 0,
              backgroundColor: part.color,
              color: part.ink,
            }}
            title={`${part.label}: ${formatDuration(part.seconds)} (${share(part.seconds)}%)`}
          >
            {/* Printed only where it fits; otherwise the legend and title carry it. */}
            {part.seconds / total >= 0.1 ? `${share(part.seconds)}%` : ''}
          </div>
        ))}
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {parts.map((part) => (
          <li key={part.label} className="flex items-center gap-2">
            <span
              className="inline-block h-2.5 w-2.5 rounded-[3px]"
              style={{ backgroundColor: part.color }}
              aria-hidden="true"
            />
            {part.label}
            <span className="text-[var(--color-muted)]">{formatDuration(part.seconds)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Applications or domains, longest first, coloured by their classification. */
export function UsageList({ rows, empty }: { rows: UsageRow[]; empty: string }) {
  if (rows.length === 0) {
    return <p className="text-sm text-[var(--color-muted)]">{empty}</p>
  }
  const longest = rows[0].seconds || 1
  return (
    <ul className="space-y-3">
      {rows.map((row) => (
        <li key={row.label}>
          <div className="flex items-baseline justify-between gap-4 text-sm">
            <span className="truncate font-medium">{row.label}</span>
            <span className="shrink-0 tabular-nums text-[var(--color-muted)]">
              {formatDuration(row.seconds)}
            </span>
          </div>
          <div className="mt-1 h-2 overflow-hidden rounded-full bg-[var(--color-line)]">
            <div
              className="h-full rounded-full"
              style={{
                width: `${Math.max(2, (row.seconds / longest) * 100)}%`,
                backgroundColor: classificationColor(row.classification),
              }}
            />
          </div>
          <p className="mt-0.5 text-xs text-[var(--color-muted)]">
            {row.classification.toLowerCase()}
          </p>
        </li>
      ))}
    </ul>
  )
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p
      role="alert"
      className="rounded-lg bg-[var(--color-focus-low)]/10 px-3 py-2 text-sm text-[var(--color-focus-low)]"
    >
      {message}
    </p>
  )
}

export function Button({
  children,
  onClick,
  type = 'button',
  variant = 'secondary',
  disabled,
}: {
  children: ReactNode
  onClick?: () => void
  type?: 'button' | 'submit'
  variant?: 'primary' | 'secondary' | 'danger'
  disabled?: boolean
}) {
  const styles = {
    primary: 'text-white shadow-[var(--shadow-card)] hover:brightness-110',
    secondary:
      'border border-[var(--color-line)] bg-[var(--color-surface)] hover:border-[var(--color-accent)] hover:text-[var(--color-accent)]',
    danger:
      'border border-[var(--color-focus-low)]/40 text-[var(--color-focus-low)] hover:bg-[var(--color-focus-low)]/10',
  }[variant]
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`no-print rounded-xl px-3.5 py-1.5 text-sm font-medium transition disabled:opacity-50 ${styles}`}
      style={variant === 'primary' ? { background: 'var(--brand-gradient)' } : undefined}
    >
      {children}
    </button>
  )
}

/** Opens the browser's print dialog, where "Save as PDF" is the PDF export (§52). */
export function PrintButton() {
  return <Button onClick={() => window.print()}>Print / Save as PDF</Button>
}

const inputClass =
  'mt-1 w-full rounded-lg border border-[var(--color-line)] bg-[var(--color-canvas)] px-3 py-2 text-sm outline-none focus:border-[var(--color-accent)]'

export function TextField({
  label,
  value,
  onChange,
  type = 'text',
  required,
  placeholder,
  hint,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  type?: string
  required?: boolean
  placeholder?: string
  hint?: string
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <input
        type={type}
        value={value}
        required={required}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      />
      {hint && <span className="mt-1 block text-xs text-[var(--color-muted)]">{hint}</span>}
    </label>
  )
}

export function SelectField({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  children: ReactNode
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      >
        {children}
      </select>
    </label>
  )
}

export function TextArea({
  label,
  value,
  onChange,
  hint,
  rows = 2,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  hint?: string
  rows?: number
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium">{label}</span>
      <textarea
        value={value}
        rows={rows}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      />
      {hint && <span className="mt-1 block text-xs text-[var(--color-muted)]">{hint}</span>}
    </label>
  )
}

export function StatusPill({ status }: { status: string }) {
  const color =
    status === 'COMPLETED'
      ? 'var(--color-focus-high)'
      : status === 'IN_PROGRESS'
        ? 'var(--color-accent)'
        : status === 'CANCELLED'
          ? 'var(--color-muted)'
          : 'var(--color-focus-mid)'
  return (
    <span
      className="inline-block rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color, backgroundColor: `color-mix(in srgb, ${color} 14%, transparent)` }}
    >
      {status.replace(/_/g, ' ').toLowerCase()}
    </span>
  )
}
