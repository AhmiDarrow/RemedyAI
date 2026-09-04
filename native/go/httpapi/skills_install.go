package httpapi

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// importSkillZipQuarantine extracts a library skill zip into destRoot with
// quarantine metadata written into SKILL.md frontmatter.
func importSkillZipQuarantine(zipData []byte, destRoot string, entry *librarySkillEntry, replace bool) ([]string, error) {
	if entry == nil {
		return nil, fmt.Errorf("missing catalog entry")
	}
	zr, err := zip.NewReader(bytes.NewReader(zipData), int64(len(zipData)))
	if err != nil {
		return nil, fmt.Errorf("invalid zip: %w", err)
	}
	tmp, err := os.MkdirTemp("", "remedy-lib-install-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tmp)

	extractRoot := filepath.Join(tmp, "extract")
	if err := os.MkdirAll(extractRoot, 0o700); err != nil {
		return nil, err
	}
	if err := safeExtractZip(zr, extractRoot); err != nil {
		return nil, err
	}

	skillDirs := findSkillDirs(extractRoot)
	if len(skillDirs) == 0 {
		return nil, fmt.Errorf("no SKILL.md in zip")
	}

	var names []string
	for _, src := range skillDirs {
		rec, ok := loadSkillDir(src)
		if !ok {
			continue
		}
		name := rec.Name
		if !isSafeSkillName(name) {
			return nil, fmt.Errorf("unsafe skill name in pack: %s", name)
		}
		dest := filepath.Join(destRoot, name)
		destAbs, err := filepath.Abs(dest)
		if err != nil {
			return nil, err
		}
		rootAbs, err := filepath.Abs(destRoot)
		if err != nil {
			return nil, err
		}
		rel, err := filepath.Rel(rootAbs, destAbs)
		if err != nil || strings.HasPrefix(rel, "..") {
			return nil, fmt.Errorf("refusing path escape for skill %s", name)
		}
		if dirExists(dest) {
			if !replace {
				return nil, fmt.Errorf("skill '%s' already installed", name)
			}
			if err := os.RemoveAll(dest); err != nil {
				return nil, err
			}
		}
		if err := copyDir(src, dest); err != nil {
			return nil, err
		}
		if err := enrichLibraryFrontmatter(dest, entry, replace); err != nil {
			return nil, err
		}
		names = append(names, name)
	}
	if len(names) == 0 {
		return nil, fmt.Errorf("import produced no skills (invalid pack?)")
	}
	return names, nil
}

func safeExtractZip(zr *zip.Reader, dest string) error {
	destAbs, err := filepath.Abs(dest)
	if err != nil {
		return err
	}
	for _, f := range zr.File {
		name := filepath.Clean(f.Name)
		if name == "." || name == "" {
			continue
		}
		// Zip Slip: reject absolute / drive / parent escapes.
		if filepath.IsAbs(name) || strings.Contains(name, ":") {
			return fmt.Errorf("unsafe zip path: %s", f.Name)
		}
		target := filepath.Join(destAbs, name)
		rel, err := filepath.Rel(destAbs, target)
		if err != nil || strings.HasPrefix(rel, "..") {
			return fmt.Errorf("unsafe zip path: %s", f.Name)
		}
		if f.FileInfo().IsDir() {
			if err := os.MkdirAll(target, 0o700); err != nil {
				return err
			}
			continue
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		rc, err := f.Open()
		if err != nil {
			return err
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o600)
		if err != nil {
			_ = rc.Close()
			return err
		}
		_, copyErr := io.Copy(out, io.LimitReader(rc, maxSkillZipBytes))
		_ = out.Close()
		_ = rc.Close()
		if copyErr != nil {
			return copyErr
		}
	}
	return nil
}

func findSkillDirs(root string) []string {
	var out []string
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		if strings.EqualFold(d.Name(), "SKILL.md") {
			out = append(out, filepath.Dir(path))
		}
		return nil
	})
	return out
}

func copyDir(src, dst string) error {
	return filepath.WalkDir(src, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(src, path)
		if err != nil {
			return err
		}
		target := filepath.Join(dst, rel)
		if d.IsDir() {
			return os.MkdirAll(target, 0o700)
		}
		if err := os.MkdirAll(filepath.Dir(target), 0o700); err != nil {
			return err
		}
		in, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		return os.WriteFile(target, in, 0o600)
	})
}

func enrichLibraryFrontmatter(skillDir string, entry *librarySkillEntry, replaced bool) error {
	md := filepath.Join(skillDir, "SKILL.md")
	rawBytes, err := os.ReadFile(md)
	if err != nil {
		return err
	}
	raw := string(rawBytes)
	fm, body, ok := parseSkillFrontmatter(raw)
	_ = fm
	trust := "library-install"
	if replaced {
		trust = "library-update"
	}
	metaLines := []string{
		"metadata:",
		"  source: library",
		"  library_id: " + yamlScalar(entry.ID),
		"  library_version: " + yamlScalar(entry.Version),
		"  quarantine: true",
		"  trust: " + yamlScalar(trust),
	}
	if len(entry.SecurityFlags) > 0 {
		metaLines = append(metaLines, "  security_flags: ["+strings.Join(quoteYAMLList(entry.SecurityFlags), ", ")+"]")
	}

	var front string
	if ok {
		// Strip existing metadata block / quarantine keys from top-level, then append ours.
		cleaned := stripMetadataBlock(raw)
		m := frontmatterRE.FindStringSubmatch(cleaned)
		if m != nil {
			block := strings.TrimRight(m[1], "\n")
			front = "---\n" + block + "\n" + strings.Join(metaLines, "\n") + "\n---\n\n" + strings.TrimLeft(body, "\n")
		} else {
			front = "---\n" + strings.Join(metaLines, "\n") + "\n---\n\n" + strings.TrimLeft(raw, "\n")
		}
	} else {
		front = "---\nname: " + yamlScalar(entry.Name) + "\ndescription: " + yamlScalar(entry.Description) +
			"\nversion: " + yamlScalar(entry.Version) + "\n" + strings.Join(metaLines, "\n") + "\n---\n\n" + strings.TrimLeft(raw, "\n")
	}
	return os.WriteFile(md, []byte(front), 0o600)
}

func stripMetadataBlock(raw string) string {
	m := frontmatterRE.FindStringSubmatch(raw)
	if m == nil {
		return raw
	}
	body := raw[len(m[0]):]
	lines := splitYAMLLines(m[1])
	var kept []string
	skipUntilIndent := -1
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		ind := countIndent(line)
		if skipUntilIndent >= 0 {
			if trim != "" && ind > skipUntilIndent {
				continue
			}
			skipUntilIndent = -1
		}
		if ind == 0 {
			key, _, ok := splitKV(trim)
			if ok && strings.EqualFold(key, "metadata") {
				skipUntilIndent = 0
				continue
			}
		}
		kept = append(kept, line)
	}
	return "---\n" + strings.Join(kept, "\n") + "\n---\n" + body
}

func yamlScalar(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return `""`
	}
	if strings.ContainsAny(s, ":#{}[]&*!|>'\"%@`") || strings.Contains(s, " ") || strings.Contains(s, ",") {
		b, _ := json.Marshal(s)
		return string(b)
	}
	return s
}

func quoteYAMLList(items []string) []string {
	out := make([]string, 0, len(items))
	for _, it := range items {
		out = append(out, yamlScalar(it))
	}
	return out
}
