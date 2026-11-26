/**
 * The FocusGuard aperture - the logo, and the focus gauge.
 *
 * A ring, six swept blades and a central pupil, drawn from geometry rather than
 * an image so it stays sharp at 16 px and at 260 px.
 *
 * AS A GAUGE
 * ----------
 * Give it a `score` and the iris responds: the more focused the session, the
 * wider the blades open and the larger the bright pupil. It is the one bold,
 * moving element in the interface (frontend-design: spend boldness in one
 * place), so it animates once - opening on first paint - and never loops.
 * Reduced motion skips the animation.
 */

import { useId } from 'react'

interface ApertureProps {
  size?: number
  /** 0-100. Omit for the logo's own proportions. */
  score?: number | null
  /** Open from closed on first paint. */
  animate?: boolean
  color?: string
  /** Accessible label; omit to hide from assistive tech (decorative use). */
  label?: string
}

const BLADES = 6

export function Aperture({
  size = 32,
  score,
  animate = false,
  color = 'var(--color-accent)',
  label,
}: ApertureProps) {
  const id = useId()
  // Openness 0..1. The logo sits at a fixed, pleasant 0.55.
  const open = score === undefined || score === null ? 0.55 : Math.max(0, Math.min(1, score / 100))

  const ring = 44
  const pupil = 6 + open * 7.5 // 6 -> 13.5
  const tipRadius = pupil + 4 + open * 6 // where each blade ends near the centre
  const sweep = 104 - open * 26 // degrees between a blade's root and its tip

  const blades = Array.from({ length: BLADES }, (_, index) => {
    const root = ((index * 360) / BLADES - 90) * (Math.PI / 180)
    const tip = root + sweep * (Math.PI / 180)
    const rx = 50 + ring * Math.cos(root)
    const ry = 50 + ring * Math.sin(root)
    const tx = 50 + tipRadius * Math.cos(tip)
    const ty = 50 + tipRadius * Math.sin(tip)
    // A tapered blade: wide at the ring, a point at the tip, bowed slightly so
    // the set reads as a swirl rather than straight spokes.
    const nx = -Math.sin(root) * 2.6
    const ny = Math.cos(root) * 2.6
    const cx = 50 + (ring * 0.62) * Math.cos(root + sweep * 0.42 * (Math.PI / 180))
    const cy = 50 + (ring * 0.62) * Math.sin(root + sweep * 0.42 * (Math.PI / 180))
    return `M${(rx + nx).toFixed(2)} ${(ry + ny).toFixed(2)} Q${cx.toFixed(2)} ${cy.toFixed(2)} ${tx.toFixed(2)} ${ty.toFixed(2)} Q${cx.toFixed(2)} ${cy.toFixed(2)} ${(rx - nx).toFixed(2)} ${(ry - ny).toFixed(2)}Z`
  })

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      focusable="false"
      className={animate ? 'fg-aperture-open' : undefined}
    >
      {label && <title>{label}</title>}
      <defs>
        <clipPath id={`${id}-clip`}>
          <circle cx="50" cy="50" r={ring} />
        </clipPath>
      </defs>
      <circle cx="50" cy="50" r={ring} fill="none" stroke={color} strokeWidth="5.5" />
      <g clipPath={`url(#${id}-clip)`} className="fg-aperture-blades">
        {blades.map((d, index) => (
          <path key={index} d={d} fill={color} />
        ))}
      </g>
      <circle cx="50" cy="50" r={pupil} fill={color} className="fg-aperture-pupil" />
    </svg>
  )
}
