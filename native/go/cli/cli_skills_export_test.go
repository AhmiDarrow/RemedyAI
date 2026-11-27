package cli

import (
	"archive/zip"
	"bytes"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"testing"
	"time"
)

// The goldens under testdata/skills_export/golden were written by Python's
// SkillExporter (src/remedy/skills/exporter.py, PyYAML 6.0.3) for the
// fixtures under testdata/skills_export/src, with datetime.now pinned to
// skGoldenNow, after the CLI's default-status step: each fixture was loaded
// with load_skill_from_dir, passed through SkillRegistry._apply_default_status
// and exported with export_native/hermes/openclaw/zip(format="native") by
// the repo venv's python -I; the zip's entries were unpacked beside a .list
// of their names in order (deflate bytes and timestamps cannot match).
var skGoldenNow = time.Date(2026, 1, 2, 3, 4, 5, 678901000, time.UTC)

func skTreeFiles(t *testing.T, root string) map[string][]byte {
	t.Helper()
	out := map[string][]byte{}
	err := filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			return nil
		}
		rel, _ := filepath.Rel(root, p)
		raw, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		out[filepath.ToSlash(rel)] = raw
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return out
}

// skTextMode: Python's write_text writes \n as os.linesep, so on Windows the
// manifest files Python writes have CRLF; copied resources do not.
func skTextMode(rel string, raw []byte) []byte {
	base := filepath.Base(rel)
	if runtime.GOOS == "windows" && (base == "SKILL.md" || base == "skill.yaml") {
		return bytes.ReplaceAll(raw, []byte("\n"), []byte("\r\n"))
	}
	return raw
}

func skCompareTrees(t *testing.T, want, got map[string][]byte) {
	t.Helper()
	for rel, w := range want {
		g, ok := got[rel]
		if !ok {
			t.Errorf("missing %s", rel)
			continue
		}
		if w = skTextMode(rel, w); !bytes.Equal(g, w) {
			t.Errorf("%s differs from Python's:\n--- go\n%s\n--- python\n%s", rel, g, w)
		}
	}
	for rel := range got {
		if _, ok := want[rel]; !ok {
			t.Errorf("unexpected file %s", rel)
		}
	}
}

func TestCLISkillExportMatchesPythonExporterByteForByte(t *testing.T) {
	src := filepath.Join("testdata", "skills_export", "src")
	golden := filepath.Join("testdata", "skills_export", "golden")
	for _, name := range []string{"fixture-skill", "learned-fixture"} {
		sk, err := skLoadDir(filepath.Join(src, name))
		if err != nil {
			t.Fatal(err)
		}
		skApplyDefaultStatus(sk)
		for _, format := range []string{"native", "hermes", "openclaw"} {
			out := t.TempDir()
			e := &skExporter{out: out, now: func() time.Time { return skGoldenNow }}
			var dest string
			switch format {
			case "native":
				dest, err = e.native(sk)
			case "hermes":
				dest, err = e.hermes(sk)
			default:
				dest, err = e.openclaw(sk)
			}
			if err != nil {
				t.Fatalf("%s %s: %v", name, format, err)
			}
			want := skTreeFiles(t, filepath.Join(golden, format, filepath.Base(dest)))
			skCompareTrees(t, want, skTreeFiles(t, dest))
		}

		out := t.TempDir()
		e := &skExporter{out: out, now: func() time.Time { return skGoldenNow }}
		zpath, err := e.zip(sk)
		if err != nil {
			t.Fatal(err)
		}
		base := filepath.Base(zpath)
		list, err := os.ReadFile(filepath.Join(golden, "zip", base+".list"))
		if err != nil {
			t.Fatalf("golden for %s: %v", base, err)
		}
		zr, err := zip.OpenReader(zpath)
		if err != nil {
			t.Fatal(err)
		}
		var names []string
		got := map[string][]byte{}
		for _, f := range zr.File {
			names = append(names, f.Name)
			rc, err := f.Open()
			if err != nil {
				t.Fatal(err)
			}
			raw, _ := io.ReadAll(rc)
			_ = rc.Close()
			got[f.Name] = raw
			if f.Method != zip.Deflate {
				t.Errorf("%s: method %d, Python writes ZIP_DEFLATED", f.Name, f.Method)
			}
		}
		_ = zr.Close()
		if strings.Join(names, "\n")+"\n" != string(list) {
			t.Errorf("%s entries\n%q\nwant\n%q", base, names, string(list))
		}
		want := skTreeFiles(t, filepath.Join(golden, "zip", base+".entries"))
		// writestr / ZipFile.write store bytes as given: no newline translation.
		for rel, w := range want {
			if !bytes.Equal(got[rel], w) {
				t.Errorf("zip %s differs from Python's:\n--- go\n%s\n--- python\n%s", rel, got[rel], w)
			}
		}
		if len(want) != len(got) {
			keys := make([]string, 0, len(got))
			for k := range got {
				keys = append(keys, k)
			}
			sort.Strings(keys)
			t.Errorf("zip entries %v, want %d", keys, len(want))
		}
	}
}

func TestCLISkillExportPreservesResourceModeAndMtime(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "keep")
	skWriteFixture(t, dir, "---\nname: keep\ndescription: keeps file metadata\n---\nBody\n")
	script := filepath.Join(dir, "scripts", "run.sh")
	if err := os.MkdirAll(filepath.Dir(script), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(script, []byte("echo hi\n"), 0o750); err != nil {
		t.Fatal(err)
	}
	old := time.Date(2020, 5, 6, 7, 8, 9, 0, time.UTC)
	if err := os.Chtimes(script, old, old); err != nil {
		t.Fatal(err)
	}
	sk, err := skLoadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	dest, err := (&skExporter{out: t.TempDir(), now: time.Now}).native(sk)
	if err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(filepath.Join(dest, "scripts", "run.sh"))
	if err != nil {
		t.Fatal(err)
	}
	if !st.ModTime().Equal(old) {
		t.Errorf("mtime %v, want %v (shutil.copy2 keeps it)", st.ModTime(), old)
	}
	if runtime.GOOS != "windows" && st.Mode().Perm() != 0o750 {
		t.Errorf("mode %v, want 0750", st.Mode().Perm())
	}
}

// Python joined the skill's name onto the output folder, so a skill named
// "../escape" exported outside it.
func TestCLISkillExportRefusesANameThatLeavesTheOutputFolder(t *testing.T) {
	root := t.TempDir()
	out := filepath.Join(root, "out")
	if err := os.MkdirAll(out, 0o755); err != nil {
		t.Fatal(err)
	}
	sk := &skSkill{Name: "../escape", Description: "x", Version: "1.0.0", Status: "active"}
	e := &skExporter{out: out, now: time.Now}
	for _, f := range []func(*skSkill) (string, error){e.native, e.hermes, e.openclaw, e.zip} {
		if _, err := f(sk); err == nil {
			t.Error("export of ../escape succeeded")
		}
	}
	sk = &skSkill{Name: "ok", Description: "x", Version: "1/../../evil", Status: "active"}
	if _, err := e.zip(sk); err == nil {
		t.Error("zip with a version holding a path succeeded")
	}
	entries, _ := os.ReadDir(root)
	if len(entries) != 1 {
		t.Errorf("something was written beside the output folder: %v", entries)
	}
}

// Python's is_file() + copy2 followed a symlink in scripts/ to anywhere, so an
// exported skill could carry a copy of the owner's SSH key.
func TestCLISkillExportSkipsResourcesLinkedOutsideTheSkill(t *testing.T) {
	root := t.TempDir()
	secret := filepath.Join(root, "id_rsa")
	if err := os.WriteFile(secret, []byte("PRIVATE KEY"), 0o600); err != nil {
		t.Fatal(err)
	}
	dir := filepath.Join(root, "linky")
	skWriteFixture(t, dir, "---\nname: linky\ndescription: has a symlink\n---\nBody\n")
	if err := os.MkdirAll(filepath.Join(dir, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "scripts", "ok.py"), []byte("print(1)\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(secret, filepath.Join(dir, "scripts", "key.txt")); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}
	sk, err := skLoadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	e := &skExporter{out: filepath.Join(root, "out"), now: time.Now}
	dest, err := e.native(sk)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dest, "scripts", "key.txt")); err == nil {
		t.Error("the linked secret was copied into the export")
	}
	if _, err := os.Stat(filepath.Join(dest, "scripts", "ok.py")); err != nil {
		t.Errorf("the skill's own script was not copied: %v", err)
	}
	if err := os.MkdirAll(e.out, 0o755); err != nil {
		t.Fatal(err)
	}
	zpath, err := e.zip(sk)
	if err != nil {
		t.Fatal(err)
	}
	zr, err := zip.OpenReader(zpath)
	if err != nil {
		t.Fatal(err)
	}
	defer zr.Close()
	for _, f := range zr.File {
		if strings.HasSuffix(f.Name, "key.txt") {
			t.Error("the linked secret was put in the zip")
		}
	}
}

func skWriteFixture(t *testing.T, dir, skillMD string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte(skillMD), 0o644); err != nil {
		t.Fatal(err)
	}
}
