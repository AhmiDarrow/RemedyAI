import { useEffect, useRef } from 'react'
import { useDialogFocus } from '../../hooks/useDialogFocus'
import { browserStackHold } from '../../utils/browserStack'
import { RemedyLogo } from '../RemedyLogo'
import {
  UPDATE_OFFER_FALLBACK,
  type SetupUpdateOffer,
} from '../../api/setup'

interface HouseRulesUpdateOfferProps {
  open: boolean
  offer?: SetupUpdateOffer | null
  onGo: () => void
  onLater: () => void
}

export function HouseRulesUpdateOffer({
  open,
  offer,
  onGo,
  onLater,
}: HouseRulesUpdateOfferProps) {
  const goRef = useRef<HTMLButtonElement | null>(null)
  const dialogRef = useDialogFocus<HTMLDivElement>(open, onLater, goRef)
  const copy = offer || UPDATE_OFFER_FALLBACK

  useEffect(() => {
    if (!open) return
    return browserStackHold('house-rules-update-offer')
  }, [open])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center p-4 ui-overlay"
      role="dialog"
      ref={dialogRef}
      tabIndex={-1}
      aria-modal="true"
      aria-labelledby="house-rules-update-title"
      data-dialog-layer
    >
      <div className="ui-surface w-full max-w-md p-5" style={{ color: 'var(--text-primary)' }}>
        <div className="flex items-center gap-3 mb-4">
          <RemedyLogo size={36} framed />
          <div className="min-w-0">
            <h2 id="house-rules-update-title" className="font-semibold text-base tracking-tight">
              {copy.title || 'House rules'}
            </h2>
          </div>
        </div>
        <p className="text-sm leading-relaxed mb-5" style={{ color: 'var(--text-secondary)' }}>
          {copy.body || UPDATE_OFFER_FALLBACK.body}
        </p>
        <div className="flex gap-2 justify-end">
          <button type="button" className="ui-btn ui-btn-ghost" onClick={onLater}>
            {copy.secondary || 'Not now'}
          </button>
          <button
            ref={goRef}
            type="button"
            className="ui-btn ui-btn-primary"
            onClick={onGo}
          >
            {copy.primary || "Let's go"}
          </button>
        </div>
      </div>
    </div>
  )
}
