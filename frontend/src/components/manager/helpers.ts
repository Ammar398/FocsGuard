/**
 * Non-component helpers for the manager portal, kept apart from `ui.tsx` so
 * React Fast Refresh can hot-reload the components (see auth/context.ts).
 */

export const WINDOWS = [
  { days: 1, label: 'Today' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
]

/** "figma.com, VS Code\nslack" -> ["figma.com", "VS Code", "slack"] */
export function parseRules(text: string): string[] {
  return text
    .split(/[,\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${Math.round(value * 100)}%`
}

export const tableClass = 'w-full text-left text-sm'
export const thClass =
  'border-b border-[var(--color-line)] pb-2 text-xs font-medium tracking-wide text-[var(--color-muted)] uppercase'
export const tdClass = 'border-b border-[var(--color-line)] py-2.5 pr-3 align-top'
