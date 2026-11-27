package tools

import "github.com/AhmiDarrow/RemedyAI/native/go/core"

// PressKeyCombo presses a key or combo ("enter", "ctrl+s") the way
// computer.key does, for the CLI computer host (`remedy computer host`).
func PressKeyCombo(key string) error {
	vks, err := resolveKeyCombo(key)
	if err != nil {
		return err
	}
	return core.KeyCombo(vks)
}
