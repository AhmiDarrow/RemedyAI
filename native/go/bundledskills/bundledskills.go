// Package bundledskills carries the skills that ship with Remedy inside the
// runtime itself, so every install route has them: the desktop installers,
// the install script and a plain copy of the binary.
package bundledskills

import "embed"

// FS holds one folder per skill under "skills", each with its SKILL.md and
// whatever scripts and references the skill uses.
//
//go:embed all:skills
var FS embed.FS
