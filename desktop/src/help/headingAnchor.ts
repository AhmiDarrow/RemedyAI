import { Children, isValidElement, type ReactNode } from 'react'

export function headingText(children: ReactNode): string {
  return Children.toArray(children).map((child): string => {
    if (typeof child === 'string' || typeof child === 'number') return String(child)
    if (isValidElement<{ children?: ReactNode }>(child)) return headingText(child.props.children)
    return ''
  }).join('')
}

/** GitHub-style anchors for the offline manual, including non-English headings. */
export function headingAnchor(children: ReactNode): string {
  return headingText(children).toLowerCase().replace(/[^\p{L}\p{N}\p{M}_\-\s]/gu, '').replace(/\s/g, '-')
}
