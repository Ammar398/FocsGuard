/**
 * The focus score as an aperture.
 *
 * The iris opens with focus - closed and small at 0, wide with a large bright
 * pupil at 100 - so the number has a shape you can read across the room. The
 * number itself is plain ink (dataviz: text wears text tokens, never a series
 * colour); its band is a small dot with a written description beside it, so
 * colour never carries the meaning alone.
 */

import { scoreColor, scoreDescription } from '../lib/format'
import { Aperture } from './Aperture'

interface FocusRingProps {
  score: number
  size?: number
  /** Hidden when the ring is a small inline indicator. */
  caption?: string
}

export function FocusRing({ score, size = 168, caption }: FocusRingProps) {
  const bounded = Math.max(0, Math.min(100, score))
  const description = scoreDescription(bounded)

  return (
    <figure
      className="flex flex-col items-center gap-3"
      aria-label={`Focus score ${Math.round(bounded)} out of 100. ${description}.`}
    >
      <Aperture size={size} score={bounded} animate />
      <figcaption className="text-center">
        <span className="block text-5xl leading-none font-[750] tracking-tight [font-stretch:112%]">
          {Math.round(bounded)}
        </span>
        <span className="mt-2 inline-flex items-center gap-1.5 text-sm text-[var(--color-muted)]">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: scoreColor(bounded) }}
            aria-hidden="true"
          />
          {caption ?? description}
        </span>
      </figcaption>
    </figure>
  )
}
