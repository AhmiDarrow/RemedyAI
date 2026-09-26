import { describe, expect, it, vi } from 'vitest'
import { createSerialWriter } from './serialWriter'

function deferred() {
  let resolve!: () => void
  const promise = new Promise<void>((r) => { resolve = r })
  return { promise, resolve }
}

describe('document saves', () => {
  it('never lets an older slow save overwrite a newer edit', async () => {
    const gate = deferred()
    const saved: string[] = []
    const write = createSerialWriter<string>(async (_, value) => {
      if (value === 'old') await gate.promise
      saved.push(value)
    })
    const first = write('notes', 'old')
    const second = write('notes', 'new')
    await Promise.resolve()
    expect(saved).toEqual([])
    gate.resolve()
    await Promise.all([first, second])
    expect(saved).toEqual(['old', 'new'])
  })
  it('allows another session to save while one is slow', async () => {
    const gate = deferred()
    const write = createSerialWriter<string>(async (key) => {
      if (key === 'slow') await gate.promise
    })
    const slow = write('slow', 'a')
    await write('other', 'b')
    gate.resolve()
    await slow
  })
  it('reports failure without poisoning later saves or leaking old queue entries', async () => {
    const sink = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined)
    const write = createSerialWriter<string>(sink)
    const failed = write('notes', 'a')
    const retry = write('notes', 'b')
    await expect(failed).rejects.toThrow('offline')
    await retry
    await write('notes', 'c')
    expect(sink.mock.calls.map((c) => c[1])).toEqual(['a', 'b', 'c'])
  })
})
