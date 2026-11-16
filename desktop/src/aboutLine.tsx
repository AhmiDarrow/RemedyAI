/** Standing About line. Identical in every language — do not translate. */
import { openExternalUrl } from './api/auth'
import { useI18n } from './i18n'

export const AHMI_SIGNATURE = '"Hope this helps" - Ahmi'
export const REMEDY_SOURCE_URL = 'https://github.com/AhmiDarrow/RemedyAI'

export function AhmiSignature({ className }: { className?: string }) {
  return (
    <div className={className} data-testid="ahmi-signature">
      {AHMI_SIGNATURE}
    </div>
  )
}

/** Translated "Source code" label plus the repo link. The URL is not translated. */
export function AboutSourceLink({ className }: { className?: string }) {
  const { t } = useI18n()
  return (
    <div className={className}>
      <span style={{ color: 'var(--text-muted)' }}>{t('about.source')}</span>{' '}
      <button
        type="button"
        data-testid="about-source"
        className="underline"
        style={{
          color: 'var(--accent)',
          background: 'none',
          border: 0,
          padding: 0,
          cursor: 'pointer',
        }}
        onClick={() => void openExternalUrl(REMEDY_SOURCE_URL)}
      >
        github.com/AhmiDarrow/RemedyAI
      </button>
    </div>
  )
}
