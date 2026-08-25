import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const dir = dirname(fileURLToPath(import.meta.url))
const chat = readFileSync(join(dir, 'GroveChat.tsx'), 'utf8')
const app = readFileSync(join(dir, 'GroveApp.tsx'), 'utf8')

describe('Grove live todos', () => {
  it('GroveChat paints the same BuildTodos feed as Studio', () => {
    expect(chat).toContain('buildTodos={buildTodos}')
  })

  it('GroveApp threads buildTodos into every GroveChat', () => {
    const n = app.split('buildTodos={buildTodos}').length - 1
    expect(n).toBeGreaterThanOrEqual(3)
  })
})
