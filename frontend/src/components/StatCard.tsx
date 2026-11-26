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
    <div className="rounded-2xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
      <p className="flex items-center gap-2 text-sm text-[var(--color-muted)]">
        {/* Meaning colour sits on a small mark beside the label; the value stays
            in ink (dataviz: text wears text tokens). Icons are quiet. */}
        {accent ? (
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: accent }} aria-hidden="true" />
        ) : (
          icon && <span className="text-[var(--color-muted)] [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
        )}
        {label}
      </p>
      <p className="mt-2 text-[1.65rem] leading-tight font-semibold [font-stretch:108%]">{value}</p>
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
          {icon && <span className="mt-0.5 text-[var(--color-accent)]">{icon}</span>}
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
