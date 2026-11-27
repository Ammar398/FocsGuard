/**
 * One suggestion, with the data that produced it (spec section 9).
 *
 * THE EVIDENCE IS NOT A TOOLTIP
 * -----------------------------
 * Section 9 requires the system to explain why. Hiding the numbers behind a
 * hover or a "details" toggle technically satisfies that and practically does
 * not - almost nobody expands it, so almost nobody sees the reasoning.
 *
 * So the evidence is rendered inline, always, as the numbers that fired the
 * rule. It is also what makes disagreement possible: "51 minutes on youtube.com"
 * is checkable, and a student who knows those were lecture recordings can go and
 * reclassify the domain. Advice without its evidence is just an instruction.
 */

import type { RecommendationItem } from '../api/types'

const KIND_ICONS: Record<string, string> = {
  ENVIRONMENT: '🪑',
  SCHEDULE: '🕐',
  BREAKS: '⏸',
  DIGITAL_HABITS: '🌐',
  REST: '😴',
  ENCOURAGEMENT: '✅',
}

const PRIORITY_COLORS: Record<string, string> = {
  HIGH: 'var(--color-focus-low)',
  MEDIUM: 'var(--color-focus-mid)',
  LOW: 'var(--color-focus-high)',
}

export function RecommendationCard({ item }: { item: RecommendationItem }) {
  const accent = PRIORITY_COLORS[item.priority] ?? 'var(--color-muted)'

  return (
    <article className="overflow-hidden rounded-xl border border-[var(--color-line)] bg-[var(--color-surface)]">
      <div className="flex gap-3 p-4">
        <span
          aria-hidden="true"
          className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-lg"
          style={{ backgroundColor: `color-mix(in srgb, ${accent} 15%, transparent)` }}
        >
          {KIND_ICONS[item.kind] ?? '•'}
        </span>

        <div className="min-w-0">
          <h3 className="font-semibold">{item.title}</h3>
          <p className="mt-1 text-sm text-[var(--color-muted)]">{item.detail}</p>
          {item.tips && item.tips.length > 0 && (
            <div className="mt-3">
              <p className="text-xs font-semibold tracking-wide text-[var(--color-accent)] uppercase">
                Try this
              </p>
              <ul className="mt-1.5 space-y-1.5">
                {item.tips.map((tip) => (
                  <li key={tip} className="flex gap-2 text-sm leading-relaxed">
                    <span
                      aria-hidden="true"
                      className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-accent)]"
                    />
                    <span>{tip}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {/* Always visible. See the note above about tooltips. */}
      <div className="border-t border-[var(--color-line)] bg-[var(--color-canvas)] px-4 py-3">
        <p className="mb-2 text-xs font-medium text-[var(--color-muted)]">
          Why you are seeing this
        </p>
        <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          {item.evidence.map((fact) => (
            <div key={fact.label} className="flex justify-between gap-3 text-sm">
              <dt className="text-[var(--color-muted)]">{fact.label}</dt>
              <dd className="shrink-0 font-medium tabular-nums">{fact.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </article>
  )
}
