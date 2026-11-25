package houserules

import (
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"
	"unicode"
)

// Python value semantics for decoded JSON / TOML values (nil, bool, string,
// numbers, []any, map[string]any): str(), repr(), bool() and ==.

func truthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case []any:
		return len(t) > 0
	case []string:
		return len(t) > 0
	case map[string]any:
		return len(t) > 0
	}
	if f, ok := toFloat(v); ok {
		return f != 0
	}
	return true
}

func toFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case float32:
		return float64(t), true
	case int:
		return float64(t), true
	case int64:
		return float64(t), true
	case int32:
		return float64(t), true
	case bool:
		if t {
			return 1, true
		}
		return 0, true
	}
	return 0, false
}

// pyEq is Python's ==, where True == 1 and 1 == 1.0.
func pyEq(a, b any) bool {
	if fa, ok := toFloat(a); ok {
		fb, ok2 := toFloat(b)
		return ok2 && fa == fb
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	case []any:
		y, ok := asList(b)
		if !ok || len(x) != len(y) {
			return false
		}
		for i := range x {
			if !pyEq(x[i], y[i]) {
				return false
			}
		}
		return true
	case []string:
		return pyEq(asAnyList(x), b)
	case map[string]any:
		y, ok := b.(map[string]any)
		if !ok || len(x) != len(y) {
			return false
		}
		for k, v := range x {
			w, ok := y[k]
			if !ok || !pyEq(v, w) {
				return false
			}
		}
		return true
	}
	return false
}

func asList(v any) ([]any, bool) {
	switch t := v.(type) {
	case []any:
		return t, true
	case []string:
		return asAnyList(t), true
	}
	return nil, false
}

func asAnyList(s []string) []any {
	out := make([]any, len(s))
	for i, x := range s {
		out[i] = x
	}
	return out
}

// pyStr is str(v).
func pyStr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case string:
		return t
	case bool:
		if t {
			return "True"
		}
		return "False"
	}
	if _, ok := toFloat(v); ok {
		return pyNum(v)
	}
	return pyRepr(v)
}

// pyOr is str(v or "").
func pyOr(v any) string {
	if !truthy(v) {
		return ""
	}
	return pyStr(v)
}

func pyNum(v any) string {
	switch t := v.(type) {
	case int:
		return strconv.Itoa(t)
	case int32:
		return strconv.Itoa(int(t))
	case int64:
		return strconv.FormatInt(t, 10)
	case float64:
		// shortcut: JSON cannot tell 1 from 1.0, so a whole number prints as an int.
		if t == math.Trunc(t) && math.Abs(t) < 1e16 {
			return strconv.FormatInt(int64(t), 10)
		}
		if math.IsInf(t, 0) || math.IsNaN(t) {
			return strings.ToLower(fmt.Sprint(t))
		}
		if a := math.Abs(t); a >= 1e16 || a < 1e-4 {
			return strconv.FormatFloat(t, 'e', -1, 64)
		}
		return strconv.FormatFloat(t, 'f', -1, 64)
	}
	return fmt.Sprint(v)
}

// pyRepr is repr(v).
func pyRepr(v any) string {
	switch t := v.(type) {
	case nil:
		return "None"
	case string:
		return reprString(t)
	case bool:
		return pyStr(t)
	case []string:
		return pyRepr(asAnyList(t))
	case []any:
		parts := make([]string, len(t))
		for i, x := range t {
			parts[i] = pyRepr(x)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case map[string]any:
		keys := sortedKeys(t)
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = reprString(k) + ": " + pyRepr(t[k])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return pyNum(v)
}

func reprString(s string) string {
	q := byte('\'')
	if strings.ContainsRune(s, '\'') && !strings.ContainsRune(s, '"') {
		q = '"'
	}
	var b strings.Builder
	b.WriteByte(q)
	for _, r := range s {
		switch {
		case r == rune(q) || r == '\\':
			b.WriteByte('\\')
			b.WriteRune(r)
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case unicode.IsPrint(r):
			b.WriteRune(r)
		case r < 0x100:
			fmt.Fprintf(&b, `\x%02x`, r)
		case r < 0x10000:
			fmt.Fprintf(&b, `\u%04x`, r)
		default:
			fmt.Fprintf(&b, `\U%08x`, r)
		}
	}
	b.WriteByte(q)
	return b.String()
}

// pyStrip is str.strip() (Python also counts U+001C..U+001F as space).
func pyStrip(s string) string {
	return strings.TrimFunc(s, func(r rune) bool {
		return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f)
	})
}

func sortedKeys(m map[string]any) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func hashable(v any) bool {
	switch v.(type) {
	case []any, []string, map[string]any:
		return false
	}
	return true
}

// deepCopy copies maps and lists so a stored value is never aliased.
func deepCopy(v any) any {
	switch t := v.(type) {
	case map[string]any:
		out := make(map[string]any, len(t))
		for k, x := range t {
			out[k] = deepCopy(x)
		}
		return out
	case []any:
		out := make([]any, len(t))
		for i, x := range t {
			out[i] = deepCopy(x)
		}
		return out
	case []string:
		return asAnyList(t)
	}
	return v
}

func copyMap(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	return deepCopy(m).(map[string]any)
}
