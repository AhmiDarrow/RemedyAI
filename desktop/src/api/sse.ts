/**
 * Incremental Server-Sent Events framing.
 *
 * Feed raw decoded text in any chunking (mid-line, mid-frame, CRLF) and get
 * one callback per complete frame. The frame name is the `event:` field when
 * the server sent one, otherwise the payload's own `type` field — the Go
 * runtime emits `data: {"type":"usage",...}` frames without an `event:` line.
 */

export type SseFrameHandler = (
  event: string,
  payload: Record<string, unknown>,
) => void

export type SseParser = {
  /** Append a decoded chunk; dispatches every frame completed by it. */
  push(chunk: string): void
  /** Flush a trailing frame the server closed without a blank line. */
  end(): void
}

export function createSseParser(onFrame: SseFrameHandler): SseParser {
  let buffer = ''
  let event = ''
  let dataLines: string[] = []

  const dispatch = () => {
    if (!dataLines.length) {
      event = ''
      return
    }
    const raw = dataLines.join('\n')
    dataLines = []
    const name = event
    event = ''
    let payload: unknown
    try {
      payload = JSON.parse(raw)
    } catch {
      return
    }
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return
    const rec = payload as Record<string, unknown>
    const resolved = name || (typeof rec.type === 'string' ? rec.type : '')
    if (!resolved) return
    onFrame(resolved, rec)
  }

  const handleLine = (line: string) => {
    if (line === '') {
      dispatch()
      return
    }
    // Comment / keepalive (`: ping`).
    if (line.startsWith(':')) return
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    switch (field) {
      case 'event':
        // A new event name with data still pending means the server omitted
        // the blank separator — close the previous frame first.
        if (dataLines.length) dispatch()
        event = value.trim()
        break
      case 'data':
        dataLines.push(value)
        break
      default:
        // id / retry / unknown fields are not used by this client.
        break
    }
  }

  const drain = () => {
    let nl = buffer.indexOf('\n')
    while (nl >= 0) {
      let line = buffer.slice(0, nl)
      if (line.endsWith('\r')) line = line.slice(0, -1)
      buffer = buffer.slice(nl + 1)
      handleLine(line)
      nl = buffer.indexOf('\n')
    }
  }

  return {
    push(chunk: string) {
      if (!chunk) return
      buffer += chunk
      drain()
    },
    end() {
      if (buffer) {
        buffer += '\n'
        drain()
      }
      dispatch()
    },
  }
}
