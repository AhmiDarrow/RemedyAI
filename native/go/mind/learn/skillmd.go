package learn

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	yaml "go.yaml.in/yaml/v4"
)

// kv / omap are an insertion-ordered YAML mapping (PyYAML sort_keys=False).
type kv struct {
	k string
	v any
}
type omap []kv

func (m omap) get(k string) (any, bool) {
	for _, e := range m {
		if e.k == k {
			return e.v, true
		}
	}
	return nil, false
}

// set replaces in place or appends, as a Python dict does.
func (m *omap) set(k string, v any) {
	for i := range *m {
		if (*m)[i].k == k {
			(*m)[i].v = v
			return
		}
	}
	*m = append(*m, kv{k, v})
}

// skill is the manifest + body that learning_loop._write_skill_md writes.
type skill struct {
	name, description, version, status string
	tags, tools                        []string
	meta                               omap
	instructions                       string
}

// -- writing (learning_loop.py 747-773) ---------------------------------------

var (
	rePlain    = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_./-]*(?: [A-Za-z0-9_./(),-]+)*$`)
	reReserved = regexp.MustCompile(`(?i)^(?:true|false|yes|no|on|off|null|y|n)$`)
)

func yamlString(s string) string {
	if s == "" {
		return "''"
	}
	for _, r := range s {
		if r < 0x20 || r == 0x7f || r == 0x85 || r == 0x2028 || r == 0x2029 || r == 0xfeff {
			return strconv.Quote(s) // YAML double-quoted escapes (\n \t \xNN \uNNNN) match Go's
		}
	}
	if rePlain.MatchString(s) && !reReserved.MatchString(s) {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", "''") + "'"
}

// pyFloat renders a float the way PyYAML does (repr, lower-cased, ".0e" fix).
func pyFloat(f float64) string {
	a := f
	if a < 0 {
		a = -a
	}
	var s string
	if a != 0 && (a < 1e-4 || a >= 1e16) {
		s = strconv.FormatFloat(f, 'e', -1, 64)
		if !strings.Contains(s, ".") {
			s = strings.Replace(s, "e", ".0e", 1)
		}
		return s
	}
	s = strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.Contains(s, ".") {
		s += ".0"
	}
	return s
}

func yamlScalar(v any) string {
	switch t := v.(type) {
	case nil:
		return "null"
	case string:
		return yamlString(t)
	case bool:
		return strconv.FormatBool(t)
	case int:
		return strconv.Itoa(t)
	case int64:
		return strconv.FormatInt(t, 10)
	case float64:
		return pyFloat(t)
	}
	return yamlString(fmt.Sprint(v))
}

func toAnyList(v any) []any {
	switch t := v.(type) {
	case []string:
		out := make([]any, len(t))
		for i, s := range t {
			out[i] = s
		}
		return out
	case []any:
		return t
	}
	return nil
}

func writeYAML(b *strings.Builder, indent int, key string, v any) {
	pad := strings.Repeat(" ", indent)
	switch t := v.(type) {
	case omap:
		if len(t) == 0 {
			b.WriteString(pad + key + ": {}\n")
			return
		}
		b.WriteString(pad + key + ":\n")
		for _, e := range t {
			writeYAML(b, indent+2, e.k, e.v)
		}
	case []string, []any:
		l := toAnyList(t)
		if len(l) == 0 {
			b.WriteString(pad + key + ": []\n")
			return
		}
		b.WriteString(pad + key + ":\n") // PyYAML does not indent a list under its key
		for _, it := range l {
			switch x := it.(type) {
			case omap: // only reachable through foreign metadata kept on merge
				if len(x) == 0 {
					b.WriteString(pad + "- {}\n")
					continue
				}
				var sub strings.Builder
				for _, e := range x {
					writeYAML(&sub, indent+2, e.k, e.v)
				}
				b.WriteString(pad + "- " + strings.TrimPrefix(sub.String(), pad+"  "))
			case []any, []string:
				var parts []string
				for _, y := range toAnyList(x) {
					parts = append(parts, yamlScalar(y))
				}
				b.WriteString(pad + "- [" + strings.Join(parts, ", ") + "]\n")
			default:
				b.WriteString(pad + "- " + yamlScalar(it) + "\n")
			}
		}
	default:
		b.WriteString(pad + key + ": " + yamlScalar(v) + "\n")
	}
}

func (s *skill) render() string {
	var b strings.Builder
	b.WriteString("---\n")
	fm := omap{
		{"name", s.name}, {"description", s.description}, {"version", s.version}, {"kind", "native"},
		{"status", s.status}, {"tags", s.tags}, {"tools", s.tools}, {"metadata", s.meta},
	}
	for _, e := range fm {
		writeYAML(&b, 0, e.k, e.v)
	}
	b.WriteString("---\n\n" + strings.TrimRight(s.instructions, " \t\r\n\f\v") + "\n")
	return b.String()
}

var reSafeName = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$`)

// isSafeSkillName is skills/library/security.is_safe_skill_name.
func isSafeSkillName(name string) bool {
	n := strings.TrimSpace(name)
	return n != "" && !strings.ContainsAny(n, `/\`) && !strings.Contains(n, "..") && reSafeName.MatchString(n)
}

// safeDirName is LearningLoop._safe_skill_dir_name (py 735-745).
func safeDirName(name string) string {
	raw := strings.TrimSpace(name)
	if raw == "" {
		raw = "unnamed-skill"
	}
	if isSafeSkillName(raw) {
		return raw
	}
	slug := slugOf(raw)
	if slug == "" {
		slug = "unnamed-skill"
	}
	if isSafeSkillName(slug) {
		return slug
	}
	return "unnamed-skill"
}

// writeSkill is _write_skill_md; it returns the absolute path of SKILL.md.
func writeSkill(skillsDir string, s *skill) (string, error) {
	s.name = safeDirName(s.name)
	dir := filepath.Join(skillsDir, s.name)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(dir, "SKILL.md")
	return path, os.WriteFile(path, []byte(s.render()), 0o600)
}

// -- reading (skills/loader.py 25-120) ----------------------------------------

var reFrontmatter = regexp.MustCompile(`(?s)^---\s*\n(.*?)\n---\s*\n`)

func nodeToAny(n *yaml.Node) any {
	switch n.Kind {
	case yaml.DocumentNode:
		if len(n.Content) == 0 {
			return nil
		}
		return nodeToAny(n.Content[0])
	case yaml.AliasNode:
		return nodeToAny(n.Alias)
	case yaml.MappingNode:
		m := omap{}
		for i := 0; i+1 < len(n.Content); i += 2 {
			m = append(m, kv{n.Content[i].Value, nodeToAny(n.Content[i+1])})
		}
		return m
	case yaml.SequenceNode:
		l := []any{}
		for _, c := range n.Content {
			l = append(l, nodeToAny(c))
		}
		return l
	}
	switch n.ShortTag() {
	case "!!null":
		return nil
	case "!!bool":
		var b bool
		if n.Decode(&b) == nil {
			return b
		}
	case "!!int":
		var i int
		if n.Decode(&i) == nil {
			return i
		}
	case "!!float":
		var f float64
		if n.Decode(&f) == nil {
			return f
		}
	}
	return n.Value
}

func strList(v any) []string {
	var out []string
	for _, x := range toAnyList(v) {
		if x != nil {
			out = append(out, scalarText(x))
		}
	}
	return out
}

func scalarText(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	if f, ok := v.(float64); ok {
		return pyFloat(f)
	}
	return fmt.Sprint(v)
}

var statuses = map[string]bool{"discovered": true, "validated": true, "active": true, "disabled": true, "deprecated": true, "archived": true}

// readSkill is load_skill_from_dir for the fields a rewrite keeps.
func readSkill(dir string) (*skill, error) {
	raw, err := os.ReadFile(filepath.Join(dir, "SKILL.md"))
	if err != nil {
		return nil, err
	}
	text := string(raw)
	m := reFrontmatter.FindStringSubmatchIndex(text)
	if m == nil {
		return nil, fmt.Errorf("no frontmatter in %s", dir)
	}
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(text[m[2]:m[3]]), &doc); err != nil {
		return nil, err
	}
	fm, ok := nodeToAny(&doc).(omap)
	if !ok {
		return nil, fmt.Errorf("frontmatter of %s is not a mapping", dir)
	}
	get := func(k string) any { v, _ := fm.get(k); return v }
	if get("name") == nil || get("description") == nil {
		return nil, fmt.Errorf("%s misses name or description", dir)
	}
	s := &skill{
		name:         scalarText(get("name")),
		description:  scalarText(get("description")),
		version:      "1.0.0",
		status:       "discovered",
		tags:         strList(get("tags")),
		tools:        strList(get("tools")),
		instructions: strings.TrimSpace(text[m[1]:]),
	}
	if v, ok := fm.get("version"); ok {
		s.version = scalarText(v)
	}
	if st := strings.ToLower(strings.TrimSpace(scalarText(get("status")))); statuses[st] {
		s.status = st
	}
	if md, ok := get("metadata").(omap); ok {
		s.meta = md
	}
	// Top-level keys the loader promotes into metadata (loader.py 88-102).
	for _, k := range []string{"auto_generated", "effort_weight", "effort_band", "effort_reasons", "lifecycle",
		"source_trace_id", "source_trace_ids", "parent_skill", "quarantine", "trust"} {
		if v, ok := fm.get(k); ok {
			if _, has := s.meta.get(k); !has {
				s.meta.set(k, v)
			}
		}
	}
	return s, nil
}
