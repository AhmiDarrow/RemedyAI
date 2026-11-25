package statefile

import (
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

// Int reads an organism knob (the resolved [advanced] table), or def.
func Int(knobs map[string]any, k string, def int) int {
	switch v := knobs[k].(type) {
	case int:
		return v
	case int64:
		return int(v)
	case float64:
		return int(v)
	}
	return def
}

// Float reads a numeric organism knob, or def.
func Float(knobs map[string]any, k string, def float64) float64 {
	switch v := knobs[k].(type) {
	case int:
		return float64(v)
	case int64:
		return float64(v)
	case float64:
		return v
	}
	return def
}

// Bool reads a boolean organism knob, or def.
func Bool(knobs map[string]any, k string, def bool) bool {
	if v, ok := knobs[k].(bool); ok {
		return v
	}
	return def
}

// Cut is Python's s[:n] on code points.
func Cut(s string, n int) string {
	if n <= 0 {
		return ""
	}
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	i := 0
	for j := range s {
		if i == n {
			return s[:j]
		}
		i++
	}
	return s
}

// Tail is Python's s[-n:] on code points.
func Tail(s string, n int) string {
	c := utf8.RuneCountInString(s)
	if n <= 0 {
		return ""
	}
	if c <= n {
		return s
	}
	skip := c - n
	i := 0
	for j := range s {
		if i == skip {
			return s[j:]
		}
		i++
	}
	return ""
}

// UpperFirst is Python's s[:1].upper() + s[1:].
func UpperFirst(s string) string {
	r, n := utf8.DecodeRuneInString(s)
	if n == 0 {
		return s
	}
	return string(unicode.ToUpper(r)) + s[n:]
}

// Fold is a case-insensitive key, close to Python's casefold.
func Fold(s string) string { return strings.ToLower(s) }

// Unix is a time as Python's time.time() float.
func Unix(t time.Time) float64 {
	if t.IsZero() {
		return 0
	}
	return float64(t.UnixNano()) / 1e9
}

// Contains reports whether list holds s.
func Contains(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}
