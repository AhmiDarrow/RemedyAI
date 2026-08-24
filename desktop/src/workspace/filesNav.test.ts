import { describe, expect, it } from 'vitest'
import { requestFilesPath, takePendingFilesPath } from './filesNav'

describe('filesNav pending path', () => {
  it('stores then consumes a folder path', () => {
    requestFilesPath(String.raw`C:\Users\Administrator\Desktop\example-folder`)
    expect(takePendingFilesPath()).toBe(
      String.raw`C:\Users\Administrator\Desktop\example-folder`,
    )
    expect(takePendingFilesPath()).toBeNull()
  })

  it('ignores blank', () => {
    requestFilesPath('   ')
    expect(takePendingFilesPath()).toBeNull()
  })
})
