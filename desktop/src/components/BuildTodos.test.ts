import { describe, expect, it } from 'vitest'
import { parseTodosPayload, todosHaveOpen } from './BuildTodos'

describe('parseTodosPayload', () => {
  it('keeps status and drops empty rows', () => {
    const items = parseTodosPayload({
      todos: [
        { id: '1', content: 'explore', status: 'completed' },
        { id: '2', content: 'implement', status: 'in_progress' },
        { id: 'x', content: '   ' },
        { title: 'verify', status: 'pending' },
      ],
    })
    expect(items).toHaveLength(3)
    expect(items[0]).toEqual({ id: '1', content: 'explore', status: 'completed' })
    expect(items[1].status).toBe('in_progress')
    expect(items[2].content).toBe('verify')
    expect(items[2].status).toBe('pending')
  })

  it('returns empty for junk', () => {
    expect(parseTodosPayload(null)).toEqual([])
    expect(parseTodosPayload({})).toEqual([])
    expect(parseTodosPayload({ todos: 'nope' })).toEqual([])
  })
})

describe('todosHaveOpen', () => {
  it('is false when every row is completed or cancelled', () => {
    expect(
      todosHaveOpen([
        { id: '1', content: 'a', status: 'completed' },
        { id: '2', content: 'b', status: 'cancelled' },
      ]),
    ).toBe(false)
    expect(todosHaveOpen([])).toBe(false)
  })

  it('is true while any row is still pending or in progress', () => {
    expect(
      todosHaveOpen([
        { id: '1', content: 'a', status: 'completed' },
        { id: '2', content: 'b', status: 'in_progress' },
      ]),
    ).toBe(true)
  })
})
