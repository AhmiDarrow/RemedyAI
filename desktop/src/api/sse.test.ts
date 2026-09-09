import { describe, expect, it } from 'vitest'
import { createSseParser } from './sse'

type Frame = [string, Record<string, unknown>]

function collect() {
  const frames: Frame[] = []
  const parser = createSseParser((event, payload) => frames.push([event, payload]))
  return { frames, parser }
}

describe('createSseParser', () => {
  it('dispatches one frame per blank-line separated event', () => {
    const { frames, parser } = collect()
    parser.push('event: token\ndata: {"text":"a"}\n\nevent: token\ndata: {"text":"b"}\n\n')
    expect(frames).toEqual([
      ['token', { text: 'a' }],
      ['token', { text: 'b' }],
    ])
  })

  it('survives chunk splits inside lines, fields and JSON', () => {
    const { frames, parser } = collect()
    const wire = 'event: tool_call\ndata: {"name":"file_read","id":"c1"}\n\nevent: done\ndata: {"request_id":"r9"}\n\n'
    // Feed one byte at a time — the harshest split.
    for (const ch of wire) parser.push(ch)
    expect(frames).toEqual([
      ['tool_call', { name: 'file_read', id: 'c1' }],
      ['done', { request_id: 'r9' }],
    ])
  })

  it('accepts CRLF line endings', () => {
    const { frames, parser } = collect()
    parser.push('event: token\r\ndata: {"text":"x"}\r\n\r\n')
    expect(frames).toEqual([['token', { text: 'x' }]])
  })

  it('ignores keepalive comments and unknown fields', () => {
    const { frames, parser } = collect()
    parser.push(': ping\n\n:keepalive\nid: 4\nretry: 1000\nevent: token\ndata: {"text":"y"}\n\n')
    expect(frames).toEqual([['token', { text: 'y' }]])
  })

  it('falls back to payload.type when the server omits event:', () => {
    const { frames, parser } = collect()
    parser.push('data: {"type":"usage","prompt_tokens":12,"completion_tokens":3}\n\n')
    parser.push('data: {"type":"thinking","text":"hm"}\n\n')
    expect(frames).toEqual([
      ['usage', { type: 'usage', prompt_tokens: 12, completion_tokens: 3 }],
      ['thinking', { type: 'thinking', text: 'hm' }],
    ])
  })

  it('closes a frame when a new event: arrives without a blank separator', () => {
    const { frames, parser } = collect()
    parser.push('event: token\ndata: {"text":"a"}\nevent: token\ndata: {"text":"b"}\n\n')
    expect(frames.map((f) => f[1].text)).toEqual(['a', 'b'])
  })

  it('joins multi-line data and drops unparseable frames', () => {
    const { frames, parser } = collect()
    parser.push('event: token\ndata: {"text":\ndata: "two"}\n\nevent: token\ndata: not json\n\n')
    expect(frames).toEqual([['token', { text: 'two' }]])
  })

  it('end() flushes a trailing frame with no final newline', () => {
    const { frames, parser } = collect()
    parser.push('event: done\ndata: {"request_id":"tail"}')
    expect(frames).toEqual([])
    parser.end()
    expect(frames).toEqual([['done', { request_id: 'tail' }]])
  })

  it('drops frames with neither event: nor type', () => {
    const { frames, parser } = collect()
    parser.push('data: {"text":"orphan"}\n\n')
    expect(frames).toEqual([])
  })
})
