/**
 * Open a prefilled GitHub issue for RemedyAI.
 */

import { openExternalUrl } from '../api/auth'

const ISSUES_NEW = 'https://github.com/AhmiDarrow/RemedyAI/issues/new'
const ISSUES_LIST = 'https://github.com/AhmiDarrow/RemedyAI/issues'

export function githubIssuesUrl(): string {
  return ISSUES_LIST
}

export function buildReportIssueUrl(version?: string | null): string {
  const ver = (version || 'unknown').replace(/^v/i, '')
  const body = encodeURIComponent(
    [
      '## What happened',
      '',
      '(Describe the issue…)',
      '',
      '## Steps to reproduce',
      '',
      '1. ',
      '',
      '## Expected vs actual',
      '',
      '',
      '## Environment',
      '',
      `- Remedy Desktop: v${ver}`,
      `- OS: Windows`,
      '',
    ].join('\n'),
  )
  const title = encodeURIComponent(`[Desktop v${ver}] `)
  return `${ISSUES_NEW}?title=${title}&body=${body}&labels=bug`
}

export async function openReportIssue(version?: string | null): Promise<void> {
  await openExternalUrl(buildReportIssueUrl(version))
}
