/** Weekly work times on tasks: "Mon–Fri 09:00 for 4 h". */

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const

export interface Schedule {
  /** 0 = Monday ... 6 = Sunday */
  days: number[]
  /** "HH:MM", on the employee's own clock */
  start: string
  minutes: number
}

export function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `${hours} h ${rest} min` : `${hours} h`
}

export function formatDays(days: number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b)
  if (sorted.length === 7) return 'Every day'
  if (sorted.join() === '0,1,2,3,4') return 'Mon–Fri'
  // Runs of three or more consecutive days read better as a range.
  const parts: string[] = []
  let index = 0
  while (index < sorted.length) {
    let end = index
    while (end + 1 < sorted.length && sorted[end + 1] === sorted[end] + 1) end += 1
    if (end - index >= 2) parts.push(`${WEEKDAYS[sorted[index]]}–${WEEKDAYS[sorted[end]]}`)
    else for (let i = index; i <= end; i += 1) parts.push(WEEKDAYS[sorted[i]])
    index = end + 1
  }
  return parts.join(', ')
}

export function formatSchedule(schedule: Schedule): string {
  return `${formatDays(schedule.days)} ${schedule.start} for ${formatDuration(schedule.minutes)}`
}

export function endTime(start: string, minutes: number): string {
  const [hour, minute] = start.split(':').map(Number)
  const total = hour * 60 + minute + minutes
  return `${String(Math.floor(total / 60) % 24).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}
