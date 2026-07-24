import { useEffect } from 'react'

interface ImageLightboxProps {
  src: string | null
  alt?: string
  onClose: () => void
}

/** Full-screen image viewer for chat / Comfy outputs. */
export function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  useEffect(() => {
    if (!src) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [src, onClose])

  if (!src) return null

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.82)' }}
      role="dialog"
      aria-modal="true"
      aria-label="Image preview"
      onClick={onClose}
    >
      <button
        type="button"
        className="absolute top-3 right-3 px-3 py-1.5 rounded-lg text-sm"
        style={{ background: 'rgba(255,255,255,0.12)', color: '#fff' }}
        onClick={onClose}
      >
        Close · Esc
      </button>
      <img
        src={src}
        alt={alt || 'Preview'}
        className="max-w-[min(96vw,1200px)] max-h-[90vh] object-contain rounded-lg shadow-2xl"
        style={{ border: '1px solid rgba(255,255,255,0.15)' }}
        onClick={(e) => e.stopPropagation()}
      />
      <a
        href={src}
        download
        target="_blank"
        rel="noreferrer"
        className="absolute bottom-4 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-lg text-xs"
        style={{ background: 'rgba(255,255,255,0.14)', color: '#fff' }}
        onClick={(e) => e.stopPropagation()}
      >
        Open original
      </a>
    </div>
  )
}
