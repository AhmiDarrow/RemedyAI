/** True when the user is clearly asking to leave Plan and/or start Build work. */
export function looksLikeBuildKick(text: string): boolean {
  const t = (text || '').trim()
  if (!t) return false
  return (
    /\bproceed\b/i.test(t)
    || /\bcontinue\b/i.test(t)
    || /\bgo\s+ahead\b/i.test(t)
    || /\bdo\s+it\b/i.test(t)
    || /\bkeep\s+going\b/i.test(t)
    || /\bimplement\b/i.test(t)
    || /\bfixes?\b/i.test(t)
    || /\bswitch\s+to\s+build\b/i.test(t)
    || /\bleave\s+plan(?:\s+mode)?\b/i.test(t)
    || /\bout\s+of\s+plan(?:\s+mode)?\b/i.test(t)
    || /\benter\s+build(?:\s+mode)?\b/i.test(t)
    || /\bbuild\s+mode\b/i.test(t)
    || /\bstart\s+(?:working|implementing|coding|building)\b/i.test(t)
    || /\bnot\s+doing\s+anything\b/i.test(t)
  )
}

/** Chat pin stays on "continue" (still talking). Clear work kicks leave Chat. */
export function looksLikeLeaveChat(text: string): boolean {
  const t = (text || '').trim()
  if (!t) return false
  return (
    /\bswitch\s+to\s+build\b/i.test(t)
    || /\benter\s+build(?:\s+mode)?\b/i.test(t)
    || /\bbuild\s+mode\b/i.test(t)
    || /\bleave\s+chat(?:\s+mode)?\b/i.test(t)
    || /\bimplement\b/i.test(t)
    || /\bstart\s+(?:working|implementing|coding|building)\b/i.test(t)
    || /\bkeep\s+going\b/i.test(t)
    || /\bgo\s+ahead\b/i.test(t)
    || /\bdo\s+it\b/i.test(t)
    || /\bproceed\b/i.test(t)
  )
}
