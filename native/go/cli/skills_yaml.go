package cli

// PyYAML parity for skill files. Python's skill loader reads frontmatter with
// yaml.safe_load (YAML 1.1 typing: yes/no are bools, 0x1f is an int, dates
// are dates) and the exporter writes it back with yaml.dump. Exports must be
// byte-identical to what the Python exporter wrote, so this file ports the
// parts of PyYAML 6 those calls reach: the 1.1 implicit resolver, the scalar
// constructors, the representer and the block/flow emitter with its scalar
// analysis, quoting and 80-column folding.

import (
	"fmt"
	"math"
	"math/big"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	yaml "go.yaml.in/yaml/v4"
)

// skKV / skMap are an insertion-ordered Python dict.
type skKV struct {
	K string
	V any
}
type skMap []skKV

func (m skMap) get(k string) (any, bool) {
	for _, e := range m {
		if e.K == k {
			return e.V, true
		}
	}
	return nil, false
}

func (m skMap) has(k string) bool { _, ok := m.get(k); return ok }

// set assigns like a dict: in place when present, appended otherwise.
func (m *skMap) set(k string, v any) {
	for i := range *m {
		if (*m)[i].K == k {
			(*m)[i].V = v
			return
		}
	}
	*m = append(*m, skKV{k, v})
}

func (m *skMap) pop(k string) (any, bool) {
	for i := range *m {
		if (*m)[i].K == k {
			v := (*m)[i].V
			*m = append((*m)[:i], (*m)[i+1:]...)
			return v, true
		}
	}
	return nil, false
}

// skInt is a Python int (arbitrary precision), kept as its decimal text.
type skInt struct{ s string }

// skTime is a datetime.date / datetime.datetime, kept as the text PyYAML's
// representer writes for it (isoformat / isoformat(' ')).
type skTime struct{ s string }

// -- resolver (yaml/resolver.py) ----------------------------------------------

var (
	skReBool  = regexp.MustCompile(`^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$`)
	skReFloat = regexp.MustCompile(`^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$`)
	skReInt   = regexp.MustCompile(`^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$`)
	skReNull  = regexp.MustCompile(`^(?:~|null|Null|NULL|)$`)
	skReTime  = regexp.MustCompile(`^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$`)
	skReTimeG = regexp.MustCompile(`^([0-9][0-9][0-9][0-9])-([0-9][0-9]?)-([0-9][0-9]?)(?:(?:[Tt]|[ \t]+)([0-9][0-9]?):([0-9][0-9]):([0-9][0-9])(?:\.([0-9]*))?(?:[ \t]*(Z|([-+])([0-9][0-9]?)(?::([0-9][0-9]))?))?)?$`)
)

// skResolve is Resolver.resolve for a plain scalar: the YAML 1.1 tag it gets.
func skResolve(v string) string {
	first := byte(0)
	if v != "" {
		first = v[0]
	}
	in := func(set string) bool { return v != "" && strings.IndexByte(set, first) >= 0 }
	if in("yYnNtTfFoO") && skReBool.MatchString(v) {
		return "bool"
	}
	if in("-+0123456789.") && skReFloat.MatchString(v) {
		return "float"
	}
	if in("-+0123456789") && skReInt.MatchString(v) {
		return "int"
	}
	if v == "<<" {
		return "merge"
	}
	if (v == "" || in("~nN")) && skReNull.MatchString(v) {
		return "null"
	}
	if in("0123456789") && skReTime.MatchString(v) {
		return "timestamp"
	}
	if v == "=" {
		return "value"
	}
	return "str"
}

// -- constructors (yaml/constructor.py SafeConstructor) -----------------------

func skConstructBool(v string) bool {
	switch strings.ToLower(v) {
	case "yes", "true", "on":
		return true
	}
	return false
}

func skConstructInt(v string) any {
	s := strings.ReplaceAll(v, "_", "")
	sign := 1
	if strings.HasPrefix(s, "-") {
		sign = -1
	}
	if s != "" && (s[0] == '-' || s[0] == '+') {
		s = s[1:]
	}
	n := new(big.Int)
	switch {
	case s == "0":
		return skInt{"0"}
	case strings.HasPrefix(s, "0b"):
		n.SetString(s[2:], 2)
	case strings.HasPrefix(s, "0x"):
		n.SetString(s[2:], 16)
	case strings.HasPrefix(s, "0"):
		n.SetString(s, 8)
	case strings.Contains(s, ":"):
		base := big.NewInt(1)
		parts := strings.Split(s, ":")
		for i := len(parts) - 1; i >= 0; i-- {
			d, _ := new(big.Int).SetString(parts[i], 10)
			n.Add(n, new(big.Int).Mul(d, base))
			base.Mul(base, big.NewInt(60))
		}
	default:
		n.SetString(s, 10)
	}
	if sign < 0 {
		n.Neg(n)
	}
	return skInt{n.String()}
}

func skConstructFloat(v string) float64 {
	s := strings.ToLower(strings.ReplaceAll(v, "_", ""))
	sign := 1.0
	if strings.HasPrefix(s, "-") {
		sign = -1
	}
	if s != "" && (s[0] == '-' || s[0] == '+') {
		s = s[1:]
	}
	switch {
	case s == ".inf":
		return sign * math.Inf(1)
	case s == ".nan":
		return math.NaN()
	case strings.Contains(s, ":"):
		parts := strings.Split(s, ":")
		base, val := 1.0, 0.0
		for i := len(parts) - 1; i >= 0; i-- {
			f, _ := strconv.ParseFloat(parts[i], 64)
			val += f * base
			base *= 60
		}
		return sign * val
	}
	f, _ := strconv.ParseFloat(s, 64)
	return sign * f
}

func skConstructTimestamp(v string) (any, error) {
	m := skReTimeG.FindStringSubmatch(v)
	if m == nil {
		return v, nil
	}
	atoi := func(s string) int { n, _ := strconv.Atoi(s); return n }
	y, mo, d := atoi(m[1]), atoi(m[2]), atoi(m[3])
	// datetime.date() raises ValueError for an impossible day.
	if t := time.Date(y, time.Month(mo), d, 0, 0, 0, 0, time.UTC); y < 1 || mo < 1 || mo > 12 || t.Day() != d {
		return nil, fmt.Errorf("ValueError: day is out of range for month: %s", v)
	}
	date := fmt.Sprintf("%04d-%02d-%02d", y, mo, d)
	if m[4] == "" {
		return skTime{date}, nil
	}
	if atoi(m[4]) > 23 || atoi(m[5]) > 59 || atoi(m[6]) > 59 {
		return nil, fmt.Errorf("ValueError: time out of range: %s", v)
	}
	out := fmt.Sprintf("%s %02d:%02d:%02d", date, atoi(m[4]), atoi(m[5]), atoi(m[6]))
	if frac := m[7]; frac != "" {
		if len(frac) > 6 {
			frac = frac[:6]
		}
		for len(frac) < 6 {
			frac += "0"
		}
		if atoi(frac) != 0 {
			out += "." + frac
		}
	}
	switch {
	case m[9] != "":
		h, mi := atoi(m[10]), atoi(m[11])
		total := h*60 + mi
		sign := "+"
		if m[9] == "-" && total != 0 {
			sign = "-"
		}
		if total >= 24*60 { // datetime.timezone refuses offsets of a day or more
			return nil, fmt.Errorf("ValueError: offset out of range: %s", v)
		}
		out += fmt.Sprintf("%s%02d:%02d", sign, total/60, total%60)
	case m[8] != "":
		out += "+00:00"
	}
	return skTime{out}, nil
}

// skYAMLLoad is yaml.safe_load of one document.
func skYAMLLoad(text string) (any, error) {
	var doc yaml.Node
	if err := yaml.Unmarshal([]byte(text), &doc); err != nil {
		return nil, err
	}
	if doc.Kind == 0 {
		return nil, nil
	}
	return skNode(&doc, 0)
}

func skNode(n *yaml.Node, depth int) (any, error) {
	if depth > 200 {
		return nil, fmt.Errorf("yaml nesting too deep")
	}
	switch n.Kind {
	case yaml.DocumentNode:
		if len(n.Content) == 0 {
			return nil, nil
		}
		return skNode(n.Content[0], depth+1)
	case yaml.AliasNode:
		if n.Alias == nil {
			return nil, fmt.Errorf("unknown alias")
		}
		return skNode(n.Alias, depth+1)
	case yaml.SequenceNode:
		out := []any{}
		for _, c := range n.Content {
			v, err := skNode(c, depth+1)
			if err != nil {
				return nil, err
			}
			out = append(out, v)
		}
		return out, nil
	case yaml.MappingNode:
		out := skMap{}
		var own []skKV
		var merged skMap
		for i := 0; i+1 < len(n.Content); i += 2 {
			kn, vn := n.Content[i], n.Content[i+1]
			if kn.Kind == yaml.ScalarNode && kn.Style == 0 && kn.Value == "<<" {
				// SafeConstructor.flatten_mapping: merged keys first.
				var srcs []*yaml.Node
				target := vn
				if target.Kind == yaml.AliasNode && target.Alias != nil {
					target = target.Alias
				}
				if target.Kind == yaml.SequenceNode {
					srcs = target.Content
				} else {
					srcs = []*yaml.Node{vn}
				}
				var parts []skMap
				for _, sn := range srcs {
					v, err := skNode(sn, depth+1)
					if err != nil {
						return nil, err
					}
					mm, ok := v.(skMap)
					if !ok {
						return nil, fmt.Errorf("expected a mapping for merging")
					}
					parts = append(parts, mm)
				}
				// Later sources in a merge sequence lose to earlier ones.
				for i := len(parts) - 1; i >= 0; i-- {
					for _, e := range parts[i] {
						merged.set(e.K, e.V)
					}
				}
				continue
			}
			kv, err := skNode(kn, depth+1)
			if err != nil {
				return nil, err
			}
			vv, err := skNode(vn, depth+1)
			if err != nil {
				return nil, err
			}
			own = append(own, skKV{skKeyText(kv), vv})
		}
		for _, e := range merged {
			out.set(e.K, e.V)
		}
		for _, e := range own {
			out.set(e.K, e.V)
		}
		return out, nil
	}
	// Scalar.
	if n.Style&yaml.TaggedStyle != 0 {
		switch n.ShortTag() {
		case "!!str":
			return n.Value, nil
		case "!!int":
			return skConstructInt(n.Value), nil
		case "!!float":
			return skConstructFloat(n.Value), nil
		case "!!bool":
			return skConstructBool(n.Value), nil
		case "!!null":
			return nil, nil
		case "!!timestamp":
			return skConstructTimestamp(n.Value)
		}
		return n.Value, nil
	}
	if n.Style&(yaml.DoubleQuotedStyle|yaml.SingleQuotedStyle|yaml.LiteralStyle|yaml.FoldedStyle) != 0 {
		return n.Value, nil
	}
	switch skResolve(n.Value) {
	case "bool":
		return skConstructBool(n.Value), nil
	case "float":
		return skConstructFloat(n.Value), nil
	case "int":
		return skConstructInt(n.Value), nil
	case "null":
		return nil, nil
	case "timestamp":
		return skConstructTimestamp(n.Value)
	}
	return n.Value, nil
}

// skKeyText is str() of a mapping key (keys stay text in this model).
func skKeyText(v any) string { return skPyStr(v) }

// -- Python str() / repr() of loaded values ------------------------------------

// skPyFloat is repr(float).
func skPyFloat(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
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
	if !strings.Contains(s, ".") {
		s += ".0"
	}
	return s
}

// skPyStr is str(v) for a loaded YAML value.
func skPyStr(v any) string {
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
	case skInt:
		return t.s
	case float64:
		return skPyFloat(t)
	case skTime:
		return t.s
	}
	return skPyRepr(v)
}

// skPyRepr is repr(v) for a loaded YAML / JSON value.
func skPyRepr(v any) string {
	switch t := v.(type) {
	case string:
		return skPyReprStr(t)
	case []any:
		parts := make([]string, len(t))
		for i, x := range t {
			parts[i] = skPyRepr(x)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case skMap:
		parts := make([]string, len(t))
		for i, e := range t {
			parts[i] = skPyReprStr(e.K) + ": " + skPyRepr(e.V)
		}
		return "{" + strings.Join(parts, ", ") + "}"
	case skTime:
		return t.s
	}
	return skPyStr(v)
}

func skPyReprStr(s string) string {
	q := "'"
	if strings.Contains(s, "'") && !strings.Contains(s, `"`) {
		q = `"`
	}
	var b strings.Builder
	b.WriteString(q)
	for _, r := range s {
		switch {
		case string(r) == q || r == '\\':
			b.WriteString(`\` + string(r))
		case r == '\n':
			b.WriteString(`\n`)
		case r == '\r':
			b.WriteString(`\r`)
		case r == '\t':
			b.WriteString(`\t`)
		case r < 0x20 || r == 0x7f:
			fmt.Fprintf(&b, `\x%02x`, r)
		default:
			b.WriteRune(r)
		}
	}
	b.WriteString(q)
	return b.String()
}

// skTruthy is Python bool(v).
func skTruthy(v any) bool {
	switch t := v.(type) {
	case nil:
		return false
	case bool:
		return t
	case string:
		return t != ""
	case skInt:
		return t.s != "0"
	case float64:
		return t != 0
	case []any:
		return len(t) > 0
	case skMap:
		return len(t) > 0
	}
	return true
}

// -- dumper (representer + serializer + emitter) --------------------------------

type skDumpOpts struct {
	sortKeys     bool
	allowUnicode bool
}

// skYAMLDump is yaml.dump(data, default_flow_style=False, …).
func skYAMLDump(data any, o skDumpOpts) string {
	e := &skEmitter{opts: o, indent: -1, whitespace: true, indention: true}
	e.node(skRepresent(data, o), true, false, false, false)
	// expect_document_end
	e.writeIndent()
	if e.openEnded {
		e.writeIndicator("...", true, false, false)
		e.writeIndent()
	}
	return e.b.String()
}

type skNodeKind int

const (
	skScalarNode skNodeKind = iota
	skSeqNode
	skMapNode
)

type skYNode struct {
	kind      skNodeKind
	value     string
	implicit0 bool // the plain form resolves back to the node's tag
	items     []*skYNode
	pairs     [][2]*skYNode
}

func skRepresent(v any, o skDumpOpts) *skYNode {
	scalar := func(s string, plainOK bool) *skYNode {
		return &skYNode{kind: skScalarNode, value: s, implicit0: plainOK}
	}
	switch t := v.(type) {
	case nil:
		return scalar("null", true)
	case string:
		return scalar(t, skResolve(t) == "str")
	case bool:
		if t {
			return scalar("true", true)
		}
		return scalar("false", true)
	case skInt:
		return scalar(t.s, true)
	case int:
		return scalar(strconv.Itoa(t), true)
	case int64:
		return scalar(strconv.FormatInt(t, 10), true)
	case float64:
		var s string
		switch {
		case math.IsNaN(t):
			s = ".nan"
		case math.IsInf(t, 1):
			s = ".inf"
		case math.IsInf(t, -1):
			s = "-.inf"
		default:
			s = strings.ToLower(skPyFloat(t))
			if !strings.Contains(s, ".") && strings.Contains(s, "e") {
				s = strings.Replace(s, "e", ".0e", 1)
			}
		}
		return scalar(s, true)
	case skTime:
		return scalar(t.s, true)
	case []string:
		n := &skYNode{kind: skSeqNode}
		for _, x := range t {
			n.items = append(n.items, skRepresent(x, o))
		}
		return n
	case []any:
		n := &skYNode{kind: skSeqNode}
		for _, x := range t {
			n.items = append(n.items, skRepresent(x, o))
		}
		return n
	case skMap:
		pairs := append(skMap(nil), t...)
		if o.sortKeys {
			sort.SliceStable(pairs, func(i, j int) bool { return pairs[i].K < pairs[j].K })
		}
		n := &skYNode{kind: skMapNode}
		for _, e := range pairs {
			n.pairs = append(n.pairs, [2]*skYNode{skRepresent(e.K, o), skRepresent(e.V, o)})
		}
		return n
	}
	return scalar(fmt.Sprint(v), false)
}

type skEmitter struct {
	opts       skDumpOpts
	b          strings.Builder
	indents    []int
	indent     int // -1 is None
	flowLevel  int
	column     int
	whitespace bool
	indention  bool
	openEnded  bool

	rootCtx, seqCtx, mapCtx, simpleKeyCtx bool
}

const skBestIndent, skBestWidth = 2, 80

func (e *skEmitter) increaseIndent(flow, indentless bool) {
	e.indents = append(e.indents, e.indent)
	if e.indent < 0 {
		if flow {
			e.indent = skBestIndent
		} else {
			e.indent = 0
		}
	} else if !indentless {
		e.indent += skBestIndent
	}
}

func (e *skEmitter) popIndent() {
	e.indent = e.indents[len(e.indents)-1]
	e.indents = e.indents[:len(e.indents)-1]
}

func (e *skEmitter) write(s string) {
	e.b.WriteString(s)
}

func (e *skEmitter) writeIndicator(ind string, needWS, whitespace, indention bool) {
	data := ind
	if !e.whitespace && needWS {
		data = " " + ind
	}
	e.whitespace = whitespace
	e.indention = e.indention && indention
	e.column += len([]rune(data))
	e.openEnded = false
	e.write(data)
}

func (e *skEmitter) writeIndent() {
	indent := e.indent
	if indent < 0 {
		indent = 0
	}
	if !e.indention || e.column > indent || (e.column == indent && !e.whitespace) {
		e.writeLineBreak("")
	}
	if e.column < indent {
		e.whitespace = true
		e.write(strings.Repeat(" ", indent-e.column))
		e.column = indent
	}
}

func (e *skEmitter) writeLineBreak(data string) {
	if data == "" {
		data = "\n"
	}
	e.whitespace = true
	e.indention = true
	e.column = 0
	e.write(data)
}

func (e *skEmitter) node(n *skYNode, root, seq, mapping, simpleKey bool) {
	e.rootCtx, e.seqCtx, e.mapCtx, e.simpleKeyCtx = root, seq, mapping, simpleKey
	switch n.kind {
	case skScalarNode:
		e.increaseIndent(true, false)
		e.processScalar(n)
		e.popIndent()
	case skSeqNode:
		if e.flowLevel > 0 || len(n.items) == 0 {
			e.flowSequence(n)
		} else {
			e.blockSequence(n, mapping)
		}
	case skMapNode:
		if e.flowLevel > 0 || len(n.pairs) == 0 {
			e.flowMapping(n)
		} else {
			e.blockMapping(n)
		}
	}
}

func (e *skEmitter) flowSequence(n *skYNode) {
	e.writeIndicator("[", true, true, false)
	e.flowLevel++
	e.increaseIndent(true, false)
	for i, it := range n.items {
		if i > 0 {
			e.writeIndicator(",", false, false, false)
		}
		if e.column > skBestWidth {
			e.writeIndent()
		}
		e.node(it, false, true, false, false)
	}
	e.popIndent()
	e.flowLevel--
	e.writeIndicator("]", false, false, false)
}

func (e *skEmitter) flowMapping(n *skYNode) {
	e.writeIndicator("{", true, true, false)
	e.flowLevel++
	e.increaseIndent(true, false)
	for i, p := range n.pairs {
		if i > 0 {
			e.writeIndicator(",", false, false, false)
		}
		if e.column > skBestWidth {
			e.writeIndent()
		}
		if e.checkSimpleKey(p[0]) {
			e.node(p[0], false, false, true, true)
			e.writeIndicator(":", false, false, false)
			e.node(p[1], false, false, true, false)
		} else {
			e.writeIndicator("?", true, false, false)
			e.node(p[0], false, false, true, false)
			if e.column > skBestWidth {
				e.writeIndent()
			}
			e.writeIndicator(":", true, false, false)
			e.node(p[1], false, false, true, false)
		}
	}
	e.popIndent()
	e.flowLevel--
	e.writeIndicator("}", false, false, false)
}

func (e *skEmitter) blockSequence(n *skYNode, mappingCtx bool) {
	indentless := mappingCtx && !e.indention
	e.increaseIndent(false, indentless)
	for _, it := range n.items {
		e.writeIndent()
		e.writeIndicator("-", true, false, true)
		e.node(it, false, true, false, false)
	}
	e.popIndent()
}

func (e *skEmitter) blockMapping(n *skYNode) {
	e.increaseIndent(false, false)
	for _, p := range n.pairs {
		e.writeIndent()
		if e.checkSimpleKey(p[0]) {
			e.node(p[0], false, false, true, true)
			e.writeIndicator(":", false, false, false)
			e.node(p[1], false, false, true, false)
		} else {
			e.writeIndicator("?", true, false, true)
			e.node(p[0], false, false, true, false)
			e.writeIndent()
			e.writeIndicator(":", true, false, true)
			e.node(p[1], false, false, true, false)
		}
	}
	e.popIndent()
}

func (e *skEmitter) checkSimpleKey(n *skYNode) bool {
	switch n.kind {
	case skScalarNode:
		a := e.analyze(n.value)
		return len([]rune(n.value)) < 128 && !a.empty && !a.multiline
	case skSeqNode:
		return len(n.items) == 0
	case skMapNode:
		return len(n.pairs) == 0
	}
	return false
}

type skAnalysis struct {
	empty, multiline                                                      bool
	allowFlowPlain, allowBlockPlain, allowSingle, allowDouble, allowBlock bool
}

func skIsBreak(r rune) bool { return r == '\n' || r == 0x85 || r == 0x2028 || r == 0x2029 }

func skIsWSOrEnd(r rune) bool {
	return r == 0 || r == ' ' || r == '\t' || r == '\r' || skIsBreak(r)
}

func (e *skEmitter) analyze(scalar string) skAnalysis {
	if scalar == "" {
		return skAnalysis{empty: true, allowBlockPlain: true, allowSingle: true, allowDouble: true}
	}
	rs := []rune(scalar)
	var blockInd, flowInd, lineBreaks, special bool
	var leadSpace, leadBreak, trailSpace, trailBreak, breakSpace, spaceBreak bool
	if strings.HasPrefix(scalar, "---") || strings.HasPrefix(scalar, "...") {
		blockInd, flowInd = true, true
	}
	precededWS := true
	followedWS := len(rs) == 1 || skIsWSOrEnd(rs[1])
	prevSpace, prevBreak := false, false
	for i, ch := range rs {
		if i == 0 {
			if strings.ContainsRune("#,[]{}&*!|>'\"%@`", ch) {
				flowInd, blockInd = true, true
			}
			if ch == '?' || ch == ':' {
				flowInd = true
				if followedWS {
					blockInd = true
				}
			}
			if ch == '-' && followedWS {
				flowInd, blockInd = true, true
			}
		} else {
			if strings.ContainsRune(",?[]{}", ch) {
				flowInd = true
			}
			if ch == ':' {
				flowInd = true
				if followedWS {
					blockInd = true
				}
			}
			if ch == '#' && precededWS {
				flowInd, blockInd = true, true
			}
		}
		if skIsBreak(ch) {
			lineBreaks = true
		}
		if !(ch == '\n' || (ch >= 0x20 && ch <= 0x7e)) {
			if (ch == 0x85 || (ch >= 0xa0 && ch <= 0xd7ff) || (ch >= 0xe000 && ch <= 0xfffd) ||
				(ch >= 0x10000 && ch < 0x10ffff)) && ch != 0xfeff {
				if !e.opts.allowUnicode {
					special = true
				}
			} else {
				special = true
			}
		}
		switch {
		case ch == ' ':
			if i == 0 {
				leadSpace = true
			}
			if i == len(rs)-1 {
				trailSpace = true
			}
			if prevBreak {
				breakSpace = true
			}
			prevSpace, prevBreak = true, false
		case skIsBreak(ch):
			if i == 0 {
				leadBreak = true
			}
			if i == len(rs)-1 {
				trailBreak = true
			}
			if prevSpace {
				spaceBreak = true
			}
			prevSpace, prevBreak = false, true
		default:
			prevSpace, prevBreak = false, false
		}
		precededWS = skIsWSOrEnd(ch)
		followedWS = i+2 >= len(rs) || skIsWSOrEnd(rs[i+2])
	}
	a := skAnalysis{multiline: lineBreaks, allowFlowPlain: true, allowBlockPlain: true, allowSingle: true, allowDouble: true, allowBlock: true}
	if leadSpace || leadBreak || trailSpace || trailBreak {
		a.allowFlowPlain, a.allowBlockPlain = false, false
	}
	if trailSpace {
		a.allowBlock = false
	}
	if breakSpace {
		a.allowFlowPlain, a.allowBlockPlain, a.allowSingle = false, false, false
	}
	if spaceBreak || special {
		a.allowFlowPlain, a.allowBlockPlain, a.allowSingle, a.allowBlock = false, false, false, false
	}
	if lineBreaks {
		a.allowFlowPlain, a.allowBlockPlain = false, false
	}
	if flowInd {
		a.allowFlowPlain = false
	}
	if blockInd {
		a.allowBlockPlain = false
	}
	return a
}

func (e *skEmitter) chooseStyle(n *skYNode, a skAnalysis) string {
	if n.implicit0 {
		if !(e.simpleKeyCtx && (a.empty || a.multiline)) &&
			((e.flowLevel > 0 && a.allowFlowPlain) || (e.flowLevel == 0 && a.allowBlockPlain)) {
			return ""
		}
	}
	if a.allowSingle && !(e.simpleKeyCtx && a.multiline) {
		return "'"
	}
	return `"`
}

func (e *skEmitter) processScalar(n *skYNode) {
	a := e.analyze(n.value)
	style := e.chooseStyle(n, a)
	split := !e.simpleKeyCtx
	switch style {
	case `"`:
		e.writeDoubleQuoted(n.value, split)
	case "'":
		e.writeSingleQuoted(n.value, split)
	default:
		e.writePlain(n.value, split)
	}
}

func (e *skEmitter) writePlain(text string, split bool) {
	if e.rootCtx {
		e.openEnded = true
	}
	if text == "" {
		return
	}
	if !e.whitespace {
		e.column++
		e.write(" ")
	}
	e.whitespace, e.indention = false, false
	rs := []rune(text)
	spaces, breaks := false, false
	start := 0
	for end := 0; end <= len(rs); end++ {
		var ch rune = -1
		if end < len(rs) {
			ch = rs[end]
		}
		switch {
		case spaces:
			if ch != ' ' {
				if start+1 == end && e.column > skBestWidth && split {
					e.writeIndent()
					e.whitespace, e.indention = false, false
				} else {
					e.column += end - start
					e.write(string(rs[start:end]))
				}
				start = end
			}
		case breaks:
			if ch < 0 || !skIsBreak(ch) {
				if rs[start] == '\n' {
					e.writeLineBreak("")
				}
				for _, br := range rs[start:end] {
					if br == '\n' {
						e.writeLineBreak("")
					} else {
						e.writeLineBreak(string(br))
					}
				}
				e.writeIndent()
				e.whitespace, e.indention = false, false
				start = end
			}
		default:
			if ch < 0 || ch == ' ' || skIsBreak(ch) {
				e.column += end - start
				e.write(string(rs[start:end]))
				start = end
			}
		}
		if ch >= 0 {
			spaces = ch == ' '
			breaks = skIsBreak(ch)
		}
	}
}

func (e *skEmitter) writeSingleQuoted(text string, split bool) {
	e.writeIndicator("'", true, false, false)
	rs := []rune(text)
	spaces, breaks := false, false
	start := 0
	for end := 0; end <= len(rs); end++ {
		var ch rune = -1
		if end < len(rs) {
			ch = rs[end]
		}
		switch {
		case spaces:
			if ch < 0 || ch != ' ' {
				if start+1 == end && e.column > skBestWidth && split && start != 0 && end != len(rs) {
					e.writeIndent()
				} else {
					e.column += end - start
					e.write(string(rs[start:end]))
				}
				start = end
			}
		case breaks:
			if ch < 0 || !skIsBreak(ch) {
				if rs[start] == '\n' {
					e.writeLineBreak("")
				}
				for _, br := range rs[start:end] {
					if br == '\n' {
						e.writeLineBreak("")
					} else {
						e.writeLineBreak(string(br))
					}
				}
				e.writeIndent()
				start = end
			}
		default:
			if ch < 0 || ch == ' ' || skIsBreak(ch) || ch == '\'' {
				if start < end {
					e.column += end - start
					e.write(string(rs[start:end]))
					start = end
				}
			}
		}
		if ch == '\'' {
			e.column += 2
			e.write("''")
			start = end + 1
		}
		if ch >= 0 {
			spaces = ch == ' '
			breaks = skIsBreak(ch)
		}
	}
	e.writeIndicator("'", false, false, false)
}

var skEscapes = map[rune]string{
	0: "0", 0x07: "a", 0x08: "b", 0x09: "t", 0x0a: "n", 0x0b: "v", 0x0c: "f", 0x0d: "r",
	0x1b: "e", '"': `"`, '\\': `\`, 0x85: "N", 0xa0: "_", 0x2028: "L", 0x2029: "P",
}

func (e *skEmitter) writeDoubleQuoted(text string, split bool) {
	e.writeIndicator(`"`, true, false, false)
	rs := []rune(text)
	start := 0
	for end := 0; end <= len(rs); end++ {
		var ch rune = -1
		if end < len(rs) {
			ch = rs[end]
		}
		if ch < 0 || ch == '"' || ch == '\\' || ch == 0x85 || ch == 0x2028 || ch == 0x2029 || ch == 0xfeff ||
			!((ch >= 0x20 && ch <= 0x7e) || (e.opts.allowUnicode && ((ch >= 0xa0 && ch <= 0xd7ff) || (ch >= 0xe000 && ch <= 0xfffd)))) {
			if start < end {
				e.column += end - start
				e.write(string(rs[start:end]))
				start = end
			}
			if ch >= 0 {
				var data string
				if r, ok := skEscapes[ch]; ok {
					data = `\` + r
				} else if ch <= 0xff {
					data = fmt.Sprintf(`\x%02X`, ch)
				} else if ch <= 0xffff {
					data = fmt.Sprintf(`\u%04X`, ch)
				} else {
					data = fmt.Sprintf(`\U%08X`, ch)
				}
				e.column += len(data)
				e.write(data)
				start = end + 1
			}
		}
		if end > 0 && end < len(rs)-1 && (ch == ' ' || start >= end) && e.column+(end-start) > skBestWidth && split {
			data := `\`
			if start < end {
				data = string(rs[start:end]) + `\`
			}
			if start < end {
				start = end
			}
			e.column += len([]rune(data))
			e.write(data)
			e.writeIndent()
			e.whitespace, e.indention = false, false
			if rs[start] == ' ' {
				e.column++
				e.write(`\`)
			}
		}
	}
	e.writeIndicator(`"`, false, false, false)
}
