package houserules

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync/atomic"

	"github.com/pelletier/go-toml/v2"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

// File IO of core/house_rules.py: the restricted TOML writer (lines 468-533),
// the loaders, save_*, the pending file, and the audit log.

var tmpSeq atomic.Uint64

// writeAtomic replaces path through a sibling temp file (atomic_json.py).
func writeAtomic(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := filepath.Join(filepath.Dir(path), fmt.Sprintf(".%s.%d.%d.tmp", filepath.Base(path), os.Getpid(), tmpSeq.Add(1)))
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	var err error
	for i := 0; i < 10; i++ { // Windows refuses a rename onto a file a reader holds open
		if err = os.Rename(tmp, path); err == nil {
			return nil
		}
	}
	_ = os.Remove(tmp)
	return err
}

func jsonBytes(v any, indent bool) ([]byte, error) {
	var b bytes.Buffer
	enc := json.NewEncoder(&b)
	enc.SetEscapeHTML(false)
	if indent {
		enc.SetIndent("", "  ")
	}
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	return bytes.TrimRight(b.Bytes(), "\n"), nil
}

func writeJSON(path string, v any) error {
	b, err := jsonBytes(v, true)
	if err != nil {
		return err
	}
	return writeAtomic(path, b)
}

// loadJSON is _load_json: a missing, unreadable, or non-object file is {}.
func loadJSON(path string) map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]any{}
	}
	var m map[string]any
	if json.Unmarshal(data, &m) != nil || m == nil {
		return map[string]any{}
	}
	return m
}

func loadTOML(path string) map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]any{}
	}
	var m map[string]any
	if toml.Unmarshal(data, &m) != nil || m == nil {
		return map[string]any{}
	}
	return m
}

func tomlEscape(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, r := range s {
		switch {
		case r == '\\':
			b.WriteString(`\\`)
		case r == '"':
			b.WriteString(`\"`)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r < 0x20 || r == 0x7f: // Python leaves these raw, which tomllib rejects
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteByte('"')
	return b.String()
}

func tomlScalar(v any) string {
	switch t := v.(type) {
	case nil:
		return `""`
	case bool:
		if t {
			return "true"
		}
		return "false"
	case []any:
		parts := make([]string, len(t))
		for i, x := range t {
			parts[i] = tomlScalar(x)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case []string:
		return tomlScalar(asAnyList(t))
	case float64:
		if t != math.Trunc(t) || math.Abs(t) >= 1e16 {
			if math.IsInf(t, 0) || math.IsNaN(t) {
				return strings.ToLower(strings.TrimPrefix(fmt.Sprint(t), "+"))
			}
			s := pyNum(t)
			if !strings.ContainsAny(s, ".e") {
				s += ".0"
			}
			return s
		}
		return strconv.FormatInt(int64(t), 10)
	}
	if _, ok := toFloat(v); ok {
		return pyNum(v)
	}
	return tomlEscape(pyStr(v))
}

// orderedKeys lists known keys first (in the given order), then the rest sorted.
func orderedKeys(m map[string]any, first []string) []string {
	var out []string
	seen := map[string]bool{}
	for _, k := range first {
		if _, ok := m[k]; ok {
			out = append(out, k)
			seen[k] = true
		}
	}
	var rest []string
	for k := range m {
		if !seen[k] {
			rest = append(rest, k)
		}
	}
	sort.Strings(rest)
	return append(out, rest...)
}

var rulesOrder = []string{"version", "status", "offered_at", "completed_at", "source", "skipped", "update_offer", "update_offer_id", "answers", "provenance"}

// dumpTOML is _dump_toml: root scalars first, then one table per dict value
// with nested [name.key] tables below it.
func dumpTOML(data map[string]any) string {
	lines := []string{
		"# Remedy house rules — answers and provenance.",
		"# API keys are never stored here. See docs/HOUSE_RULES.md.",
		"",
	}
	var tables []string
	for _, key := range orderedKeys(data, rulesOrder) {
		switch v := data[key].(type) {
		case map[string]any:
			tables = append(tables, key)
		case nil:
		default:
			lines = append(lines, key+" = "+tomlScalar(v))
		}
	}
	for _, name := range tables {
		table := data[name].(map[string]any)
		lines = append(lines, "", "["+name+"]")
		var nested []string
		for _, k := range orderedKeys(table, nil) {
			switch v := table[k].(type) {
			case map[string]any:
				nested = append(nested, k)
			case nil:
			default:
				lines = append(lines, k+" = "+tomlScalar(v))
			}
		}
		for _, k := range nested {
			lines = append(lines, "", "["+name+"."+k+"]")
			inner := table[k].(map[string]any)
			for _, ik := range orderedKeys(inner, nil) {
				switch iv := inner[ik].(type) {
				case nil, map[string]any:
				default:
					lines = append(lines, ik+" = "+tomlScalar(iv))
				}
			}
		}
	}
	lines = append(lines, "")
	return strings.Join(lines, "\n")
}

// scrub is _scrub: drop secret keys from any nested map.
func scrub(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			if !isSecretKey(k) {
				out[k] = scrub(x)
			}
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = scrub(x)
		}
		return out
	case []string:
		return asAnyList(t)
	}
	return v
}

func scrubMap(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	return scrub(m).(map[string]any)
}

func emptyRules() map[string]any {
	return map[string]any{
		"version": rulesVersion, "status": statusPending, "offered_at": "", "completed_at": "", "source": "",
		"answers": map[string]any{}, "provenance": map[string]any{}, "skipped": []any{},
		"update_offer": "", "update_offer_id": "",
	}
}

func emptySession() map[string]any {
	return map[string]any{
		"version": rulesVersion, "mode": modeConversation, "status": statusPending, "onboarding_pending": false,
		"mind_failure": nil, "draft": map[string]any{}, "skipped": []any{}, "offered_at": "", "completed_at": "",
		"source": "", "setup_session_id": "", "update_offer": "", "update_offer_id": "", "update_offer_at": "",
	}
}

func (r *run) path(name string) string { return filepath.Join(r.env.Home, name) }

// loadRules is load_rules (lines 595-611).
func (r *run) loadRules() map[string]any {
	out := emptyRules()
	data := loadTOML(r.path("house_rules.toml"))
	if len(data) == 0 {
		return out
	}
	for k := range out {
		if k != "answers" && k != "provenance" && k != "skipped" {
			if v, ok := data[k]; ok {
				out[k] = v
			}
		}
	}
	if a, ok := data["answers"].(map[string]any); ok {
		out["answers"] = scrubMap(a)
	}
	if p, ok := data["provenance"].(map[string]any); ok {
		out["provenance"] = scrubMap(p)
	}
	if l, ok := data["skipped"].([]any); ok {
		strs := make([]any, len(l))
		for i, x := range l {
			strs[i] = pyStr(x)
		}
		out["skipped"] = strs
	}
	return out
}

// loadSession is load_session (lines 614-628). Only the keys of
// empty_session survive a load, as in Python (secrets_set is one that does not).
func (r *run) loadSession() map[string]any {
	out := emptySession()
	data := loadJSON(r.path("setup_session.json"))
	if len(data) == 0 {
		return out
	}
	for k := range out {
		if v, ok := data[k]; ok {
			out[k] = v
		}
	}
	if d, ok := out["draft"].(map[string]any); ok {
		out["draft"] = scrubMap(d)
	} else {
		out["draft"] = map[string]any{}
	}
	if m := out["mode"]; m != modeConversation && m != modeTraditional {
		out["mode"] = modeConversation
	}
	return out
}

func (r *run) saveRules(data map[string]any) {
	payload := emptyRules()
	for k, v := range data {
		payload[k] = v
	}
	payload["answers"] = scrubMap(asMap(payload["answers"]))
	payload["provenance"] = scrubMap(asMap(payload["provenance"]))
	payload["version"] = rulesVersion
	check(os.MkdirAll(r.env.Home, 0o755))
	check(writeAtomic(r.path("house_rules.toml"), []byte(dumpTOML(payload))))
}

func (r *run) saveSession(data map[string]any) {
	payload := emptySession()
	for k, v := range data {
		payload[k] = v
	}
	payload["draft"] = scrubMap(asMap(payload["draft"]))
	payload["version"] = rulesVersion
	check(writeJSON(r.path("setup_session.json"), scrub(payload)))
}

// asMap is `x or {}` for a value that should be a dict.
func asMap(v any) map[string]any {
	m, _ := v.(map[string]any)
	return m
}

func (r *run) loadPending() map[string]any {
	data := loadJSON(r.path("house_rules_pending.json"))
	if len(data) == 0 {
		return nil
	}
	return scrubMap(data)
}

func (r *run) savePending(data map[string]any) {
	if data == nil {
		_ = os.Remove(r.path("house_rules_pending.json"))
		return
	}
	check(writeJSON(r.path("house_rules_pending.json"), scrub(data)))
}

// appendAudit is _append_audit (lines 658-686).
func (r *run) appendAudit(entry map[string]any) {
	if !r.knobBool("house_rules_audit", true) {
		return
	}
	clean := scrubMap(entry)
	if _, ok := clean["ts"]; !ok {
		clean["ts"] = r.now()
	}
	line, err := jsonBytes(clean, false)
	check(err)
	path := r.path("house_rules_audit.jsonl")
	check(os.MkdirAll(r.env.Home, 0o755))
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	check(err)
	_, err = f.Write(append(line, '\n'))
	check(err)
	check(f.Close())
	st, err := os.Stat(path)
	if err != nil || st.Size() <= auditMax {
		return
	}
	text, err := os.ReadFile(path)
	check(err)
	keep := sf.Tail(string(text), auditKeep) // Python keeps the last chars, not bytes
	if i := strings.Index(keep, "\n"); i >= 0 {
		keep = keep[i+1:]
	}
	if !strings.HasSuffix(keep, "\n") {
		keep += "\n"
	}
	check(writeAtomic(path, []byte(keep)))
}
