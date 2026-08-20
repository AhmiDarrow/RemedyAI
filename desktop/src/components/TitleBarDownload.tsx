/** Slim download strip for the unused title-bar spacer. */

import { useDownloadProgress } from '../hooks/useDownloadProgress'

export function TitleBarDownload() {
  const { primary, caption } = useDownloadProgress()
  if (!primary) return null
  // 0% is a real "started" value from the sidecar — treat it as unknown so
  // the fill animates instead of looking like an empty title bar.
  const known = primary.percent != null && primary.percent >= 3
  const pct = known ? primary.percent : null
  return (
    <div
      className="titlebar-download"
      role="progressbar"
      aria-label={caption}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct ?? undefined}
      aria-live="polite"
    >
      <div className="titlebar-download-track" aria-hidden>
        <div
          className={`titlebar-download-fill${known ? '' : ' is-unknown'}`}
          style={{ width: known ? `${pct}%` : '32%' }}
        />
      </div>
      <span className="titlebar-download-label">{caption}</span>
    </div>
  )
}
