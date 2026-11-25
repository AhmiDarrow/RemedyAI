package prompt

import (
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

// Small helpers that keep Python's semantics: str(x or ""), bool(x),
// len() and slices on code points.

// pyStr is str(m.get(k) or "").
func pyStr(m map[string]any, k string) string {
	v, ok := m[k]
	if !ok || v == nil {
		return ""
	}
	switch t := v.(type) {
	case string:
		return t
	case bool:
		if !t {
			return ""
		}
		return "True"
	case float64:
		if t == 0 {
			return ""
		}
		if t == float64(int64(t)) {
			return fmt.Sprintf("%d", int64(t))
		}
		return fmt.Sprintf("%v", t)
	case int:
		if t == 0 {
			return ""
		}
		return fmt.Sprintf("%d", t)
	case int64:
		if t == 0 {
			return ""
		}
		return fmt.Sprintf("%d", t)
	}
	return fmt.Sprint(v)
}

// truthy is Python's bool(v) for decoded JSON / TOML values.
func truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case float64:
		return t != 0
	case int:
		return t != 0
	case int64:
		return t != 0
	case []any:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	return true
}

func boolIn(m map[string]any, k string) bool { return truthy(m[k]) }

func cpLen(s string) int { return utf8.RuneCountInString(s) }

// cut is s[:n] on code points.
func cut(s string, n int) string { return sf.Cut(s, n) }

// pyStrip is str.strip() with no argument.
func pyStrip(s string) string { return strings.TrimFunc(s, unicode.IsSpace) }

// pyRstrip is str.rstrip() with no argument.
func pyRstrip(s string) string { return strings.TrimRightFunc(s, unicode.IsSpace) }

// isPrintable is str.isprintable() for one character: Python excludes the
// categories C* and Z* except the ASCII space, which is unicode.IsPrint.
func isPrintable(r rune) bool { return unicode.IsPrint(r) }

// cfgStr is str(cfg.get(k) or "").
func cfgStr(cfg map[string]any, k string) string { return pyStr(cfg, k) }

func cfgTable(cfg map[string]any, k string) map[string]any {
	if t, ok := cfg[k].(map[string]any); ok {
		return t
	}
	return nil
}
