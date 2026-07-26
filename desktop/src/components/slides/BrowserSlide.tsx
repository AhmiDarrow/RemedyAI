import { useState } from 'react'

const HOME = 'https://github.com/AhmiDarrow/RemedyAI'

export function BrowserSlide() {
  const [url, setUrl] = useState(HOME)
  const [active, setActive] = useState(HOME)

  return (
    <div className="flex flex-col h-full min-h-0 text-xs">
      <form
        className="flex gap-1 px-2 py-1.5 border-b shrink-0"
        style={{ borderColor: 'var(--border)' }}
        onSubmit={(e) => {
          e.preventDefault()
          let u = url.trim()
          if (!u) return
          if (!/^https?:\/\//i.test(u)) u = `https://${u}`
          setUrl(u)
          setActive(u)
        }}
      >
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="flex-1 min-w-0 rounded px-1.5 py-1 outline-none"
          style={{
            background: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
          }}
          placeholder="https://"
          aria-label="Browser URL"
        />
        <button
          type="submit"
          className="px-2 py-1 rounded"
          style={{ background: 'var(--accent)', color: '#fff' }}
        >
          Go
        </button>
      </form>
      <iframe
        title="Remedy browser"
        src={active}
        className="flex-1 min-h-0 w-full border-0"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
        referrerPolicy="no-referrer"
      />
    </div>
  )
}
