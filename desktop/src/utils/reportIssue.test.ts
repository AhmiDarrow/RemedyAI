import { describe, expect, it } from 'vitest'
import { buildReportIssueUrl, githubIssuesUrl } from './reportIssue'

describe('reportIssue', () => {
  it('builds prefilled GitHub new-issue URL', () => {
    const url = buildReportIssueUrl('0.10.37')
    expect(url).toContain('github.com/AhmiDarrow/RemedyAI/issues/new')
    expect(url).toContain('labels=bug')
    expect(decodeURIComponent(url)).toContain('Desktop v0.10.37')
    expect(decodeURIComponent(url)).toContain('Remedy Desktop: v0.10.37')
  })

  it('strips leading v from version', () => {
    const url = buildReportIssueUrl('v0.10.37')
    expect(decodeURIComponent(url)).toContain('Desktop v0.10.37')
    expect(decodeURIComponent(url)).not.toContain('vv0.10')
  })

  it('lists issues index', () => {
    expect(githubIssuesUrl()).toBe('https://github.com/AhmiDarrow/RemedyAI/issues')
  })
})
