import type { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: ReactNode
  hint?: string
  /** Colour of the value. Use only where the colour *means* something (a score band). */
  accent?: string
  icon?: ReactNode
}

/**
 * One number, labelled - the stat-tile contract from the dataviz skill:
 * a sentence-case label, a semibold value in proportional figures (tabular
 * figures make large numbers look loose), and an optional hint line.
 */
export function StatCard({ label, value, hint, accent, icon }: StatCardProps) {
  return (
    <div className="fg-rise rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4 shadow-[var(--shadow-card)]">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-medium text-[var(--color-muted)]">{label}</p>
        {icon && (
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
            {icon}
          </span>
        )}
      </div>
      <p
        className="mt-2 text-[1.65rem] leading-tight font-semibold tracking-tight"
        style={accent ? { color: accent } : undefined}
      >
        {value}
      </p>
      {hint !== undefined && (
        <p className="mt-1 text-xs text-[var(--color-muted)]">{hint}</p>
      )}
    </div>
  )
}

export function Card({
  title,
  children,
  action,
  icon,
  subtitle,
}: {
  title: string
  children: ReactNode
  action?: ReactNode
  icon?: ReactNode
  subtitle?: string
}) {
  return (
    <section className="fg-rise rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-5 shadow-[var(--shadow-card)] sm:p-6">
      <header className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          {icon && (
            <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--color-accent-soft)] text-[var(--color-accent)]">
              {icon}
            </span>
          )}
          <div>
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
            {subtitle && <p className="mt-0.5 text-sm text-[var(--color-muted)]">{subtitle}</p>}
          </div>
        </div>
        {action}
      </header>
      {children}
    </section>
  )
}
