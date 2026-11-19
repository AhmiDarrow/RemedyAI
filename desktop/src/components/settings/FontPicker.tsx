/** Settings → Appearance → Fonts: one choice per role, applied instantly. */

import { useState } from 'react'
import {
  DEFAULT_FONTS,
  FONT_ROLES,
  type FontFaceId,
  type FontRole,
  facesForRole,
  fontStack,
  loadFonts,
  resetFonts,
  saveFont,
} from '../../utils/fontPrefs'
import { FormActionButton, FormHint, FormLabel, FormSelect } from './formUi'

export function FontPicker() {
  const [fonts, setFonts] = useState(loadFonts)
  const isDefault = FONT_ROLES.every((r) => fonts[r.role] === DEFAULT_FONTS[r.role])

  return (
    <div data-testid="font-picker">
      <FormLabel className="mt-2">Fonts</FormLabel>
      <FormHint>
        Remedy Sans UI is the main font. Headings, long reading, small accents and code each have
        their own font so the app is easy to scan. All fonts ship with Remedy and work offline.
      </FormHint>
      {FONT_ROLES.map((r) => (
        <div key={r.role} className="mb-1">
          <FormLabel htmlFor={`font-${r.role}`}>{r.label}</FormLabel>
          <FormSelect
            id={`font-${r.role}`}
            size="sm"
            value={fonts[r.role]}
            onChange={(v) => setFonts(saveFont(r.role as FontRole, v as FontFaceId))}
            options={facesForRole(r.role).map((f) => ({
              value: f.id,
              label: f.id === DEFAULT_FONTS[r.role] ? `${f.label} (default)` : f.label,
            }))}
          />
          <FormHint className="-mt-1">
            <span style={{ fontFamily: fontStack(fonts[r.role]) }}>{r.hint}</span>
          </FormHint>
        </div>
      ))}
      {!isDefault ? (
        <FormActionButton onClick={() => setFonts(resetFonts())}>Reset fonts</FormActionButton>
      ) : null}
    </div>
  )
}
