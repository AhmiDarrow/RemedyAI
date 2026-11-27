package cli

import (
	"archive/zip"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// Port of src/remedy/skills/exporter.py (SkillExporter.export_native,
// export_hermes, export_openclaw, export_zip). The files written are the
// same bytes Python wrote; cli_skills_export_test.go holds goldens captured
// from the Python exporter.

// skExporter writes into out; now is datetime.now(UTC) (hermes stamps it).
type skExporter struct {
	out string
	now func() time.Time
}

// skISO is datetime.isoformat() of an aware UTC time.
func skISO(t time.Time) string {
	t = t.UTC()
	s := t.Format("2006-01-02T15:04:05")
	if us := t.Nanosecond() / 1000; us != 0 {
		s += fmt.Sprintf(".%06d", us)
	}
	return s + "+00:00"
}

func skStrList(l []string) []any {
	out := make([]any, len(l))
	for i, s := range l {
		out[i] = s
	}
	return out
}

func skAuthorOr(sk *skSkill) string {
	if sk.Author != nil && *sk.Author != "" {
		return *sk.Author
	}
	return "Remedy"
}

// skExportFrontmatter is _build_frontmatter.
func skExportFrontmatter(sk *skSkill) skMap {
	fm := skMap{
		{"name", sk.Name},
		{"description", sk.Description},
		{"version", sk.Version},
		{"author", skAuthorOr(sk)},
		{"tags", skStrList(sk.Tags)},
		{"requires", skStrList(sk.Requires)},
		{"tools", skStrList(sk.Tools)},
	}
	if sk.Status != "" {
		fm.set("status", sk.Status)
	}
	if len(sk.Meta) > 0 {
		fm.set("metadata", append(skMap(nil), sk.Meta...))
	}
	return fm
}

func skNativeSkillMD(sk *skSkill) string {
	return "---\n" + skPyStrip(skYAMLDump(skExportFrontmatter(sk), skDumpOpts{sortKeys: true})) + "\n---\n\n" + sk.Instructions
}

// skCheckNames refuses a skill name (and, for zip, version) that would not be
// a single path segment under the output folder.
func skCheckNames(sk *skSkill, parts ...string) error {
	for _, p := range parts {
		if !skSafeSegment(p) {
			return fmt.Errorf("refusing to export skill %q: %q is not a single safe path segment", sk.Name, p)
		}
	}
	return nil
}

// skResources lists the scripts and references to copy: (relative path,
// absolute source). Python's is_file() + copy2 followed symlinks, so a link
// in scripts/ to a file anywhere (an SSH key) was copied into the export; a
// resource whose real path leaves the skill folder is skipped here.
func skResources(sk *skSkill) [][2]string {
	if sk.Dir == "" {
		return nil
	}
	base := skResolvePath(sk.Dir)
	var out [][2]string
	for _, rel := range append(append([]string(nil), sk.Scripts...), sk.References...) {
		src := filepath.Join(sk.Dir, rel)
		if !skIsFile(src) {
			continue
		}
		real := skResolvePath(src)
		if r, err := filepath.Rel(base, real); err != nil || r == ".." || strings.HasPrefix(r, ".."+string(filepath.Separator)) || filepath.IsAbs(r) {
			continue
		}
		out = append(out, [2]string{rel, src})
	}
	return out
}

func (e *skExporter) copyResources(sk *skSkill, dest string) error {
	for _, r := range skResources(sk) {
		target := filepath.Join(dest, r[0])
		if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
			return err
		}
		if err := skCopy2(r[1], target); err != nil {
			return err
		}
	}
	return nil
}

func (e *skExporter) native(sk *skSkill) (string, error) {
	if err := skCheckNames(sk, sk.Name); err != nil {
		return "", err
	}
	dest := filepath.Join(e.out, sk.Name)
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return "", err
	}
	if err := skWriteText(filepath.Join(dest, "SKILL.md"), skNativeSkillMD(sk)); err != nil {
		return "", err
	}
	return dest, e.copyResources(sk, dest)
}

func (e *skExporter) hermes(sk *skSkill) (string, error) {
	if err := skCheckNames(sk, sk.Name); err != nil {
		return "", err
	}
	dest := filepath.Join(e.out, "hermes_"+sk.Name)
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return "", err
	}
	fm := skMap{
		{"name", sk.Name},
		{"description", sk.Description},
		{"version", sk.Version},
		{"author", skAuthorOr(sk)},
		{"tags", skStrList(sk.Tags)},
		{"hermes_version", "2.0"},
		{"remedy_exported", skISO(e.now())},
	}
	content := "---\n" + skYAMLDump(fm, skDumpOpts{sortKeys: true}) + "---\n\n" + sk.Instructions
	if err := skWriteText(filepath.Join(dest, "SKILL.md"), content); err != nil {
		return "", err
	}
	return dest, e.copyResources(sk, dest)
}

func (e *skExporter) openclaw(sk *skSkill) (string, error) {
	if err := skCheckNames(sk, sk.Name); err != nil {
		return "", err
	}
	dest := filepath.Join(e.out, "openclaw_"+sk.Name)
	if err := os.MkdirAll(dest, 0o755); err != nil {
		return "", err
	}
	manifest := skMap{
		{"name", sk.Name},
		{"title", sk.Name},
		{"description", sk.Description},
		{"version", sk.Version},
		{"author", skAuthorOr(sk)},
		{"tags", skStrList(sk.Tags)},
		{"instructions", sk.Instructions},
		{"type", "skill"},
		{"source", "remedy-export"},
	}
	if err := skWriteText(filepath.Join(dest, "skill.yaml"), skYAMLDump(manifest, skDumpOpts{sortKeys: true})); err != nil {
		return "", err
	}
	return dest, e.copyResources(sk, dest)
}

// zip is export_zip(format="native"): <name>/SKILL.md (written as is, no
// newline translation, as ZipFile.writestr does) then the resources. Entry
// names and contents match Python; the deflate stream and timestamps differ.
func (e *skExporter) zip(sk *skSkill) (string, error) {
	file := sk.Name + "_" + sk.Version + ".zip"
	if err := skCheckNames(sk, sk.Name, file); err != nil {
		return "", err
	}
	zipPath := filepath.Join(e.out, file)
	f, err := os.Create(zipPath)
	if err != nil {
		return "", err
	}
	zw := zip.NewWriter(f)
	fail := func(err error) (string, error) {
		_ = zw.Close()
		_ = f.Close()
		return "", err
	}
	w, err := zw.CreateHeader(&zip.FileHeader{Name: sk.Name + "/SKILL.md", Method: zip.Deflate, Modified: e.now()})
	if err != nil {
		return fail(err)
	}
	if _, err := io.WriteString(w, skNativeSkillMD(sk)); err != nil {
		return fail(err)
	}
	for _, r := range skResources(sk) {
		st, err := os.Stat(r[1])
		if err != nil {
			return fail(err)
		}
		h, err := zip.FileInfoHeader(st)
		if err != nil {
			return fail(err)
		}
		h.Name = sk.Name + "/" + filepath.ToSlash(r[0])
		h.Method = zip.Deflate
		w, err := zw.CreateHeader(h)
		if err != nil {
			return fail(err)
		}
		src, err := os.Open(r[1])
		if err != nil {
			return fail(err)
		}
		_, err = io.Copy(w, src)
		_ = src.Close()
		if err != nil {
			return fail(err)
		}
	}
	if err := zw.Close(); err != nil {
		_ = f.Close()
		return "", err
	}
	return zipPath, f.Close()
}
