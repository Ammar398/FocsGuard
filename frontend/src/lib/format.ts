/**
 * Formatting shared across the app.
 *
 * In one place because the same duration must read identically on the dashboard
 * card and inside the session view. Two implementations drift, and a student who
 * sees "1h 5m" in one place and "65 min" in another stops trusting either.
 */

/** "1h 05m", "7m 30s", "12s". Never "0.11666 hours". */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))

  if (total < 60) return `${total}s`

  const minutes = Math.floor(total / 60)
  if (minutes < 60) {
    const remainder = total % 60
    return remainder === 0 ? `${minutes}m` : `${minutes}m ${String(remainder).padStart(2, '0')}s`
  }

  const hours = Math.floor(minutes / 60)
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
}

export function formatClock(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  })
}

export function formatDate(iso: string): string {
  const date = new Date(iso)
  const today = new Date()
  const sameDay =
    date.getFullYear() === today.getFullYear() &&
    date.getMonth() === today.getMonth() &&
    date.getDate() === today.getDate()

  if (sameDay) return `Today, ${formatClock(iso)}`
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * The colour a focus score is drawn in.
 *
 * Bands, not a gradient, and the thresholds live here alone so the ring, the
 * timeline and the score text can never disagree about what "good" looks like.
 * Matches the agent's own bands in `scoring/engine.py`.
 */
export function scoreColor(score: number): string {
  if (score >= 70) return 'var(--color-focus-high)'
  if (score >= 40) return 'var(--color-focus-mid)'
  return 'var(--color-focus-low)'
}

/**
 * A plain description of a score.
 *
 * Deliberately about the session, never about the person (spec section 8). "A
 * distracted session" describes data; "you are easily distracted" would be a
 * claim about someone's character that this project has no business making.
 */
export function scoreDescription(score: number): string {
  if (score >= 85) return 'Very focused session'
  if (score >= 70) return 'Focused session'
  if (score >= 55) return 'Mixed session'
  if (score >= 40) return 'Often interrupted'
  return 'Frequently interrupted'
}

export function classificationColor(classification: string): string {
  switch (classification) {
    case 'PRODUCTIVE':
      return 'var(--color-focus-high)'
    case 'DISTRACTING':
      return 'var(--color-focus-low)'
    default:
      return 'var(--color-muted)'
  }
}

/** "Visual Studio Code" or "youtube.com" - whichever the row actually has. */
export function activityLabel(applicationName: string, domain: string | null): string {
  return domain ?? applicationName
}
