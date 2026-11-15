import { memo, useEffect, useRef, type KeyboardEvent } from 'react'
import type { MessageReactions } from '../types'
import { REACTIONS, isReaction, nextReaction, reactionLabel } from '../utils/reactions'

/**
 * Reactions under a chat bubble: at most one pill per side (the owner's and
 * Remedy's). The owner's pill on a reply is a button — pressing it removes it.
 */
export const ReactionPills = memo(function ReactionPills({
  messageId,
  reactions,
  partnerName,
  onReact,
  align,
}: {
  messageId: string
  reactions?: MessageReactions
  partnerName: string
  /** Present when the owner may change their reaction on this message. */
  onReact?: (messageId: string, emoji: string) => void
  align: 'start' | 'end'
}) {
  const mine = isReaction(reactions?.user) ? reactions!.user! : ''
  const theirs = isReaction(reactions?.agent) ? reactions!.agent! : ''
  if (!mine && !theirs) return null
  return (
    <div className={`reaction-row is-${align}`} data-testid="reaction-row">
      {mine &&
        (onReact ? (
          <button
            type="button"
            className="reaction-pill is-mine"
            aria-label={`You reacted ${reactionLabel(mine)}. Press to remove.`}
            title={`You reacted ${reactionLabel(mine)} — click to remove`}
            onClick={() => onReact(messageId, '')}
          >
            <span aria-hidden>{mine}</span>
          </button>
        ) : (
          <span className="reaction-pill is-mine" role="img" aria-label={`You reacted ${reactionLabel(mine)}`}>
            {mine}
          </span>
        ))}
      {theirs && (
        <span
          className="reaction-pill is-agent"
          role="img"
          aria-label={`${partnerName} reacted ${reactionLabel(theirs)}`}
          title={`${partnerName} reacted ${reactionLabel(theirs)}`}
        >
          <span aria-hidden>{theirs}</span>
          <span className="reaction-who" aria-hidden>
            {partnerName.slice(0, 1).toUpperCase()}
          </span>
        </span>
      )}
    </div>
  )
})

/** The four-option picker. Arrow keys move, Enter/Space picks, Escape closes. */
export function ReactionPicker({
  messageId,
  current,
  open,
  onOpenChange,
  onReact,
}: {
  messageId: string
  current?: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onReact: (messageId: string, emoji: string) => void
}) {
  const wrapRef = useRef<HTMLSpanElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([])

  // Focus the selected (or first) option when the picker opens.
  useEffect(() => {
    if (!open) return
    const at = Math.max(0, REACTIONS.findIndex((r) => r.emoji === current))
    optionRefs.current[at]?.focus()
  }, [open, current])

  // Click / tap outside closes.
  useEffect(() => {
    if (!open) return
    const onDown = (e: PointerEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) onOpenChange(false)
    }
    document.addEventListener('pointerdown', onDown)
    return () => document.removeEventListener('pointerdown', onDown)
  }, [open, onOpenChange])

  const close = (refocus: boolean) => {
    onOpenChange(false)
    if (refocus) triggerRef.current?.focus()
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = optionRefs.current.findIndex((el) => el === document.activeElement)
    if (e.key === 'Escape') {
      e.preventDefault()
      close(true)
    } else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault()
      optionRefs.current[(i + 1) % REACTIONS.length]?.focus()
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault()
      optionRefs.current[(i - 1 + REACTIONS.length) % REACTIONS.length]?.focus()
    } else if (e.key === 'Home') {
      e.preventDefault()
      optionRefs.current[0]?.focus()
    } else if (e.key === 'End') {
      e.preventDefault()
      optionRefs.current[REACTIONS.length - 1]?.focus()
    }
  }

  return (
    <span className="reaction-picker-wrap" ref={wrapRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`reaction-add${open ? ' is-open' : ''}`}
        aria-label="Add reaction"
        title="React"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => onOpenChange(!open)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
          <circle cx="11" cy="12" r="8" />
          <path d="M8 14.5c.8 1 1.8 1.5 3 1.5s2.2-.5 3-1.5" strokeLinecap="round" />
          <circle cx="8.5" cy="10" r="0.6" fill="currentColor" />
          <circle cx="13.5" cy="10" r="0.6" fill="currentColor" />
          <path d="M20 3v4M18 5h4" strokeLinecap="round" />
        </svg>
      </button>
      {open && (
        <div
          className="reaction-picker"
          role="toolbar"
          aria-label="React to this message"
          data-testid="reaction-picker"
          onKeyDown={onKeyDown}
        >
          {REACTIONS.map((r, i) => {
            const selected = current === r.emoji
            return (
              <button
                key={r.emoji}
                ref={(el) => {
                  optionRefs.current[i] = el
                }}
                type="button"
                className={`reaction-option${selected ? ' is-selected' : ''}`}
                aria-label={selected ? `${r.label} (selected, press to remove)` : r.label}
                aria-pressed={selected}
                title={`${r.label} — ${r.meaning}`}
                tabIndex={selected || (!current && i === 0) ? 0 : -1}
                onClick={() => {
                  onReact(messageId, nextReaction(current, r.emoji))
                  close(true)
                }}
              >
                <span aria-hidden>{r.emoji}</span>
              </button>
            )
          })}
        </div>
      )}
    </span>
  )
}
