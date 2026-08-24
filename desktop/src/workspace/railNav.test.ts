import { describe, expect, it } from 'vitest'
import {
  requestBrowserUrl,
  requestFilesPath,
  requestTerminalCwd,
  takePendingBrowserUrl,
  takePendingFilesPath,
  takePendingTerminalCwd,
} from './railNav'

describe('railNav pending context', () => {
  it('stores then consumes a files path', () => {
    requestFilesPath(String.raw`C:\Users\Administrator\Desktop\example-folder`)
    expect(takePendingFilesPath()).toBe(
      String.raw`C:\Users\Administrator\Desktop\example-folder`,
    )
    expect(takePendingFilesPath()).toBeNull()
  })

  it('stores terminal cwd and browser url', () => {
    requestTerminalCwd(String.raw`C:\Users\Administrator\Desktop`)
    requestBrowserUrl('https://github.com/AhmiDarrow/RemedyAI')
    expect(takePendingTerminalCwd()).toContain('Desktop')
    expect(takePendingBrowserUrl()).toContain('github.com')
  })

  it('ignores blank', () => {
    requestFilesPath('   ')
    expect(takePendingFilesPath()).toBeNull()
  })
})
