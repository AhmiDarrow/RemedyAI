/** A floating chat with Remedy — opens over a room when the owner speaks
 * there. Drag it by its header, close it with ✕ or Esc. It is a window onto
 * her main thread, not a separate conversation: everything said here is in
 * that thread too. Position is remembered per device. */

import { useCallback, useEffect, useRef, useState } from 'react'
import { RemedyLogo } from '../components/RemedyLogo'
import { BUBBLE_H, BUBBLE_W, clampBubble, type BubbleMessage } from './headHelpers'

export interface RemedyBubbleProps {
  name: string
  userName: string
  messages: BubbleMessage[]
  partial: string
  streaming: boolean
  onSend: (text: string) => Promise<void> | void
  onClose: () => void
}

const POS_KEY = 'remedy.grove.bubble.pos.v1'
const W = BUBBLE_W
const H = BUBBLE_H

type Pos = { x: number; y: number }

function loadPos(): Pos | null {
  try {
    const raw = localStorage.getItem(POS_KEY)
    const p = raw ? JSON.parse(raw) : null
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p
  } catch {
    /* storage unavailable */
  }
  return null
}

function savePos(p: Pos) {
  try {
    localStorage.setItem(POS_KEY, JSON.stringify(p))
  } catch {
    /* */
  }
}

export function RemedyBubble({ name, userName, messages, partial, streaming, onSend, onClose }: RemedyBubbleProps) {
  const [pos, setPos] = useState<Pos>(() => {
    const vw = typeof window !== 'undefined' ? window.innerWidth : 1200
    const vh = typeof window !== 'undefined' ? window.innerHeight : 800
    return clampBubble(loadPos() || { x: vw - W - 32, y: vh - H - 96 }, vw, vh)
  })
  const [draft, setDraft] = useState('')
  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const bodyRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    const onResize = () => setPos((p) => clampBubble(p, window.innerWidth, window.innerHeight))
    window.addEventListener('keydown', onKey)
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', onResize)
    }
  }, [onClose])

  useEffect(() => {
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages.length, partial])

  const onPointerDown = useCallback(
    (e: React.PointerEvent) => {
      if ((e.target as HTMLElement).closest('button')) return
      drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y }
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    },
    [pos],
  )
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current) return
    setPos(clampBubble({ x: e.clientX - drag.current.dx, y: e.clientY - drag.current.dy }, window.innerWidth, window.innerHeight))
  }
  const onPointerUp = () => {
    if (!drag.current) return
    drag.current = null
    savePos(pos)
  }
  // Keyboard move for owners who do not drag: arrows on the header.
  const onHeaderKey = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 40 : 12
    const d: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    }
    const v = d[e.key]
    if (!v) return
    e.preventDefault()
    setPos((p) => {
      const n = clampBubble({ x: p.x + v[0], y: p.y + v[1] }, window.innerWidth, window.innerHeight)
      savePos(n)
      return n
    })
  }

  return (
    <div className="rb" role="dialog" aria-label={`Chat with ${name}`} style={{ left: pos.x, top: pos.y, width: W }}>
      <div
        className="rb-head"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onKeyDown={onHeaderKey}
        tabIndex={0}
        aria-label="Move chat (arrow keys)"
      >
        <RemedyLogo size={16} variant="auto" title={name} />
        <span className="rb-name">{name}</span>
        {streaming && <span className="grove-live-dot" title={`${name} is answering`} />}
        <span className="rb-hint">Also in her main thread</span>
        <button type="button" className="rb-x" onClick={onClose} aria-label="Close chat">
          ✕
        </button>
      </div>
      <div className="rb-body" ref={bodyRef}>
        {messages.map((m) => (
          <div key={m.id} className={`rb-msg ${m.role}`}>
            <span className="rb-who">{m.role === 'user' ? userName || 'You' : name}</span>
            <span className="rb-text">{m.text}</span>
          </div>
        ))}
        {streaming && (
          <div className="rb-msg assistant live">
            <span className="rb-who">{name}</span>
            <span className="rb-text">{partial || '…'}</span>
          </div>
        )}
      </div>
      <form
        className="rb-foot"
        onSubmit={(e) => {
          e.preventDefault()
          const t = draft.trim()
          if (!t) return
          setDraft('')
          void onSend(t)
        }}
      >
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={`Reply to ${name}…`} aria-label={`Reply to ${name}`} />
        <button type="submit" className="rh-btn" disabled={!draft.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}
