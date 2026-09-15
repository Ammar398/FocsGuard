/**
 * The focus score, as a ring (spec section 41).
 *
 * WHY AN SVG AND NOT A CHART LIBRARY
 * ----------------------------------
 * Recharts earns its place on the timeline, where axes, tooltips and a brush are
 * real work. A ring is two circles and a dash offset. Pulling a chart component
 * in for that would cost more than it saves and would be harder to control.
 *
 * ACCESSIBILITY
 * -------------
 * The ring is `role="img"` with a label that states the number, because the
 * visual is a shape and a colour - neither of which a screen reader can read.
 * The colour is never the only carrier of meaning: the number is always printed
 * in the middle, which also matters for the roughly one in twelve men with a
 * red-green colour deficiency, for whom the "good" and "bad" ends of this
 * palette are the hardest pair to tell apart.
 */

import { scoreColor, scoreDescription } from '../lib/format'

interface FocusRingProps {
  score: number
  size?: number
  /** Hidden when the ring is a small inline indicator. */
  caption?: string
}

export function FocusRing({ score, size = 180, caption }: FocusRingProps) {
  const bounded = Math.max(0, Math.min(100, score))
  const stroke = Math.max(8, Math.round(size * 0.075))
  const radius = (size - stroke) / 2
  const circumference = 2 * Math.PI * radius

  // The dash gap is the unfilled part, so the offset is the remainder.
  const offset = circumference * (1 - bounded / 100)
  const color = scoreColor(bounded)

  return (
    <div className="flex flex-col items-center gap-2">
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        role="img"
        aria-label={`Focus score ${Math.round(bounded)} out of 100. ${scoreDescription(bounded)}.`}
      >
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-line)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          // Starts at twelve o'clock instead of three, which is where people
          // expect a progress ring to begin.
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
          style={{ transition: 'stroke-dashoffset 600ms ease, stroke 600ms ease' }}
        />
        <text
          x="50%"
          y="50%"
          textAnchor="middle"
          dominantBaseline="central"
          fill="var(--color-ink)"
          fontSize={size * 0.26}
          fontWeight={600}
        >
          {Math.round(bounded)}
        </text>
      </svg>

      {caption !== undefined && (
        <p className="text-sm text-[var(--color-muted)]">{caption}</p>
      )}
    </div>
  )
}
