package prompt

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

// The alive inject (core/metabolism/alive.py build_alive_inject) and the
// "state" kind of the inner-language codec (core/inner_language/codec.py),
// including the audit line it always writes (inner_language/audit.py).

func knobStr(adv map[string]any, key, def string, allowed ...string) string {
	v, ok := adv[key]
	if !ok || v == nil {
		return def
	}
	s := strings.ToLower(pyStrip(pyStr(map[string]any{"v": v}, "v")))
	for _, a := range allowed {
		if s == a {
			return s
		}
	}
	return def
}

// aliveMissing reports alive pieces this port cannot build.
func aliveMissing(env Env) []string {
	adv := cfgTable(env.Config, "advanced")
	st, err := drives.Load(env.soulEnv())
	if err != nil {
		// Python falls back to the default state on an unreadable file.
		return []string{"alive:unreadable_state"}
	}
	if sf.Bool(adv, "proactive_reach_out", false) && st.ReachOut.Pending && st.ReachOut.Gloss != "" {
		// Voicing the offer rewrites alive.json through Python's writer.
		return []string{"alive:reach_out"}
	}
	return nil
}

// aliveInject is build_alive_inject. Call only when aliveMissing is empty.
func aliveInject(env Env) (string, error) {
	adv := cfgTable(env.Config, "advanced")
	capChars := sf.Int(adv, "autobiography_chars", 400)
	if capChars <= 0 {
		return "", nil
	}
	st, err := drives.Load(env.soulEnv())
	if err != nil {
		return "", err
	}
	verbosity := knobStr(adv, "inner_language_verbosity", "dense", "off", "natural", "dense")
	if sf.Bool(adv, "inner_language", true) && verbosity == "dense" {
		bio := cut(pyStrip(st.Autobiography), capChars)
		beliefs := st.Beliefs
		if len(beliefs) > 4 {
			beliefs = beliefs[len(beliefs)-4:]
		}
		dense := encodeState(st.Drives, st.Affect.Label, bio, beliefs)
		decoded := expandTerms(stateNatural(st.Drives, st.Affect.Label, bio, beliefs), loadLexiconTerms(env.Home))
		if err := recordAudit(env, "state", dense, decoded); err != nil {
			return "", err
		}
		return cut(dense, capChars+200), nil
	}
	var lines []string
	auto := pyStrip(st.Autobiography)
	if auto != "" {
		lines = append(lines, cut(auto, capChars))
	}
	if sf.Bool(adv, "affect_enabled", true) {
		label := st.Affect.Label
		weight := sf.Float(adv, "affect_tone_weight", 0.15)
		if label != "" && weight > 0 && !strings.Contains(strings.ToLower(auto), "feel") {
			lines = append(lines, "(tone: "+label+")")
		}
	}
	text := pyStrip(strings.Join(lines, " "))
	return cut(text, capChars+40), nil
}

// dumpDrv is codec._dump_drv for the drives mapping.
func dumpDrv(d map[string]float64) string {
	out := make([]string, 0, 4)
	for _, k := range drives.DriveKeys {
		n := int(math.RoundToEven(d[k] * 100))
		out = append(out, strconv.Itoa(max(0, min(100, n))))
	}
	return strings.Join(out, ",")
}

// dumpList is codec._dump_list.
func dumpList(items []string) string {
	blob := joinNonEmpty(items)
	return strconv.Itoa(cpLen(blob)) + ":" + blob
}

// encodeState is codec.encode("state", fields) at level 1, dense.
func encodeState(d map[string]float64, aff, bio string, beliefs []string) string {
	return "ril1 state" +
		" drv=" + dumpDrv(d) +
		" aff=" + aff +
		" bio=" + strconv.Itoa(cpLen(bio)) + ":" + bio +
		" bel=" + dumpList(beliefs)
}

// stateNatural is codec.format_natural("state", decode(encodeState(...))).
// The dense form round-trips: drives come back as n/100, the beliefs list
// as its comma-split, non-empty parts.
func stateNatural(d map[string]float64, aff, bio string, beliefs []string) string {
	drv := make([]string, 0, 4)
	for _, k := range drives.DriveKeys {
		n := max(0, min(100, int(math.RoundToEven(d[k]*100))))
		drv = append(drv, k+" "+strconv.FormatFloat(float64(n)/100.0, 'f', 2, 64))
	}
	bits := []string{"Drives: " + strings.Join(drv, ", ") + "."}
	// _split_fields reads a bare value up to the next " tag="; a label is one
	// word, so it comes back unchanged.
	if aff != "" {
		bits = append(bits, "Feeling: "+aff+".")
	}
	if bio != "" {
		bits = append(bits, bio)
	}
	var bel []string
	for _, x := range strings.Split(joinNonEmpty(beliefs), ",") {
		if x != "" {
			bel = append(bel, x)
		}
	}
	if len(bel) > 0 {
		if len(bel) > 6 {
			bel = bel[:6]
		}
		bits = append(bits, "Beliefs: "+strings.Join(bel, "; "))
	}
	return pyStrip(strings.Join(bits, " "))
}

func joinNonEmpty(items []string) string {
	var kept []string
	for _, x := range items {
		if s := pyStrip(x); s != "" {
			kept = append(kept, s)
		}
	}
	return strings.Join(kept, ",")
}

var coreTags = map[string]bool{
	"ril1": true, "state": true, "dream": true, "note": true, "brief": true, "report": true,
	"coin": true, "lex": true, "drv": true, "aff": true, "bio": true, "bel": true, "tsk": true,
	"cst": true, "out": true, "ok": true, "id": true, "gloss": true, "from": true, "used": true,
	"miss": true, "term": true, "ver": true,
}

// loadLexiconTerms is lexicon.load_lexicon(home)["terms"] as term -> gloss.
func loadLexiconTerms(home string) map[string]string {
	data, err := os.ReadFile(filepath.Join(home, "inner_lexicon.json"))
	if err != nil {
		return nil
	}
	var raw map[string]any
	if json.Unmarshal(data, &raw) != nil {
		return nil
	}
	terms, _ := raw["terms"].(map[string]any)
	out := map[string]string{}
	for k, v := range terms {
		m, ok := v.(map[string]any)
		if !ok {
			continue
		}
		if g := pyStr(m, "gloss"); pyStrip(g) != "" {
			out[k] = g
		}
	}
	return out
}

// expandTerms is codec.expand_terms: whole-word coined terms become their
// gloss, longest term first.
func expandTerms(text string, terms map[string]string) string {
	if text == "" || len(terms) == 0 {
		return text
	}
	keys := make([]string, 0, len(terms))
	for k := range terms {
		keys = append(keys, k)
	}
	sort.SliceStable(keys, func(i, j int) bool { return cpLen(keys[i]) > cpLen(keys[j]) })
	out := text
	for _, term := range keys {
		gloss := terms[term]
		if term == "" || gloss == "" || coreTags[term] {
			continue
		}
		re := regexp.MustCompile(`\b` + regexp.QuoteMeta(term) + `\b`)
		out = re.ReplaceAllLiteralString(out, gloss)
	}
	return out
}

const (
	auditName     = "inner_language.jsonl"
	auditMaxBytes = 256 * 1024
	auditMaxLines = 400
)

// recordAudit is inner_language.audit.record_audit: one JSON line per use,
// in Python's json.dumps layout, then the rotation.
func recordAudit(env Env, kind, dense, decoded string) error {
	path := filepath.Join(env.Home, auditName)
	line := `{"ts": ` + pyFloat(sf.Unix(env.now())) +
		`, "kind": ` + pyJSONString(kind) +
		`, "dense": ` + pyJSONString(dense) +
		`, "decoded": ` + pyJSONString(decoded) + "}\n"
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil // Python suppresses write failures here
	}
	_, _ = f.WriteString(line)
	_ = f.Close()
	rotateAudit(path)
	return nil
}

func rotateAudit(path string) {
	st, err := os.Stat(path)
	if err != nil || st.Size() <= auditMaxBytes {
		return
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return
	}
	var lines []string
	for _, ln := range strings.Split(strings.ReplaceAll(string(data), "\r\n", "\n"), "\n") {
		if pyStrip(ln) != "" {
			lines = append(lines, ln)
		}
	}
	if len(lines) <= auditMaxLines/2 {
		return
	}
	keep := lines[len(lines)/2:]
	if len(lines) > auditMaxLines {
		keep = lines[len(lines)-auditMaxLines:]
	}
	_ = sf.WriteBytes(path, []byte(strings.Join(keep, "\n")+"\n"))
}

// pyFloat is Python's repr(float).
func pyFloat(f float64) string {
	if math.IsInf(f, 0) || math.IsNaN(f) {
		return strconv.FormatFloat(f, 'g', -1, 64)
	}
	a := math.Abs(f)
	if a != 0 && (a < 1e-4 || a >= 1e16) {
		s := strconv.FormatFloat(f, 'e', -1, 64)
		mant, exp, _ := strings.Cut(s, "e")
		sign := exp[0]
		digits := strings.TrimLeft(exp[1:], "0")
		if len(digits) < 2 {
			digits = strings.Repeat("0", 2-len(digits)) + digits
		}
		return mant + "e" + string(sign) + digits
	}
	s := strconv.FormatFloat(f, 'f', -1, 64)
	if !strings.ContainsAny(s, ".") {
		s += ".0"
	}
	return s
}

// pyJSONString is json.dumps(s, ensure_ascii=False) for a string.
func pyJSONString(s string) string {
	var b strings.Builder
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
			if r < 0x20 {
				b.WriteString(`\u00`)
				b.WriteString(strconv.FormatInt(int64(r)>>4, 16))
				b.WriteString(strconv.FormatInt(int64(r)&0xf, 16))
			} else {
				b.WriteRune(r)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}
