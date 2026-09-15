import type { ReactNode } from 'react'

interface StatCardProps {
  label: string
  value: ReactNode
  hint?: string
  accent?: string
}

/** One number, labelled. The unit of the dashboard's top row. */
export function StatCard({ label, value, hint, accent }: StatCardProps) {
  return (
    <div className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-4">
      <p className="text-xs font-medium tracking-wide text-[var(--color-muted)] uppercase">
        {label}
      </p>
      <p
        className="mt-1 text-2xl font-semibold"
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
}: {
  title: string
  children: ReactNode
  action?: ReactNode
}) {
  return (
    <section className="rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)] p-5">
      <header className="mb-4 flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold tracking-wide text-[var(--color-muted)] uppercase">
          {title}
        </h2>
        {action}
      </header>
      {children}
    </section>
  )
}
