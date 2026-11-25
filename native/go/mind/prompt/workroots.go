package prompt

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"unicode/utf8"
)

// Port of work_roots.work_roots_context_block for a single focus folder,
// with project_fingerprint.orientation_block and the non-game part of
// fingerprint_path. Left out on purpose: game-engine fingerprints (Godot,
// Unity, Unreal, LÖVE, Bevy, Phaser/Pixi, pygame/arcade) and the research
// fingerprint; a folder only those would recognise reads as unrecognised.

const (
	workRootsCap     = 3200 // work_roots_context_block max_chars
	orientCap        = 1200 // min(1_200, budget) passed to orientation_block
	orientMaxBytes   = 6000
	orientExcerptLen = 280
)

var orientNames = []string{"AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", "README.md", "README.rst", "README.txt"}

// workRootsBlock is the "## Active work tree" context block for a project
// folder, or "" when there is no folder or nothing is recognised.
func workRootsBlock(projectDir string) string {
	if pyStrip(projectDir) == "" {
		return ""
	}
	home, _ := os.UserHomeDir()
	root := resolvePath(projectDir, home)
	if fi, err := os.Stat(root); err != nil || !fi.IsDir() {
		return ""
	}
	var parts []string
	if o := orientBlock(root, min(orientCap, workRootsCap)); o != "" {
		parts = append(parts, o)
	}
	if lines := fingerprintLines(root); len(lines) > 0 {
		parts = append(parts, strings.Join(lines, "\n"))
	}
	if len(parts) == 0 {
		return ""
	}
	block := "## Active work tree\n" + root + "\n" + strings.Join(parts, "\n")
	if cpLen(block) > workRootsCap {
		block = cut(block, workRootsCap-1) + "…"
	}
	return block
}

// readReplace is read_text(errors="replace") plus universal newlines.
func readReplace(path string) (string, bool) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", false
	}
	var b strings.Builder
	for i := 0; i < len(data); {
		r, n := utf8.DecodeRune(data[i:])
		if r == utf8.RuneError && n == 1 {
			b.WriteRune('�')
		} else {
			b.Write(data[i : i+n])
		}
		i += n
	}
	s := strings.ReplaceAll(b.String(), "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n"), true
}

func isFile(path string) bool {
	fi, err := os.Stat(path)
	return err == nil && fi.Mode().IsRegular()
}

func fileSize(path string) int64 {
	if fi, err := os.Stat(path); err == nil {
		return fi.Size()
	}
	return 0
}

// firstLine is splitlines()[0] for already stripped text.
func firstLine(s string) string {
	if i := strings.IndexAny(s, "\n\r\v\f\x1c\x1d\x1e\u0085  "); i >= 0 {
		return s[:i]
	}
	return s
}

func orientBlock(root string, maxChars int) string {
	lines := []string{"Orientation (read these first if relevant):"}
	found := 0
	for _, name := range orientNames {
		p := filepath.Join(root, name)
		if !isFile(p) {
			continue
		}
		found++
		if size := fileSize(p); size > orientMaxBytes {
			lines = append(lines, "- "+name+" ("+strconv.FormatInt(size, 10)+" bytes) — file_read path for sections as needed")
			continue
		}
		text, ok := readReplace(p)
		if !ok {
			lines = append(lines, "- "+name+" (unreadable)")
			continue
		}
		excerpt := strings.Join(strings.Fields(text), " ")
		if cpLen(excerpt) > orientExcerptLen {
			excerpt = cut(excerpt, orientExcerptLen-1) + "…"
		}
		if excerpt != "" {
			lines = append(lines, "- "+name+": "+excerpt)
		} else {
			lines = append(lines, "- "+name)
		}
	}

	if latest := filepath.Join(root, "memory", "LATEST_HANDOFF.md"); isFile(latest) {
		found++
		target := ""
		if text, ok := readReplace(latest); ok {
			target = pyStrip(firstLine(pyStrip(text)))
		}
		if target == "" {
			lines = append(lines, "- memory/LATEST_HANDOFF.md → (empty pointer)")
		} else {
			lines = append(lines, "- memory/LATEST_HANDOFF.md → "+target)
			rel := "memory/SESSION_NOTES/" + target
			if !isFile(filepath.Join(root, filepath.FromSlash(rel))) {
				rel = "memory/" + target
			}
			if !isFile(filepath.Join(root, filepath.FromSlash(rel))) && !strings.HasSuffix(target, ".md") {
				rel = "memory/SESSION_NOTES/" + target + ".md"
			}
			if isFile(filepath.Join(root, filepath.FromSlash(rel))) {
				lines = append(lines, "  handoff file: "+rel+" (file_read before continuing long work)")
			}
		}
	}

	if arch := filepath.Join(root, "docs", "ARCHITECTURE.md"); isFile(arch) {
		found++
		lines = append(lines, "- docs/ARCHITECTURE.md ("+strconv.FormatInt(fileSize(arch), 10)+" bytes) — read when changing system design")
	}
	if found == 0 {
		return ""
	}
	block := strings.Join(lines, "\n")
	if cpLen(block) > maxChars {
		block = cut(block, maxChars-1) + "…"
	}
	return block
}

// fingerprintLines is StackFingerprint.context_lines without engine/research.
func fingerprintLines(root string) []string {
	has := func(name string) bool { _, err := os.Stat(filepath.Join(root, name)); return err == nil }
	var stacks, hints []string
	verify := ""
	setVerify := func(v string) {
		if verify == "" {
			verify = v
		}
	}
	if has("package.json") {
		stacks = append(stacks, "node")
		hints = append(hints, "Node: prefer npm/pnpm/yarn scripts from package.json")
		switch {
		case has("pnpm-lock.yaml"):
			setVerify("pnpm test")
		case has("yarn.lock"):
			setVerify("yarn test")
		default:
			setVerify("npm test")
		}
	}
	if has("pyproject.toml") || has("pytest.ini") || has("setup.py") || has("setup.cfg") {
		stacks = append(stacks, "python")
		hints = append(hints, "Python: uv run pytest / pytest -q when tests exist")
		if fi, err := os.Stat(filepath.Join(root, ".venv")); has("uv.lock") || (err == nil && fi.IsDir()) {
			setVerify("uv run pytest -q")
		} else {
			setVerify("pytest -q")
		}
	}
	if has("Cargo.toml") {
		stacks = append(stacks, "rust")
		hints = append(hints, "Rust: cargo test / cargo check")
		setVerify("cargo test")
	}
	if has("go.mod") {
		stacks = append(stacks, "go")
		hints = append(hints, "Go: go test ./…")
		setVerify("go test ./...")
	}
	if has("Makefile") || has("makefile") {
		stacks = append(stacks, "make")
		hints = append(hints, "Makefile present — check targets before inventing commands")
		setVerify("make test")
	}
	if has("justfile") || has("Justfile") {
		stacks = append(stacks, "just")
		setVerify("just test")
	}
	if entries, err := os.ReadDir(root); err == nil {
		for _, e := range entries {
			if n := e.Name(); strings.HasSuffix(n, ".sln") || strings.HasSuffix(n, ".csproj") {
				stacks = append(stacks, "dotnet")
				hints = append(hints, "dotnet: dotnet test")
				setVerify("dotnet test")
				break
			}
		}
	}
	if len(stacks) == 0 && len(hints) == 0 {
		return nil
	}
	lines := []string{"Stack fingerprint (" + root + "):"}
	if len(stacks) > 0 {
		lines = append(lines, "  stacks: "+strings.Join(stacks, ", "))
	}
	if verify != "" {
		lines = append(lines, "  suggested verify: "+verify)
	}
	for _, h := range hints[:min(6, len(hints))] {
		lines = append(lines, "  - "+h)
	}
	return lines
}
