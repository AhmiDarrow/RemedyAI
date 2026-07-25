import { useMemo } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { markdown } from '@codemirror/lang-markdown'
import { EditorView } from '@codemirror/view'

interface SkillMarkdownEditorProps {
  value: string
  onChange: (v: string) => void
  height?: string
  readOnly?: boolean
}

/** Embedded CodeMirror markdown editor for skill SKILL.md bodies. */
export function SkillMarkdownEditor({
  value,
  onChange,
  height = '280px',
  readOnly = false,
}: SkillMarkdownEditorProps) {
  const extensions = useMemo(
    () => [
      markdown(),
      EditorView.theme({
        '&': {
          fontSize: '12px',
          backgroundColor: 'var(--bg-primary)',
          color: 'var(--text-primary)',
        },
        '.cm-content': {
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          caretColor: 'var(--accent)',
        },
        '.cm-gutters': {
          backgroundColor: 'var(--bg-secondary)',
          color: 'var(--text-muted)',
          border: 'none',
        },
        '&.cm-focused': { outline: '1px solid var(--accent)' },
      }),
      EditorView.lineWrapping,
    ],
    [],
  )

  return (
    <div
      className="rounded overflow-hidden"
      style={{ border: '1px solid var(--border)' }}
    >
      <CodeMirror
        value={value}
        height={height}
        extensions={extensions}
        editable={!readOnly}
        basicSetup={{
          lineNumbers: true,
          foldGutter: true,
          highlightActiveLine: true,
        }}
        onChange={onChange}
      />
    </div>
  )
}
