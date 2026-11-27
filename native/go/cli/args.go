// Package cli is the `remedy` command line, run by remedy-runtime when its
// first argument is a command word (memory, skill, serve, ...). The Python
// `remedy` entry point only finds this binary and hands it argv.
package cli

import (
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
)

// FlagKind is how a flag's value is parsed.
type FlagKind int

const (
	// FlagString takes one value.
	FlagString FlagKind = iota
	// FlagBool takes no value.
	FlagBool
	// FlagInt takes one integer value.
	FlagInt
	// FlagFloat takes one number.
	FlagFloat
)

// Flag is one --option.
type Flag struct {
	Name    string // without the leading dashes
	Kind    FlagKind
	Default string
	Choices []string
	Help    string
	Hidden  bool
}

// Arg is one positional argument.
type Arg struct {
	Name     string
	Help     string
	Optional bool   // nargs="?"
	Default  string // used when Optional and absent
	Many     bool   // nargs="*": zero or more
	Rest     bool   // nargs=REMAINDER: everything left, flags included
}

// Command is a node in the command tree.
type Command struct {
	Name        string
	Help        string
	Flags       []Flag
	Args        []Arg
	Sub         []*Command
	SubRequired bool
	Run         func(*Ctx) int
}

// Parsed holds what the parser found for one command path.
type Parsed struct {
	Path  []string // e.g. ["memory", "search"]
	flags map[string]string
	set   map[string]bool
	args  map[string]string
	many  map[string][]string
	cmd   *Command
}

// Str returns a flag or positional as text.
func (p *Parsed) Str(name string) string {
	if v, ok := p.flags[name]; ok {
		return v
	}
	return p.args[name]
}

// Bool returns a bool flag.
func (p *Parsed) Bool(name string) bool { return p.flags[name] == "true" }

// Int returns an int flag or positional (already validated).
func (p *Parsed) Int(name string) int {
	n, _ := strconv.Atoi(p.Str(name))
	return n
}

// Float returns a float flag.
func (p *Parsed) Float(name string) float64 {
	f, _ := strconv.ParseFloat(p.Str(name), 64)
	return f
}

// List returns a Many or Rest positional.
func (p *Parsed) List(name string) []string { return p.many[name] }

// Has reports whether a flag was given on the command line.
func (p *Parsed) Has(name string) bool { return p.set[name] }

// Sub is the chosen subcommand of the leaf's parent, "" when none.
func (p *Parsed) Sub(depth int) string {
	if depth < len(p.Path) {
		return p.Path[depth]
	}
	return ""
}

// errHelp means -h/--help was printed.
var errHelp = errors.New("help")

// usageError is an argparse-style refusal: usage plus a message, exit 2.
type usageError struct {
	path []string
	msg  string
}

func (e *usageError) Error() string { return e.msg }

func flagDefaults(p *Parsed, c *Command) {
	for _, f := range c.Flags {
		if _, ok := p.flags[f.Name]; ok {
			continue
		}
		switch {
		case f.Kind == FlagBool:
			p.flags[f.Name] = "false"
		default:
			p.flags[f.Name] = f.Default
		}
	}
}

func findFlag(chain []*Command, name string) (*Flag, bool) {
	// The innermost command wins, then outward (globals live on the root).
	for i := len(chain) - 1; i >= 0; i-- {
		for j := range chain[i].Flags {
			if chain[i].Flags[j].Name == name {
				return &chain[i].Flags[j], true
			}
		}
	}
	return nil, false
}

func checkValue(f *Flag, v string) error {
	switch f.Kind {
	case FlagInt:
		if _, err := strconv.Atoi(strings.TrimSpace(v)); err != nil {
			return fmt.Errorf("argument --%s: invalid int value: '%s'", f.Name, v)
		}
	case FlagFloat:
		if _, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err != nil {
			return fmt.Errorf("argument --%s: invalid float value: '%s'", f.Name, v)
		}
	}
	if len(f.Choices) > 0 {
		for _, c := range f.Choices {
			if c == v {
				return nil
			}
		}
		return fmt.Errorf("argument --%s: invalid choice: '%s' (choose from %s)", f.Name, v, quoteJoin(f.Choices))
	}
	return nil
}

func quoteJoin(xs []string) string {
	q := make([]string, len(xs))
	for i, x := range xs {
		q[i] = "'" + x + "'"
	}
	return strings.Join(q, ", ")
}

// Parse walks argv against the tree. It returns the leaf command to run.
func Parse(root *Command, argv []string) (*Parsed, error) {
	p := &Parsed{flags: map[string]string{}, set: map[string]bool{}, args: map[string]string{}, many: map[string][]string{}}
	chain := []*Command{root}
	cur := root
	argIdx := 0
	var pending []string // positionals not yet bound
	bind := func() error {
		for _, v := range pending {
			if argIdx >= len(cur.Args) {
				return &usageError{p.Path, "unrecognized arguments: " + v}
			}
			a := cur.Args[argIdx]
			if a.Many {
				p.many[a.Name] = append(p.many[a.Name], v)
				continue
			}
			p.args[a.Name] = v
			argIdx++
		}
		pending = nil
		return nil
	}
	i := 0
	for i < len(argv) {
		tok := argv[i]
		// REMAINDER swallows everything from here, flags included, unless the
		// token is one of this command's own flags (argparse does the same).
		if argIdx < len(cur.Args) && cur.Args[argIdx].Rest && len(pending) == 0 {
			known := tok == "-h" || tok == "--help"
			if !known && strings.HasPrefix(tok, "--") && len(tok) > 2 {
				name, _, _ := strings.Cut(tok[2:], "=")
				_, known = findFlag(chain, name)
			}
			if !known {
				rest := cur.Args[argIdx].Name
				p.many[rest] = append(p.many[rest], argv[i:]...)
				break
			}
		}
		if tok == "-h" || tok == "--help" {
			return p, errHelp
		}
		if strings.HasPrefix(tok, "--") && len(tok) > 2 {
			name, val, hasVal := strings.Cut(tok[2:], "=")
			f, ok := findFlag(chain, name)
			if !ok {
				return p, &usageError{p.Path, "unrecognized arguments: " + tok}
			}
			if f.Kind == FlagBool {
				if hasVal {
					return p, &usageError{p.Path, fmt.Sprintf("argument --%s: ignored explicit argument '%s'", name, val)}
				}
				p.flags[name] = "true"
				p.set[name] = true
				i++
				continue
			}
			if !hasVal {
				if i+1 >= len(argv) {
					return p, &usageError{p.Path, fmt.Sprintf("argument --%s: expected one argument", name)}
				}
				val = argv[i+1]
				i++
			}
			if err := checkValue(f, val); err != nil {
				return p, &usageError{p.Path, err.Error()}
			}
			p.flags[name] = val
			p.set[name] = true
			i++
			continue
		}
		if len(cur.Sub) > 0 && argIdx >= len(cur.Args) {
			var next *Command
			for _, s := range cur.Sub {
				if s.Name == tok {
					next = s
					break
				}
			}
			if next == nil {
				return p, &usageError{p.Path, fmt.Sprintf("argument command: invalid choice: '%s' (choose from %s)", tok, quoteJoin(subNames(cur)))}
			}
			if err := bind(); err != nil {
				return p, err
			}
			if err := finishArgs(p, cur, argIdx); err != nil {
				return p, err
			}
			flagDefaults(p, cur)
			p.Path = append(p.Path, tok)
			chain = append(chain, next)
			cur = next
			argIdx = 0
			i++
			continue
		}
		pending = append(pending, tok)
		if err := bind(); err != nil {
			return p, err
		}
		i++
	}
	if err := bind(); err != nil {
		return p, err
	}
	if len(cur.Sub) > 0 && cur.SubRequired && cur != root {
		return p, &usageError{p.Path, "the following arguments are required: " + cur.Name + "_cmd"}
	}
	if err := finishArgs(p, cur, argIdx); err != nil {
		return p, err
	}
	flagDefaults(p, cur)
	for _, c := range chain {
		flagDefaults(p, c)
	}
	p.cmd = cur
	return p, nil
}

func finishArgs(p *Parsed, c *Command, from int) error {
	var missing []string
	for j := from; j < len(c.Args); j++ {
		a := c.Args[j]
		switch {
		case a.Many || a.Rest:
		case a.Optional:
			if _, ok := p.args[a.Name]; !ok {
				p.args[a.Name] = a.Default
			}
		default:
			if _, ok := p.args[a.Name]; !ok {
				missing = append(missing, a.Name)
			}
		}
	}
	if len(missing) > 0 {
		return &usageError{p.Path, "the following arguments are required: " + strings.Join(missing, ", ")}
	}
	return nil
}

func subNames(c *Command) []string {
	out := make([]string, 0, len(c.Sub))
	for _, s := range c.Sub {
		out = append(out, s.Name)
	}
	return out
}

// lookup returns the command at path.
func lookup(root *Command, path []string) *Command {
	cur := root
	for _, name := range path {
		found := false
		for _, s := range cur.Sub {
			if s.Name == name {
				cur, found = s, true
				break
			}
		}
		if !found {
			break
		}
	}
	return cur
}

func usageLine(root *Command, path []string) string {
	c := lookup(root, path)
	parts := append([]string{"remedy"}, path...)
	if len(c.Flags) > 0 {
		parts = append(parts, "[options]")
	}
	for _, a := range c.Args {
		switch {
		case a.Optional:
			parts = append(parts, "["+a.Name+"]")
		case a.Many:
			parts = append(parts, "["+a.Name+" ...]")
		case a.Rest:
			parts = append(parts, "...")
		default:
			parts = append(parts, a.Name)
		}
	}
	if len(c.Sub) > 0 {
		parts = append(parts, "{"+strings.Join(subNames(c), ",")+"}", "...")
	}
	return "usage: " + strings.Join(parts, " ")
}

// printHelp writes argparse-shaped help for the command at path.
func printHelp(w io.Writer, root *Command, path []string) {
	c := lookup(root, path)
	fmt.Fprintln(w, usageLine(root, path))
	if c.Help != "" {
		fmt.Fprintln(w)
		fmt.Fprintln(w, c.Help)
	}
	if len(c.Args) > 0 {
		fmt.Fprintln(w, "\npositional arguments:")
		for _, a := range c.Args {
			fmt.Fprintf(w, "  %-22s %s\n", a.Name, a.Help)
		}
	}
	if len(c.Sub) > 0 {
		fmt.Fprintln(w, "\ncommands:")
		for _, s := range c.Sub {
			fmt.Fprintf(w, "  %-22s %s\n", s.Name, s.Help)
		}
	}
	fmt.Fprintln(w, "\noptions:")
	fmt.Fprintf(w, "  %-22s %s\n", "-h, --help", "show this help message and exit")
	for _, f := range c.Flags {
		if f.Hidden {
			continue
		}
		name := "--" + f.Name
		if f.Kind != FlagBool {
			name += " " + strings.ToUpper(strings.ReplaceAll(f.Name, "-", "_"))
		}
		help := f.Help
		if len(f.Choices) > 0 {
			help += " {" + strings.Join(f.Choices, ",") + "}"
		}
		fmt.Fprintf(w, "  %-22s %s\n", name, help)
	}
}
