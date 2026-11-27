package cli

import (
	"bufio"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// sysPrompter reads answers from c.In the way rich.prompt does: the answer is
// stripped, an empty answer takes the default, and an invalid one re-asks.
// Prompts go to c.Out; nothing is coloured.
type sysPrompter struct {
	out io.Writer
	in  io.Reader
	r   *bufio.Reader
}

// errSysNoInput is raised (as a panic, caught by the command) when input
// ends mid-wizard: Python crashes with EOFError there and writes nothing,
// so the Go wizard also stops without writing.
var errSysNoInput = errors.New("no input")

func newSysPrompter(c *Ctx) *sysPrompter {
	in := c.In
	if in == nil {
		in = strings.NewReader("")
	}
	return &sysPrompter{out: c.Out, in: in, r: bufio.NewReader(in)}
}

func (p *sysPrompter) line() string {
	s, err := p.r.ReadString('\n')
	if err != nil && s == "" {
		panic(errSysNoInput)
	}
	return strings.TrimRight(s, "\r\n")
}

// Ask is Prompt.ask(prompt, default=def).
func (p *sysPrompter) Ask(prompt, def string) string {
	fmt.Fprintf(p.out, "%s (%s): ", prompt, def)
	v := strings.TrimSpace(p.line())
	if v == "" {
		return def
	}
	return v
}

// Confirm is Confirm.ask: only y or n (any case) is accepted.
func (p *sysPrompter) Confirm(prompt string, def bool) bool {
	d := "n"
	if def {
		d = "y"
	}
	for {
		fmt.Fprintf(p.out, "%s [y/n] (%s): ", prompt, d)
		v := strings.ToLower(strings.TrimSpace(p.line()))
		switch v {
		case "":
			return def
		case "y":
			return true
		case "n":
			return false
		}
		fmt.Fprintln(p.out, "Please enter Y or N")
	}
}

// Int is IntPrompt.ask.
func (p *sysPrompter) Int(prompt string, def int) int {
	for {
		fmt.Fprintf(p.out, "%s (%d): ", prompt, def)
		v := strings.TrimSpace(p.line())
		if v == "" {
			return def
		}
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
		fmt.Fprintln(p.out, "Please enter a valid integer number")
	}
}

// Float is FloatPrompt.ask.
func (p *sysPrompter) Float(prompt string, def float64) float64 {
	for {
		fmt.Fprintf(p.out, "%s (%s): ", prompt, sysPyFloat(def))
		v := strings.TrimSpace(p.line())
		if v == "" {
			return def
		}
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
		fmt.Fprintln(p.out, "Please enter a number")
	}
}

// Secret is getpass.getpass: echo is turned off when the input is a
// terminal; the typed value is never written back.
func (p *sysPrompter) Secret(prompt string) string {
	fmt.Fprint(p.out, prompt)
	if f, ok := p.in.(*os.File); ok && secret.IsTerminal(f.Fd()) && p.r.Buffered() == 0 {
		if restore, hidden := secret.DisableEcho(f); hidden {
			defer func() {
				restore()
				fmt.Fprintln(p.out)
			}()
		}
	}
	return p.line()
}

// sysPyFloat prints a float the way Python's str() does (0.8, 1.0).
func sysPyFloat(f float64) string {
	s := strconv.FormatFloat(f, 'g', -1, 64)
	if !strings.ContainsAny(s, ".eEn") {
		s += ".0"
	}
	return s
}
