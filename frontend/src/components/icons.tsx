/**
 * A small inline icon set: 24px grid, 1.75 stroke, currentColor.
 *
 * Inline rather than an icon library: twenty glyphs do not justify a dependency
 * and a bundle entry, and inline SVG inherits the text colour, so an icon next
 * to a label always matches it.
 */

import type { SVGProps } from 'react'

import { Aperture } from './Aperture'

type IconProps = SVGProps<SVGSVGElement> & { size?: number }

function Icon({ size = 20, children, ...props }: IconProps & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  )
}

export const IconCamera = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 8.5A2.5 2.5 0 0 1 6.5 6h1.2l1.4-2h5.8l1.4 2h1.2A2.5 2.5 0 0 1 20 8.5v8A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5z" />
    <circle cx="12" cy="12.5" r="3.5" />
  </Icon>
)

export const IconDownload = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 4v11M7.5 10.5 12 15l4.5-4.5M5 19h14" />
  </Icon>
)

export const IconGlobe = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M3.5 12h17M12 3.5c2.4 2.3 3.6 5.1 3.6 8.5s-1.2 6.2-3.6 8.5c-2.4-2.3-3.6-5.1-3.6-8.5S9.6 5.8 12 3.5Z" />
  </Icon>
)

export const IconMonitor = (p: IconProps) => (
  <Icon {...p}>
    <rect x="3" y="4" width="18" height="12" rx="2" />
    <path d="M8 20h8M12 16v4" />
  </Icon>
)

export const IconLink = (p: IconProps) => (
  <Icon {...p}>
    <path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1" />
    <path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1" />
  </Icon>
)

export const IconPlay = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 5.5v13l10-6.5z" />
  </Icon>
)

export const IconStop = (p: IconProps) => (
  <Icon {...p}>
    <rect x="6.5" y="6.5" width="11" height="11" rx="2" />
  </Icon>
)

export const IconCheck = (p: IconProps) => (
  <Icon {...p}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
)

export const IconShield = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3 5 6v5.5c0 4.4 3 8.2 7 9.5 4-1.3 7-5.1 7-9.5V6z" />
    <path d="m9 12 2 2 4-4" />
  </Icon>
)

export const IconSparkles = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 4v3M12 17v3M4 12h3M17 12h3" />
    <path d="m7 7 1.5 1.5M15.5 15.5 17 17M17 7l-1.5 1.5M8.5 15.5 7 17" />
  </Icon>
)

export const IconClock = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Icon>
)

export const IconTarget = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8.5" />
    <circle cx="12" cy="12" r="4.5" />
    <circle cx="12" cy="12" r="0.8" fill="currentColor" />
  </Icon>
)

export const IconPhone = (p: IconProps) => (
  <Icon {...p}>
    <rect x="7" y="3" width="10" height="18" rx="2.5" />
    <path d="M11 17.5h2" />
  </Icon>
)

export const IconEye = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" />
    <circle cx="12" cy="12" r="3" />
  </Icon>
)

export const IconUsers = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="9" cy="8.5" r="3.5" />
    <path d="M3 19.5c.6-3 3-5 6-5s5.4 2 6 5" />
    <path d="M16 5.2a3.5 3.5 0 0 1 0 6.6M18 14.8c1.6.6 2.7 2.2 3 4.7" />
  </Icon>
)

export const IconTasks = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 6h11M9 12h11M9 18h11" />
    <path d="m3.5 6 1.2 1.2L7 5M3.5 12l1.2 1.2L7 11M3.5 18l1.2 1.2L7 17" />
  </Icon>
)

export const IconBolt = (p: IconProps) => (
  <Icon {...p}>
    <path d="M13 3 5 13.5h6L10 21l8-10.5h-6z" />
  </Icon>
)

export const IconAlert = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 4 2.8 19.5h18.4z" />
    <path d="M12 10v4M12 17v.2" />
  </Icon>
)

export const IconChart = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 20V4M4 20h16" />
    <path d="M8 16v-5M12 16V8M16 16v-3" />
  </Icon>
)

export const IconLock = (p: IconProps) => (
  <Icon {...p}>
    <rect x="5" y="10.5" width="14" height="10" rx="2" />
    <path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" />
  </Icon>
)

export const IconArrowRight = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
)

export const IconLogout = (p: IconProps) => (
  <Icon {...p}>
    <path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" />
    <path d="M10 16l-4-4 4-4M6 12h10" />
  </Icon>
)

/**
 * The FocusGuard mark: the yellow aperture from the brand logo.
 * `id` is accepted for compatibility with older call sites and ignored.
 */
export function Logo({ size = 28 }: { size?: number; id?: string }) {
  return <Aperture size={size} />
}
