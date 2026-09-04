package httpapi

import (
	"os"
	"path/filepath"
	"strings"
)

// countSkills counts unique skill directories that contain SKILL.md.
// Prefer $home/skills (seeded/user); also scan bundled/repo roots when discoverable.
func countSkills(homeDir string) int {
	names := map[string]struct{}{}
	roots := skillSearchRoots(homeDir)
	for _, root := range roots {
		walkSkillMDs(root, names)
	}
	return len(names)
}

func skillSearchRoots(homeDir string) []string {
	roots := make([]string, 0, 8)
	home := ResolveHomeDir(homeDir)
	if home != "" {
		roots = append(roots, filepath.Join(home, "skills"))
	}
	if cwd, err := os.Getwd(); err == nil {
		for _, p := range parentsOf(cwd) {
			roots = append(roots,
				filepath.Join(p, "skills"),
				filepath.Join(p, "src", "remedy", "bundled_skills"),
			)
		}
	}
	if exe, err := os.Executable(); err == nil {
		for _, p := range parentsOf(exe) {
			roots = append(roots,
				filepath.Join(p, "skills"),
				filepath.Join(p, "src", "remedy", "bundled_skills"),
				filepath.Join(p, "bundled_skills"),
			)
		}
	}
	seen := map[string]struct{}{}
	out := make([]string, 0, len(roots))
	for _, r := range roots {
		if r == "" {
			continue
		}
		key := r
		if abs, err := filepath.Abs(r); err == nil {
			key = abs
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		if st, err := os.Stat(key); err == nil && st.IsDir() {
			out = append(out, key)
		}
	}
	return out
}

func walkSkillMDs(root string, names map[string]struct{}) {
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if d.IsDir() {
			base := d.Name()
			if base == ".git" || base == "node_modules" || base == "__pycache__" {
				return filepath.SkipDir
			}
			return nil
		}
		if !strings.EqualFold(d.Name(), "SKILL.md") {
			return nil
		}
		name := filepath.Base(filepath.Dir(path))
		if name == "" || name == "." || name == string(filepath.Separator) {
			return nil
		}
		names[strings.ToLower(name)] = struct{}{}
		return nil
	})
}
