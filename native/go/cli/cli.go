package cli

import (
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

// Hooks are the parts of remedy-runtime the CLI reuses rather than copies.
type Hooks struct {
	// Serve runs the production local API on addr until interrupted and
	// returns the exit code. home is the resolved --home.
	Serve func(addr, home string, onBound func(string)) int
}

// Ctx is what a command sees.
type Ctx struct {
	P      *Parsed
	Home   string // resolved, jailed --home (exists)
	Out    io.Writer
	Err    io.Writer
	In     io.Reader
	Hooks  Hooks
	root   *Command
	logOut func(string)
}

// Printf writes to stdout.
func (c *Ctx) Printf(format string, a ...any) { fmt.Fprintf(c.Out, format, a...) }

// Println writes a line to stdout.
func (c *Ctx) Println(a ...any) { fmt.Fprintln(c.Out, a...) }

// Errorf writes to stderr.
func (c *Ctx) Errorf(format string, a ...any) { fmt.Fprintf(c.Err, format, a...) }

// DBPath is the shared memory.db.
func (c *Ctx) DBPath() string { return filepath.Join(c.Home, "memory.db") }

// usage prints the usage line of the current command to stderr.
func (c *Ctx) usage() { fmt.Fprintln(c.Err, usageLine(c.root, c.P.Path)) }

// commandWords are the first-argument words that mean "run the CLI".
func commandWords(root *Command) map[string]bool {
	out := map[string]bool{"help": true, "--version": true, "--home": true, "-h": true, "--help": true}
	for _, s := range root.Sub {
		out[s.Name] = true
	}
	return out
}

// IsCLI reports whether remedy-runtime's argv (without the program name)
// is a CLI invocation rather than the runtime's own --probe/--serve flags.
func IsCLI(args []string) bool {
	if len(args) == 0 {
		return false
	}
	first := args[0]
	if strings.HasPrefix(first, "--home=") {
		return true
	}
	return commandWords(Root())[first]
}

// Version is what `remedy --version` prints. The Python launcher passes the
// pip package version in REMEDY_CLI_VERSION; a bare binary prints its own.
func Version(fallback string) string {
	if v := strings.TrimSpace(os.Getenv("REMEDY_CLI_VERSION")); v != "" {
		return v
	}
	return fallback
}

// Run executes one CLI invocation and returns the process exit code.
func Run(args []string, out, errw io.Writer, in io.Reader, hooks Hooks, version string) int {
	root := Root()
	if len(args) > 0 && args[0] == "help" {
		printHelp(out, root, args[1:])
		return 0
	}
	if len(args) > 0 && args[0] == "--version" {
		fmt.Fprintf(out, "remedy %s\n", Version(version))
		return 0
	}
	p, err := Parse(root, args)
	if errors.Is(err, errHelp) {
		printHelp(out, root, p.Path)
		return 0
	}
	var ue *usageError
	if errors.As(err, &ue) {
		fmt.Fprintln(errw, usageLine(root, ue.path))
		fmt.Fprintf(errw, "remedy: error: %s\n", ue.msg)
		return 2
	}
	if err != nil {
		fmt.Fprintf(errw, "remedy: error: %v\n", err)
		return 2
	}
	if len(p.Path) == 0 {
		printHelp(out, root, nil)
		return 2
	}
	home, herr := ResolveCLIHome(p.Str("home"), true)
	if herr != nil {
		fmt.Fprintln(errw, herr.Error())
		return 2
	}
	ctx := &Ctx{P: p, Home: home, Out: out, Err: errw, In: in, Hooks: hooks, root: root}
	if p.cmd == nil || p.cmd.Run == nil {
		printHelp(out, root, p.Path)
		return 2
	}
	return p.cmd.Run(ctx)
}

// DefaultHome is $REMEDY_HOME or ~/.remedy, made absolute.
func DefaultHome() string {
	env := strings.Trim(strings.TrimSpace(os.Getenv("REMEDY_HOME")), "\"'")
	base := env
	if base == "" {
		uh, err := os.UserHomeDir()
		if err != nil {
			uh = "."
		}
		base = filepath.Join(uh, ".remedy")
	}
	base = expandUser(base)
	if abs, err := filepath.Abs(base); err == nil {
		base = abs
	}
	if real, err := filepath.EvalSymlinks(base); err == nil {
		base = real
	}
	return base
}

func expandUser(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		if uh, err := os.UserHomeDir(); err == nil {
			return filepath.Join(uh, p[1:])
		}
	}
	return p
}

// OS trees that must never become --home (first component after the root).
var blockedHomeRoots = map[string]bool{
	"windows": true, "system32": true, "syswow64": true, "program files": true,
	"program files (x86)": true, "programdata": true, "etc": true, "usr": true,
	"bin": true, "sbin": true, "boot": true, "proc": true, "sys": true, "dev": true,
}

// Windows top-level folders that make a drive path an OS path.
var osProjectRoots = map[string]bool{
	"windows": true, "program files": true, "program files (x86)": true,
	"programdata": true, "$recycle.bin": true, "recovery": true,
	"system volume information": true,
}

// windowsDriveOSKind classifies a Windows drive path without touching the
// disk, so POSIX CI tests it too: "root" for C:\, "os" for C:\Windows\...
func windowsDriveOSKind(raw string) string {
	t := strings.TrimSpace(raw)
	t = strings.TrimPrefix(t, `\\?\`)
	t = strings.ReplaceAll(t, "/", `\`)
	if len(t) < 2 || !isLetter(t[0]) || t[1] != ':' {
		return ""
	}
	rest := strings.TrimLeft(t[2:], `\`)
	if rest == "" {
		return "root"
	}
	first, _, _ := strings.Cut(rest, `\`)
	if osProjectRoots[strings.ToLower(first)] {
		return "os"
	}
	return ""
}

func isLetter(b byte) bool { return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') }

// UnsafeHomeError is a --home that points at a drive root or an OS tree.
type UnsafeHomeError struct{ msg string }

func (e *UnsafeHomeError) Error() string { return e.msg }

// ResolveCLIHome expands and jails --home, creates it, and publishes it in
// REMEDY_HOME so every package that asks for the home agrees with --home.
func ResolveCLIHome(raw string, mkdir bool) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		raw = "~/.remedy"
	}
	if kind := windowsDriveOSKind(raw); kind == "root" || kind == "os" {
		return "", &UnsafeHomeError{"--home refuses system path: " + raw}
	}
	p := expandUser(raw)
	resolved, err := filepath.Abs(p)
	if err != nil {
		resolved = p
	}
	if real, err := filepath.EvalSymlinks(resolved); err == nil {
		resolved = real
	}
	if st, err := os.Stat(resolved); err == nil && !st.IsDir() {
		return "", &UnsafeHomeError{"--home is a file, not a directory: " + resolved}
	}
	if filepath.Dir(resolved) == resolved {
		return "", &UnsafeHomeError{"--home cannot be a drive or filesystem root: " + resolved}
	}
	parts := splitParts(resolved)
	if len(parts) == 0 || blockedHomeRoots[strings.ToLower(parts[0])] {
		return "", &UnsafeHomeError{"--home refuses system path: " + resolved}
	}
	if mkdir {
		if err := os.MkdirAll(resolved, 0o700); err != nil {
			return "", fmt.Errorf("--home cannot be created: %v", err)
		}
	}
	_ = os.Setenv("REMEDY_HOME", resolved)
	return resolved, nil
}

// splitParts returns the path's components after the volume/root.
func splitParts(p string) []string {
	vol := filepath.VolumeName(p)
	rest := strings.Trim(p[len(vol):], `/\`)
	if rest == "" {
		return nil
	}
	return strings.FieldsFunc(rest, func(r rune) bool { return r == '/' || r == '\\' })
}
