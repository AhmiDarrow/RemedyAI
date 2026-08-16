/** Fallback voice picker: match an OS (speechSynthesis) voice to the
 * partner's assigned gender when local Kokoro TTS isn't installed.
 *
 * Pure + unit-tested; heuristic by necessity — OS voice metadata carries no
 * gender field, so we rank by well-known voice names per platform.
 */

export type GenderRole = 'female' | 'male' | 'neutral'

export interface NamedVoice {
  name: string
  lang: string
  default?: boolean
}

const FEMALE_NAMES = [
  'zira', 'aria', 'jenny', 'eva', 'susan', 'linda', 'heather', 'hazel',
  'samantha', 'victoria', 'karen', 'moira', 'tessa', 'fiona', 'serena',
  'amelie', 'joana', 'female',
]

const MALE_NAMES = [
  'david', 'mark', 'guy', 'ryan', 'george', 'james', 'daniel', 'alex',
  'fred', 'oliver', 'thomas', 'male',
]

function scoreVoice(v: NamedVoice, gender: GenderRole): number {
  const n = (v.name || '').toLowerCase()
  const lang = (v.lang || '').toLowerCase()
  let score = 0
  if (lang.startsWith('en')) score += 4
  if (v.default) score += 1
  const namePool =
    gender === 'male' ? MALE_NAMES : gender === 'female' ? FEMALE_NAMES : []
  if (namePool.some((g) => n.includes(g))) score += 8
  // Neutral: prefer voices that match NEITHER list strongly
  if (
    gender === 'neutral'
    && !FEMALE_NAMES.some((g) => n.includes(g))
    && !MALE_NAMES.some((g) => n.includes(g))
  ) {
    score += 6
  }
  // Penalize the opposite gender's well-known names
  const opposite = gender === 'male' ? FEMALE_NAMES : gender === 'female' ? MALE_NAMES : []
  if (opposite.some((g) => n.includes(g))) score -= 8
  // "Natural"/"Neural" voices sound far better when present
  if (n.includes('natural') || n.includes('neural') || n.includes('online')) score += 3
  return score
}

/** Best-effort OS voice for the assigned gender; null when list is empty. */
export function pickFallbackVoice(
  voices: NamedVoice[],
  gender: GenderRole,
): NamedVoice | null {
  if (!voices || voices.length === 0) return null
  let best: NamedVoice | null = null
  let bestScore = -Infinity
  for (const v of voices) {
    const s = scoreVoice(v, gender)
    if (s > bestScore) {
      best = v
      bestScore = s
    }
  }
  return best
}
