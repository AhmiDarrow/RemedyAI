package cli

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
)

// Port of src/remedy/skills/loader.py, the adapters, and the parts of
// registry.py the CLI uses. Which skills exist, and where, comes from the
// runtime's own discovery (httpapi.CLIDiscoverSkills) so `remedy skill list`
// and a running remedy-runtime agree; this file reads each one the way
// Python's loader did, for the fields only the CLI shows.

// skSkill is remedy.models.Skill with its SkillManifest.
type skSkill struct {
	Name, Description, Version string
	Author                     *string // None when nil
	Tags, Requires, Tools      []string
	Kind                       string // native | hermes | openclaw
	Status                     string // a SkillStatus value
	Meta                       skMap
	Path                       string // manifest.path: the manifest's folder
	Instructions               string
	Scripts, References        []string // relative to Dir, OS separators
	Dir                        string   // source_skill_dir (absolute)
}

// skLoadError is SkillLoadError.
type skLoadError struct{ msg string }

func (e *skLoadError) Error() string { return e.msg }

func skLoadErr(format string, a ...any) error { return &skLoadError{fmt.Sprintf(format, a...)} }

var skStatuses = []string{"discovered", "validated", "active", "disabled", "deprecated", "archived"}

// Python's \s for str patterns (str.isspace characters).
const skPyWS = `[\t\n\v\f\r \x{1c}-\x{1f}\x{85}\p{Z}]`

var (
	skReFrontmatter      = regexp.MustCompile(`(?s)^---` + skPyWS + `*\n(.*?)\n---` + skPyWS + `*\n`)
	skReFrontmatterLoose = regexp.MustCompile(`(?s)^---` + skPyWS + `*\n(.*?)\n---` + skPyWS + `*\n?`)
)

func skIsPySpace(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) }

// skPyStrip is str.strip().
func skPyStrip(s string) string { return strings.TrimFunc(s, skIsPySpace) }

// skReadText is Path.read_text(encoding="utf-8"): strict UTF-8 with universal
// newlines (\r\n and \r read as \n).
func skReadText(path string) (string, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	if !utf8.Valid(raw) {
		return "", fmt.Errorf("'utf-8' codec can't decode %s", path)
	}
	s := strings.ReplaceAll(string(raw), "\r\n", "\n")
	return strings.ReplaceAll(s, "\r", "\n"), nil
}

// skWriteText is Path.write_text(s, encoding="utf-8"): \n is written as the
// platform line separator, as Python's text mode does.
func skWriteText(path, s string) error {
	if runtime.GOOS == "windows" {
		s = strings.ReplaceAll(s, "\n", "\r\n")
	}
	return os.WriteFile(path, []byte(s), 0o644)
}

func skIsFile(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.Mode().IsRegular()
}

func skIsDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}

// skParseSkillMD is _parse_skill_md: (frontmatter, body). Without frontmatter
// the body is the whole file, unstripped.
func skParseSkillMD(path string) (skMap, string, error) {
	raw, err := skReadText(path)
	if err != nil {
		return nil, "", skLoadErr("Cannot read %s: %v", path, err)
	}
	m := skReFrontmatter.FindStringSubmatchIndex(raw)
	if m == nil {
		return skMap{}, raw, nil
	}
	v, err := skYAMLLoad(raw[m[2]:m[3]])
	if err != nil {
		return nil, "", skLoadErr("Invalid YAML frontmatter in %s: %v", path, err)
	}
	fm, _ := v.(skMap) // a non-mapping document has no 'name'
	if fm == nil {
		fm = skMap{}
	}
	return fm, skPyStrip(raw[m[1]:]), nil
}

// skParseStatus is _parse_status.
func skParseStatus(v any, has bool) string {
	if !has || v == nil {
		return "discovered"
	}
	t := strings.ToLower(skPyStrip(skPyStr(v)))
	for _, st := range skStatuses {
		if st == t {
			return st
		}
	}
	return "discovered"
}

// skStrField is a pydantic `str` field: only a string validates.
func skStrField(fm skMap, key, path string) (string, error) {
	v, _ := fm.get(key)
	s, ok := v.(string)
	if !ok {
		return "", skLoadErr("Invalid skill manifest in %s: '%s' must be a string", path, key)
	}
	return s, nil
}

// skStrListField is `frontmatter.get(key, []) or []` into a list[str] field.
func skStrListField(fm skMap, key, path string) ([]string, error) {
	v, _ := fm.get(key)
	if !skTruthy(v) {
		return []string{}, nil
	}
	l, ok := v.([]any)
	if !ok {
		return nil, skLoadErr("Invalid skill manifest in %s: '%s' must be a list of strings", path, key)
	}
	out := make([]string, 0, len(l))
	for _, x := range l {
		s, ok := x.(string)
		if !ok {
			return nil, skLoadErr("Invalid skill manifest in %s: '%s' must be a list of strings", path, key)
		}
		out = append(out, s)
	}
	return out, nil
}

var skPromotedMeta = []string{
	"auto_generated", "effort_weight", "effort_band", "effort_reasons", "lifecycle",
	"source_trace_id", "source_trace_ids", "parent_skill", "quarantine", "trust",
}

// skBuildManifest is _build_manifest. Trigger regexes are not compiled: Go's
// regexp is not Python's re, and a valid Python pattern must not fail here.
func skBuildManifest(fm skMap, kind, manifestPath string) (*skSkill, error) {
	if !fm.has("name") {
		return nil, skLoadErr("SKILL.md at %s is missing required 'name' field in frontmatter", manifestPath)
	}
	if !fm.has("description") {
		return nil, skLoadErr("SKILL.md at %s is missing required 'description' field in frontmatter", manifestPath)
	}
	sk := &skSkill{Kind: kind, Path: filepath.Dir(manifestPath)}
	var err error
	if sk.Name, err = skStrField(fm, "name", manifestPath); err != nil {
		return nil, err
	}
	if sk.Description, err = skStrField(fm, "description", manifestPath); err != nil {
		return nil, err
	}
	sk.Version = "1.0.0"
	if v, ok := fm.get("version"); ok {
		sk.Version = skPyStr(v)
	}
	if v, _ := fm.get("author"); v != nil {
		s, ok := v.(string)
		if !ok {
			return nil, skLoadErr("Invalid skill manifest in %s: 'author' must be a string", manifestPath)
		}
		sk.Author = &s
	}
	for _, key := range []string{"license", "homepage", "repository"} {
		if v, _ := fm.get(key); v != nil {
			if _, ok := v.(string); !ok {
				return nil, skLoadErr("Invalid skill manifest in %s: '%s' must be a string", manifestPath, key)
			}
		}
	}
	if sk.Tags, err = skStrListField(fm, "tags", manifestPath); err != nil {
		return nil, err
	}
	if sk.Requires, err = skStrListField(fm, "requires", manifestPath); err != nil {
		return nil, err
	}
	if sk.Tools, err = skStrListField(fm, "tools", manifestPath); err != nil {
		return nil, err
	}
	st, has := fm.get("status")
	sk.Status = skParseStatus(st, has)
	mv, _ := fm.get("metadata")
	meta, _ := mv.(skMap)
	meta = append(skMap(nil), meta...)
	for _, k := range skPromotedMeta {
		if v, ok := fm.get(k); ok && !meta.has(k) {
			meta.set(k, v)
		}
	}
	sk.Meta = meta
	return sk, nil
}

// skBundledResources is _discover_bundled_resources: files under scripts/
// and references/, sorted. Like Path.rglob it does not descend into a
// symlinked directory; a symlink to a file counts as a file.
func skBundledResources(dir string) (scripts, refs []string) {
	walk := func(sub string) []string {
		root := filepath.Join(dir, sub)
		if !skIsDir(root) {
			return nil
		}
		out := []string{}
		_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
			if err != nil || p == root {
				return nil
			}
			if d.IsDir() {
				return nil
			}
			if skIsFile(p) {
				if rel, err := filepath.Rel(dir, p); err == nil {
					out = append(out, rel)
				}
			}
			return nil
		})
		sort.Strings(out)
		return out
	}
	return walk("scripts"), walk("references")
}

// skResolve is Path.resolve(): absolute, symlinks followed as far as the
// path exists (non-strict).
func skResolvePath(p string) string {
	abs, err := filepath.Abs(p)
	if err != nil {
		return p
	}
	cur := abs
	var tail []string
	for {
		if real, err := filepath.EvalSymlinks(cur); err == nil {
			for i := len(tail) - 1; i >= 0; i-- {
				real = filepath.Join(real, tail[i])
			}
			return real
		}
		parent := filepath.Dir(cur)
		if parent == cur {
			return abs
		}
		tail = append(tail, filepath.Base(cur))
		cur = parent
	}
}

// skLoadDir is load_skill_from_dir (kind native).
func skLoadDir(dir string) (*skSkill, error) {
	if !skIsDir(dir) {
		return nil, skLoadErr("Skill directory not found: %s", dir)
	}
	md := filepath.Join(dir, "SKILL.md")
	if !skIsFile(md) {
		return nil, skLoadErr("No SKILL.md found in %s. Ensure the skill follows the agentskills.io format.", dir)
	}
	fm, body, err := skParseSkillMD(md)
	if err != nil {
		return nil, err
	}
	sk, err := skBuildManifest(fm, "native", md)
	if err != nil {
		return nil, err
	}
	sk.Instructions = body
	sk.Scripts, sk.References = skBundledResources(dir)
	sk.Dir = skResolvePath(dir)
	return sk, nil
}

// skLoadHermes is load_hermes_skill.
func skLoadHermes(dir string) (*skSkill, error) {
	root := skResolvePath(skExpandUser(dir))
	if !skIsDir(root) {
		return nil, skLoadErr("Hermes skill directory not found: %s", root)
	}
	md := filepath.Join(root, "SKILL.md")
	if !skIsFile(md) {
		return nil, skLoadErr("No SKILL.md found in Hermes skill dir: %s", root)
	}
	fm, body, err := skParseSkillMD(md)
	if err != nil {
		return nil, err
	}
	if !fm.has("name") && fm.has("skill_name") {
		v, _ := fm.pop("skill_name")
		fm.set("name", v)
	}
	if !fm.has("version") && fm.has("hermes_version") {
		v, _ := fm.pop("hermes_version")
		fm.set("version", v)
	}
	sk, err := skBuildManifest(fm, "hermes", md)
	if err != nil {
		return nil, err
	}
	sk.Instructions = body
	sk.Scripts, sk.References = skBundledResources(root)
	sk.Dir = root
	return sk, nil
}

// skLoadOpenClaw is load_openclaw_skill: SKILL.md, then skill.md, then
// skill.yaml (title standing in for name in the last two).
func skLoadOpenClaw(dir string) (*skSkill, error) {
	root := skResolvePath(skExpandUser(dir))
	if !skIsDir(root) {
		return nil, skLoadErr("OpenClaw skill directory not found: %s", root)
	}
	titleAsName := func(fm skMap) {
		if !fm.has("name") && fm.has("title") {
			v, _ := fm.pop("title")
			fm.set("name", v)
		}
	}
	var fm skMap
	var body, manifest string
	switch upper, lower, yml := filepath.Join(root, "SKILL.md"), filepath.Join(root, "skill.md"), filepath.Join(root, "skill.yaml"); {
	case skIsFile(upper):
		var err error
		if fm, body, err = skParseSkillMD(upper); err != nil {
			return nil, err
		}
		manifest = upper
	case skIsFile(lower):
		var err error
		if fm, body, err = skParseSkillMD(lower); err != nil {
			return nil, err
		}
		titleAsName(fm)
		manifest = lower
	case skIsFile(yml):
		raw, err := skReadText(yml)
		if err != nil {
			return nil, skLoadErr("Cannot read %s: %v", yml, err)
		}
		v, err := skYAMLLoad(raw)
		if err != nil {
			return nil, skLoadErr("Invalid YAML in %s: %v", yml, err)
		}
		fm, _ = v.(skMap)
		if fm == nil {
			fm = skMap{}
		}
		titleAsName(fm)
		manifest = yml
	default:
		return nil, skLoadErr("No SKILL.md, skill.md, or skill.yaml found in %s", root)
	}
	sk, err := skBuildManifest(fm, "openclaw", manifest)
	if err != nil {
		return nil, err
	}
	sk.Instructions = body
	sk.Scripts, sk.References = skBundledResources(root)
	sk.Dir = root
	return sk, nil
}

func skExpandUser(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		if uh, err := os.UserHomeDir(); err == nil {
			return filepath.Join(uh, p[1:])
		}
	}
	return p
}

// skDiscoverFlat is discover_skills_flat: immediate, non-hidden subfolders.
// A folder that fails to load is skipped.
func skDiscoverFlat(base string, load func(string) (*skSkill, error)) []*skSkill {
	base = skResolvePath(skExpandUser(base))
	entries, err := os.ReadDir(base)
	if err != nil {
		return nil
	}
	var out []*skSkill
	for _, e := range entries {
		p := filepath.Join(base, e.Name())
		if strings.HasPrefix(e.Name(), ".") || !skIsDir(p) {
			continue
		}
		if sk, err := load(p); err == nil {
			out = append(out, sk)
		}
	}
	return out
}

// skDiscoverTree is loader.discover_skills: every SKILL.md under root
// (recurse) or root's own. Folders that fail to load are skipped. Like
// pathlib's "**" it does not descend into symlinked folders; the order is
// lexical (Python's is the filesystem's).
func skDiscoverTree(root string, recurse bool) []*skSkill {
	isManifest := func(name string) bool {
		if runtime.GOOS == "windows" { // pathlib globbing is case-insensitive there
			return strings.EqualFold(name, "SKILL.md")
		}
		return name == "SKILL.md"
	}
	var dirs []string
	if !recurse {
		if entries, err := os.ReadDir(root); err == nil {
			for _, e := range entries {
				if isManifest(e.Name()) {
					dirs = append(dirs, root)
					break
				}
			}
		}
	} else {
		_ = filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if !d.IsDir() && isManifest(d.Name()) {
				dirs = append(dirs, filepath.Dir(p))
			}
			return nil
		})
	}
	var out []*skSkill
	for _, d := range dirs {
		if sk, err := skLoadDir(d); err == nil {
			out = append(out, sk)
		}
	}
	return out
}

// skRegistry is SkillRegistry's name-keyed, insertion-ordered store: a
// re-registered name moves to the end.
type skRegistry struct{ skills []*skSkill }

func (r *skRegistry) register(sk *skSkill) {
	for i, s := range r.skills {
		if s.Name == sk.Name {
			r.skills = append(r.skills[:i], r.skills[i+1:]...)
			break
		}
	}
	r.skills = append(r.skills, sk)
}

// get is SkillRegistry.get (exact name), falling back to a case-insensitive
// match the way the runtime's findSkill does.
func (r *skRegistry) get(name string) *skSkill {
	for _, s := range r.skills {
		if s.Name == name {
			return s
		}
	}
	for _, s := range r.skills {
		if strings.EqualFold(s.Name, name) {
			return s
		}
	}
	return nil
}

// discover is SkillRegistry.discover for one path; it returns how many
// skills it registered.
func (r *skRegistry) discover(path string, recurse bool) int {
	root := skResolvePath(skExpandUser(path))
	if _, err := os.Stat(root); err != nil {
		return 0
	}
	n := 0
	for _, sk := range skDiscoverTree(root, recurse) {
		r.register(sk)
		n++
	}
	return n
}

// skApplyDefaultStatus is SkillRegistry._apply_default_status.
func skApplyDefaultStatus(sk *skSkill) {
	if sk.Status == "disabled" || sk.Status == "deprecated" {
		return
	}
	if v, _ := sk.Meta.get("auto_generated"); skTruthy(v) {
		return
	}
	if v, _ := sk.Meta.get("quarantine"); skTruthy(v) {
		return
	}
	if sk.Status == "discovered" || sk.Status == "validated" || sk.Status == "active" {
		sk.Status = "active"
	}
}

// skDefaults is SkillRegistry.discover_defaults(home) for the CLI: seeded
// copies are refreshed from newer curated sources (registry.py 61-131), then
// the runtime's own discovery says which skills exist and where; each is
// read with the Python loader for the fields the CLI prints.
func skDefaults(home string) *skRegistry {
	userRoot := filepath.Join(home, "skills")
	_ = os.MkdirAll(userRoot, 0o700)
	for _, seed := range httpapi.CLISkillSeedRoots(home) {
		entries, err := os.ReadDir(seed)
		if err != nil {
			continue
		}
		for _, e := range entries {
			child := filepath.Join(seed, e.Name())
			if strings.HasPrefix(e.Name(), ".") || !skIsDir(child) || !skIsFile(filepath.Join(child, "SKILL.md")) {
				continue
			}
			dest := filepath.Join(userRoot, e.Name())
			if _, err := os.Lstat(dest); err != nil {
				continue // missing: the runtime's discovery seeds it below
			}
			_, _ = skRefreshSeeded(child, dest)
			_ = skMergeBundledLocal(filepath.Join(child, "SKILL.md"), filepath.Join(dest, "SKILL.md"))
		}
	}
	reg := &skRegistry{}
	for _, rec := range httpapi.CLIDiscoverSkills(home) {
		sk, err := skLoadDir(rec.Dir)
		if err != nil {
			// The runtime lists it, so the CLI does too, with what it read.
			sk = &skSkill{
				Name: rec.Name, Description: rec.Description, Version: rec.Version, Kind: "native",
				Status: rec.Status, Tags: append([]string{}, rec.Tags...), Path: rec.Dir, Dir: rec.Dir,
				Requires: []string{}, Tools: []string{},
			}
			if sk.Version == "" {
				sk.Version = "1.0.0"
			}
			if rec.AutoGenerated {
				sk.Meta.set("auto_generated", true)
			}
			if rec.Quarantine {
				sk.Meta.set("quarantine", true)
			}
			if md, err := skReadText(filepath.Join(rec.Dir, "SKILL.md")); err == nil {
				if m := skReFrontmatter.FindStringIndex(md); m != nil {
					sk.Instructions = skPyStrip(md[m[1]:])
				} else {
					sk.Instructions = md
				}
			}
			sk.Scripts, sk.References = skBundledResources(rec.Dir)
		}
		skApplyDefaultStatus(sk)
		reg.register(sk)
	}
	return reg
}

// skFrontmatterOf is registry._skill_frontmatter.
func skFrontmatterOf(path string) skMap {
	if !skIsFile(path) {
		return skMap{}
	}
	raw, err := skReadText(path)
	if err != nil {
		return skMap{}
	}
	m := skReFrontmatterLoose.FindStringSubmatch(raw)
	if m == nil {
		return skMap{}
	}
	v, err := skYAMLLoad(m[1])
	if err != nil {
		return skMap{}
	}
	fm, _ := v.(skMap)
	return fm
}

// skVersionTuple is registry._version_tuple.
func skVersionTuple(v any) []int {
	s := "0"
	if skTruthy(v) {
		s = skPyStr(v)
	}
	s = skPyStrip(s)
	var out []int
	cur, in := 0, false
	for _, r := range s + " " {
		if r >= '0' && r <= '9' {
			cur, in = cur*10+int(r-'0'), true
			continue
		}
		if in {
			out = append(out, cur)
		}
		cur, in = 0, false
	}
	if len(out) == 0 {
		return []int{0}
	}
	return out
}

func skTupleGreater(a, b []int) bool {
	for i := 0; i < len(a) && i < len(b); i++ {
		if a[i] != b[i] {
			return a[i] > b[i]
		}
	}
	return len(a) > len(b)
}

// skCopy2 is shutil.copy2: bytes, permission bits and modification time.
func skCopy2(src, dst string) error {
	st, err := os.Stat(src)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(src)
	if err != nil {
		return err
	}
	if err := os.WriteFile(dst, raw, st.Mode().Perm()); err != nil {
		return err
	}
	_ = os.Chmod(dst, st.Mode().Perm())
	return os.Chtimes(dst, st.ModTime(), st.ModTime())
}

// skRefreshSeeded is _refresh_seeded_skill_from_bundled: copy SKILL.md and
// scripts/ over a seeded copy when the curated version is newer, unless the
// owner put .user_locked in the folder.
func skRefreshSeeded(bundled, user string) (bool, error) {
	if skIsFile(filepath.Join(user, ".user_locked")) {
		return false, nil
	}
	bMD, uMD := filepath.Join(bundled, "SKILL.md"), filepath.Join(user, "SKILL.md")
	if !skIsFile(bMD) || !skIsFile(uMD) {
		return false, nil
	}
	bv, _ := skFrontmatterOf(bMD).get("version")
	uv, _ := skFrontmatterOf(uMD).get("version")
	if !skTupleGreater(skVersionTuple(bv), skVersionTuple(uv)) {
		return false, nil
	}
	if err := skCopy2(bMD, uMD); err != nil {
		return false, err
	}
	bScripts := filepath.Join(bundled, "scripts")
	if skIsDir(bScripts) {
		dest := filepath.Join(user, "scripts")
		if err := os.MkdirAll(dest, 0o755); err != nil {
			return false, err
		}
		var copyErr error
		_ = filepath.WalkDir(bScripts, func(p string, d fs.DirEntry, err error) error {
			if err != nil || d.IsDir() || !skIsFile(p) {
				return nil
			}
			rel, err := filepath.Rel(bScripts, p)
			if err != nil {
				return nil
			}
			target := filepath.Join(dest, rel)
			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				copyErr = err
				return fs.SkipAll
			}
			if err := skCopy2(p, target); err != nil {
				copyErr = err
				return fs.SkipAll
			}
			return nil
		})
		if copyErr != nil {
			return false, copyErr
		}
	}
	return true, nil
}

// skPyList is list(v) for the values a YAML document holds.
func skPyList(v any) []any {
	switch t := v.(type) {
	case []any:
		return append([]any(nil), t...)
	case string:
		out := []any{}
		for _, r := range t {
			out = append(out, string(r))
		}
		return out
	case skMap:
		out := []any{}
		for _, e := range t {
			out = append(out, e.K)
		}
		return out
	}
	return nil
}

// skMergeBundledLocal is _merge_bundled_local_frontmatter: give a seeded copy
// that has no `local:` block the curated one, and the curated tools it lacks.
func skMergeBundledLocal(bundledMD, userMD string) error {
	if !skIsFile(bundledMD) || !skIsFile(userMD) {
		return nil
	}
	bRaw, err := skReadText(bundledMD)
	if err != nil {
		return err
	}
	uRaw, err := skReadText(userMD)
	if err != nil {
		return err
	}
	bm := skReFrontmatterLoose.FindStringSubmatch(bRaw)
	um := skReFrontmatterLoose.FindStringSubmatchIndex(uRaw)
	if bm == nil || um == nil {
		return nil
	}
	bv, err := skYAMLLoad(bm[1])
	if err != nil {
		return nil
	}
	uv, err := skYAMLLoad(uRaw[um[2]:um[3]])
	if err != nil {
		return nil
	}
	if bv == nil {
		bv = skMap{}
	}
	if uv == nil {
		uv = skMap{}
	}
	bFM, ok1 := bv.(skMap)
	uFM, ok2 := uv.(skMap)
	if !ok1 || !ok2 {
		return nil
	}
	local, ok := bFM.get("local")
	if !ok || uFM.has("local") {
		return nil
	}
	uFM.set("local", local)
	bt, _ := bFM.get("tools")
	ut, _ := uFM.get("tools")
	var bTools, uTools []any
	if skTruthy(bt) {
		bTools = skPyList(bt)
	}
	if skTruthy(ut) {
		uTools = skPyList(ut)
	}
	for _, t := range bTools {
		found := false
		for _, x := range uTools {
			if skPyRepr(x) == skPyRepr(t) {
				found = true
				break
			}
		}
		if !found {
			uTools = append(uTools, t)
		}
	}
	if len(uTools) > 0 {
		uFM.set("tools", uTools)
	}
	body := uRaw[um[1]:]
	newFM := skPyStrip(skYAMLDump(uFM, skDumpOpts{allowUnicode: true}))
	return skWriteText(userMD, "---\n"+newFM+"\n---\n"+body)
}

// skSafeSegment reports whether name can be used as one path segment under an
// output folder: Python joined the skill name (and, for zip, its version)
// straight onto the path, so "../x" wrote outside it.
func skSafeSegment(name string) bool {
	if name == "" || name == "." || name == ".." || strings.ContainsAny(name, "/\\\x00") {
		return false
	}
	if runtime.GOOS == "windows" && strings.ContainsAny(name, `:<>"|?*`) {
		return false
	}
	return true
}
