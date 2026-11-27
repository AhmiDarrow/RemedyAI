package httpapi

import (
	"crypto/sha256"
	"encoding/hex"
	"io/fs"
	"os"
	"path/filepath"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/bundledskills"
)

// The skills that ship with Remedy are compiled into the runtime. The rest of
// the skill code works on folders, so they are written out once, to a folder
// named after their content, and that folder is used as a seed root. A new
// runtime with changed skills gets a new folder; the old one is removed.

var embeddedSkills struct {
	sync.Mutex
	home, root string
}

// embeddedSkillsRoot returns the folder holding the shipped skills for this
// runtime, writing it first if it is not there yet. "" when it cannot be written.
func embeddedSkillsRoot(home string) string {
	if home == "" {
		return ""
	}
	embeddedSkills.Lock()
	defer embeddedSkills.Unlock()
	if embeddedSkills.home == home && embeddedSkills.root != "" && dirExists(embeddedSkills.root) {
		return embeddedSkills.root
	}
	base := filepath.Join(home, "cache", "bundled-skills")
	root := filepath.Join(base, embeddedSkillsDigest())
	if !fileExists(filepath.Join(root, ".complete")) {
		if err := writeEmbeddedSkills(root); err != nil {
			_ = os.RemoveAll(root)
			return ""
		}
		// Folders of other runtime versions are no longer the shipped set.
		if entries, err := os.ReadDir(base); err == nil {
			for _, ent := range entries {
				if ent.IsDir() && ent.Name() != filepath.Base(root) {
					_ = os.RemoveAll(filepath.Join(base, ent.Name()))
				}
			}
		}
	}
	embeddedSkills.home, embeddedSkills.root = home, root
	return root
}

// embeddedSkillsDigest names the shipped set by every file's path and size.
var embeddedSkillsDigest = sync.OnceValue(func() string {
	h := sha256.New()
	_ = fs.WalkDir(bundledskills.FS, "skills", func(p string, d fs.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		info, err := d.Info()
		if err != nil {
			return nil
		}
		h.Write([]byte(p))
		h.Write([]byte{0})
		h.Write([]byte(itoa64(info.Size())))
		h.Write([]byte{0})
		return nil
	})
	return hex.EncodeToString(h.Sum(nil))[:16]
})

func writeEmbeddedSkills(root string) error {
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	err := fs.WalkDir(bundledskills.FS, "skills", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel("skills", filepath.FromSlash(p))
		if err != nil || rel == "." {
			return err
		}
		target := filepath.Join(root, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		data, err := fs.ReadFile(bundledskills.FS, p)
		if err != nil {
			return err
		}
		return os.WriteFile(target, data, 0o600)
	})
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(root, ".complete"), nil, 0o600)
}
