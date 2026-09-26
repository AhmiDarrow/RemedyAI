import { describe, expect, it } from 'vitest'
import { absoluteFilePath, parentFilePath } from './filePaths'

describe('workspace file paths', () => {
  it('joins relative paths and preserves absolute paths on both platforms', () => {
    expect(absoluteFilePath('C:\\work', 'src/main.ts')).toBe('C:\\work\\src\\main.ts')
    expect(absoluteFilePath('/home/alex/work', './src/main.ts')).toBe('/home/alex/work/src/main.ts')
    expect(absoluteFilePath('/home/alex/work', '/tmp/file.txt')).toBe('/tmp/file.txt')
    expect(absoluteFilePath('C:\\work', 'D:\\other\\file.txt')).toBe('D:\\other\\file.txt')
    expect(absoluteFilePath('C:\\work', '\\\\server\\share\\file.txt')).toBe('\\\\server\\share\\file.txt')
    expect(absoluteFilePath('/work', '.')).toBe('/work')
    expect(absoluteFilePath('/', 'file.txt')).toBe('/file.txt')
  })
  it('keeps volume, POSIX and UNC roots intact when navigating upward', () => {
    for (const [path, parent] of [
      ['src/lib', 'src'], ['src', '.'], ['.', '.'],
      ['/home/alex', '/home'], ['/home', '/'], ['/', '/'],
      ['C:\\work', 'C:/'], ['C:\\', 'C:/'],
      ['\\\\server\\share\\folder', '//server/share'], ['\\\\server\\share', '//server/share'],
    ]) expect(parentFilePath(path)).toBe(parent)
  })
})
