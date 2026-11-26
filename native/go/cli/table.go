package cli

import (
	"fmt"
	"io"
	"strings"
	"unicode/utf8"
)

// table prints aligned columns with a title, plain text (no colours), so
// output pipes cleanly into grep and files.
type table struct {
	title string
	head  []string
	rows  [][]string
}

func newTable(title string, head ...string) *table { return &table{title: title, head: head} }

func (t *table) add(cells ...string) { t.rows = append(t.rows, cells) }

func (t *table) print(w io.Writer) {
	widths := make([]int, len(t.head))
	for i, h := range t.head {
		widths[i] = utf8.RuneCountInString(h)
	}
	for _, r := range t.rows {
		for i := range widths {
			if i < len(r) {
				if n := utf8.RuneCountInString(r[i]); n > widths[i] {
					widths[i] = n
				}
			}
		}
	}
	line := func(cells []string) {
		parts := make([]string, len(widths))
		for i := range widths {
			c := ""
			if i < len(cells) {
				c = cells[i]
			}
			if i == len(widths)-1 {
				parts[i] = c
			} else {
				parts[i] = c + strings.Repeat(" ", widths[i]-utf8.RuneCountInString(c))
			}
		}
		fmt.Fprintln(w, strings.TrimRight(strings.Join(parts, "  "), " "))
	}
	if t.title != "" {
		fmt.Fprintln(w, t.title)
	}
	line(t.head)
	seps := make([]string, len(widths))
	for i, n := range widths {
		seps[i] = strings.Repeat("-", n)
	}
	line(seps)
	for _, r := range t.rows {
		line(r)
	}
}

// clip cuts s to n runes.
func clip(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	r := []rune(s)
	return string(r[:n])
}

// asString renders a JSON-decoded value the way Python's str() would for the
// common cases (nil prints empty, numbers without a trailing .0).
func asString(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case bool:
		if x {
			return "True"
		}
		return "False"
	case float64:
		if x == float64(int64(x)) {
			return fmt.Sprintf("%d", int64(x))
		}
		return fmt.Sprintf("%g", x)
	default:
		return fmt.Sprintf("%v", x)
	}
}
