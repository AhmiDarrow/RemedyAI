package httpapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// The `remedy memory|user|session|handoff` commands write memory.db rows that
// Python's MemoryStore also reads and rewrites. Python serialises those JSON
// columns with json.dumps (", " / ": " separators, ASCII escapes, insertion
// order), so the CLI does the same and a row reads back byte-for-byte alike
// whichever side wrote it.

// cliStoreObj is a JSON object that keeps its key order, like a Python dict.
type cliStoreObj struct {
	keys []string
	vals map[string]any
}

func newCLIStoreObj() *cliStoreObj { return &cliStoreObj{vals: map[string]any{}} }

func (o *cliStoreObj) get(k string) (any, bool) {
	v, ok := o.vals[k]
	return v, ok
}

func (o *cliStoreObj) set(k string, v any) {
	if _, ok := o.vals[k]; !ok {
		o.keys = append(o.keys, k)
	}
	o.vals[k] = v
}

func (o *cliStoreObj) copy() *cliStoreObj {
	out := newCLIStoreObj()
	for _, k := range o.keys {
		out.set(k, o.vals[k])
	}
	return out
}

// cliStorePyLoads is json.loads: objects keep their order, numbers keep
// int-vs-float (json.Number).
func cliStorePyLoads(s string) (any, error) {
	dec := json.NewDecoder(strings.NewReader(s))
	dec.UseNumber()
	v, err := cliStoreDecodeValue(dec)
	if err != nil {
		return nil, err
	}
	if _, err := dec.Token(); err != io.EOF {
		return nil, fmt.Errorf("extra data after JSON value")
	}
	return v, nil
}

func cliStoreDecodeValue(dec *json.Decoder) (any, error) {
	tok, err := dec.Token()
	if err != nil {
		return nil, err
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			obj := newCLIStoreObj()
			for dec.More() {
				kt, err := dec.Token()
				if err != nil {
					return nil, err
				}
				k, _ := kt.(string)
				v, err := cliStoreDecodeValue(dec)
				if err != nil {
					return nil, err
				}
				obj.set(k, v)
			}
			if _, err := dec.Token(); err != nil {
				return nil, err
			}
			return obj, nil
		case '[':
			arr := []any{}
			for dec.More() {
				v, err := cliStoreDecodeValue(dec)
				if err != nil {
					return nil, err
				}
				arr = append(arr, v)
			}
			if _, err := dec.Token(); err != nil {
				return nil, err
			}
			return arr, nil
		}
		return nil, fmt.Errorf("unexpected delimiter %v", t)
	default:
		return t, nil
	}
}

// cliStorePyDumps is json.dumps(v) (indent 0: one line with ", " and ": ";
// indent n: pretty with n spaces). ensureASCII mirrors Python's default.
func cliStorePyDumps(v any, indent int, ensureASCII bool) string {
	var b bytes.Buffer
	cliStoreEncode(&b, v, indent, 0, ensureASCII)
	return b.String()
}

func cliStoreEncode(b *bytes.Buffer, v any, indent, depth int, ascii bool) {
	nl := func(d int) {
		b.WriteByte('\n')
		b.WriteString(strings.Repeat(" ", indent*d))
	}
	itemSep := func() {
		if indent > 0 {
			b.WriteByte(',')
			nl(depth + 1)
		} else {
			b.WriteString(", ")
		}
	}
	switch x := v.(type) {
	case nil:
		b.WriteString("null")
	case bool:
		if x {
			b.WriteString("true")
		} else {
			b.WriteString("false")
		}
	case string:
		cliStoreEncodeString(b, x, ascii)
	case json.Number:
		s := x.String()
		if strings.ContainsAny(s, ".eE") {
			f, err := x.Float64()
			if err == nil {
				b.WriteString(cliStorePyFloat(f))
				return
			}
		}
		b.WriteString(s)
	case float64:
		b.WriteString(cliStorePyFloat(x))
	case int:
		b.WriteString(strconv.Itoa(x))
	case int64:
		b.WriteString(strconv.FormatInt(x, 10))
	case []string:
		arr := make([]any, len(x))
		for i, s := range x {
			arr[i] = s
		}
		cliStoreEncode(b, arr, indent, depth, ascii)
	case []any:
		if len(x) == 0 {
			b.WriteString("[]")
			return
		}
		b.WriteByte('[')
		if indent > 0 {
			nl(depth + 1)
		}
		for i, e := range x {
			if i > 0 {
				itemSep()
			}
			cliStoreEncode(b, e, indent, depth+1, ascii)
		}
		if indent > 0 {
			nl(depth)
		}
		b.WriteByte(']')
	case *cliStoreObj:
		if x == nil || len(x.keys) == 0 {
			b.WriteString("{}")
			return
		}
		b.WriteByte('{')
		if indent > 0 {
			nl(depth + 1)
		}
		for i, k := range x.keys {
			if i > 0 {
				itemSep()
			}
			cliStoreEncodeString(b, k, ascii)
			b.WriteString(": ")
			cliStoreEncode(b, x.vals[k], indent, depth+1, ascii)
		}
		if indent > 0 {
			nl(depth)
		}
		b.WriteByte('}')
	case map[string]any:
		keys := make([]string, 0, len(x))
		for k := range x {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		o := newCLIStoreObj()
		for _, k := range keys {
			o.set(k, x[k])
		}
		cliStoreEncode(b, o, indent, depth, ascii)
	default:
		raw, err := json.Marshal(x)
		if err != nil {
			b.WriteString("null")
			return
		}
		b.Write(raw)
	}
}

func cliStoreEncodeString(b *bytes.Buffer, s string, ascii bool) {
	b.WriteByte('"')
	for _, r := range s {
		switch r {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			switch {
			case r < 0x20:
				fmt.Fprintf(b, `\u%04x`, r)
			case ascii && r > 0x7e:
				if r == utf8.RuneError {
					b.WriteString(`�`)
				} else if r > 0xffff {
					r -= 0x10000
					fmt.Fprintf(b, `\u%04x\u%04x`, 0xd800+(r>>10), 0xdc00+(r&0x3ff))
				} else {
					fmt.Fprintf(b, `\u%04x`, r)
				}
			default:
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
}

// cliStorePyFloat is Python's repr(float), which json.dumps uses.
func cliStorePyFloat(f float64) string {
	switch {
	case math.IsNaN(f):
		return "NaN"
	case math.IsInf(f, 1):
		return "Infinity"
	case math.IsInf(f, -1):
		return "-Infinity"
	case f == 0:
		if math.Signbit(f) {
			return "-0.0"
		}
		return "0.0"
	}
	e := strconv.FormatFloat(f, 'e', -1, 64)
	mant, expS, _ := strings.Cut(e, "e")
	exp, _ := strconv.Atoi(expS)
	if exp >= -4 && exp < 16 {
		s := strconv.FormatFloat(f, 'f', -1, 64)
		if !strings.Contains(s, ".") {
			s += ".0"
		}
		return s
	}
	return mant + "e" + fmt.Sprintf("%+03d", exp)
}

// cliStorePyStr is Python's str(value) for a JSON-decoded value.
func cliStorePyStr(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return cliStorePyRepr(v)
}

// cliStorePyRepr is Python's repr(value) for a JSON-decoded value.
func cliStorePyRepr(v any) string {
	switch x := v.(type) {
	case nil:
		return "None"
	case bool:
		if x {
			return "True"
		}
		return "False"
	case string:
		if strings.Contains(x, "'") && !strings.Contains(x, `"`) {
			return `"` + strings.ReplaceAll(x, `\`, `\\`) + `"`
		}
		r := strings.NewReplacer(`\`, `\\`, `'`, `\'`, "\n", `\n`, "\r", `\r`, "\t", `\t`)
		return "'" + r.Replace(x) + "'"
	case json.Number, float64, int, int64:
		return cliStorePyDumps(x, 0, false)
	case []string:
		parts := make([]string, len(x))
		for i, s := range x {
			parts[i] = cliStorePyRepr(s)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case []any:
		parts := make([]string, len(x))
		for i, e := range x {
			parts[i] = cliStorePyRepr(e)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case *cliStoreObj:
		parts := make([]string, 0, len(x.keys))
		for _, k := range x.keys {
			parts = append(parts, cliStorePyRepr(k)+": "+cliStorePyRepr(x.vals[k]))
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return fmt.Sprint(v)
}

// cliStoreNow is datetime.now(UTC): microsecond precision.
func cliStoreNow() time.Time { return time.Now().UTC().Truncate(time.Microsecond) }

// cliStoreTime is a parsed Python datetime: naive ones carry no offset.
type cliStoreTime struct {
	T     time.Time
	Naive bool
}

// cliStoreParseISO is datetime.fromisoformat for the shapes memory.db holds.
func cliStoreParseISO(s string) (cliStoreTime, error) {
	t := strings.TrimSpace(s)
	if len(t) > 10 && t[10] == ' ' {
		t = t[:10] + "T" + t[11:]
	}
	for _, l := range []string{
		"2006-01-02T15:04:05.999999999Z07:00", "2006-01-02T15:04:05Z07:00",
		"2006-01-02T15:04:05.999999999Z0700", "2006-01-02T15:04:05Z0700",
		"2006-01-02T15:04Z07:00",
	} {
		if p, err := time.Parse(l, t); err == nil {
			return cliStoreTime{T: p.Truncate(time.Microsecond)}, nil
		}
	}
	for _, l := range []string{"2006-01-02T15:04:05.999999999", "2006-01-02T15:04:05", "2006-01-02T15:04", "2006-01-02"} {
		if p, err := time.Parse(l, t); err == nil {
			return cliStoreTime{T: p.Truncate(time.Microsecond), Naive: true}, nil
		}
	}
	return cliStoreTime{}, fmt.Errorf("Invalid isoformat string: %q", s)
}

// iso is datetime.isoformat(); pydantic=true is pydantic's JSON form ("Z").
func (ct cliStoreTime) iso(pydantic bool) string {
	s := ct.T.Format("2006-01-02T15:04:05")
	if us := ct.T.Nanosecond() / 1000; us != 0 {
		s += fmt.Sprintf(".%06d", us)
	}
	if ct.Naive {
		return s
	}
	_, off := ct.T.Zone()
	if off == 0 {
		if pydantic {
			return s + "Z"
		}
		return s + "+00:00"
	}
	sign := '+'
	if off < 0 {
		sign = '-'
		off = -off
	}
	return s + fmt.Sprintf("%c%02d:%02d", sign, off/3600, (off%3600)/60)
}

func cliStoreISO(t time.Time) string { return cliStoreTime{T: t}.iso(false) }
