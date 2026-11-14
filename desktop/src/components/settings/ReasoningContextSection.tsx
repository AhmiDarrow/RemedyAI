import { useEffect, useState, type ReactNode } from 'react'
import { getSettings, updateSettings } from '../../api/settings'
import {
  EMPTY_FORM,
  buildGenerationPatch,
  formFromValues,
  modelKey,
  tokenError,
  type Effort,
  type GenerationForm,
  type GenerationValues,
  type TokenKey,
} from '../../utils/generationSettings'
import { FormActionButton, FormHint, FormInput, FormLabel, FormSegmented, FormSelectNative } from './formUi'

const EFFORT_OPTIONS: Array<{ value: Effort; label: string }> = [
  { value: '', label: 'Provider default' },
  { value: 'none', label: 'None' },
  { value: 'minimal', label: 'Minimal' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra high' },
]

const TOKEN_FIELDS: Array<{ key: TokenKey; label: string; hint: string }> = [
  {
    key: 'thinking_budget_tokens',
    label: 'Thinking budget (tokens)',
    hint: 'Claude models with budget thinking and Gemini 2.5+. Overrides the effort for those models.',
  },
  { key: 'max_output_tokens', label: 'Max output tokens', hint: 'Ceiling for one model reply.' },
  {
    key: 'context_window_tokens',
    label: 'Context window (tokens)',
    hint: 'How much history is kept before older turns are trimmed. Local servers stay capped at their real window.',
  },
]

type Stored = GenerationValues & { model_settings?: Record<string, GenerationValues> }

export function ReasoningContextSection({ provider, model }: { provider: string; model: string }): ReactNode {
  const [stored, setStored] = useState<Stored | null>(null)
  const [scope, setScope] = useState<'all' | 'model'>('all')
  const [form, setForm] = useState<GenerationForm>(EMPTY_FORM)
  const [status, setStatus] = useState<string>('')
  const [saving, setSaving] = useState(false)
  const key = modelKey(provider, model)

  useEffect(() => {
    let live = true
    getSettings()
      .then((s) => {
        if (live) setStored(s)
      })
      .catch(() => {})
    return () => {
      live = false
    }
  }, [])

  useEffect(() => {
    if (!stored) return
    const per = stored.model_settings?.[key] ?? stored.model_settings?.[model]
    setForm(formFromValues(scope === 'model' ? per : stored))
  }, [stored, scope, key, model])

  const errors = TOKEN_FIELDS.map((f) => tokenError(f.key, form[f.key]))
  const invalid = errors.some(Boolean) || (scope === 'model' && !key)

  const save = async () => {
    setSaving(true)
    setStatus('')
    try {
      await updateSettings(buildGenerationPatch(form, scope, provider, model))
      setStored(await getSettings())
      setStatus('Saved. Applies from the next message.')
    } catch (e) {
      setStatus(e instanceof Error ? e.message : 'Could not save.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mb-2" data-testid="reasoning-context">
      <FormSegmented
        value={scope}
        onChange={setScope}
        options={[
          { id: 'all', label: 'All models' },
          { id: 'model', label: model ? `This model (${model})` : 'This model', title: key },
        ]}
      />
      <FormLabel htmlFor="gen-effort">Reasoning effort</FormLabel>
      <FormSelectNative
        id="gen-effort"
        value={form.reasoning_effort}
        onChange={(v) => setForm({ ...form, reasoning_effort: v as Effort })}
      >
        {EFFORT_OPTIONS.map((o) => (
          <option key={o.value || 'default'} value={o.value}>
            {o.label}
          </option>
        ))}
      </FormSelectNative>
      <FormHint>
        Sent as reasoning effort (OpenAI reasoning models, Grok 3 mini, Gemini), adaptive effort or a thinking budget
        (Claude). Models without the control ignore it.
      </FormHint>
      {TOKEN_FIELDS.map((f, i) => (
        <div key={f.key} className="mb-2">
          <FormLabel htmlFor={`gen-${f.key}`}>{f.label}</FormLabel>
          <FormInput
            id={`gen-${f.key}`}
            value={form[f.key]}
            placeholder="Provider default"
            onChange={(v) => setForm({ ...form, [f.key]: v })}
            size="sm"
          />
          <FormHint>{errors[i] ?? f.hint}</FormHint>
        </div>
      ))}
      <FormActionButton variant="primary" disabled={invalid || saving || !stored} onClick={save}>
        {saving ? 'Saving…' : 'Save reasoning and context'}
      </FormActionButton>
      {status ? <FormHint className="mt-1">{status}</FormHint> : null}
    </div>
  )
}
