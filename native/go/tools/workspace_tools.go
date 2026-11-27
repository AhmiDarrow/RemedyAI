package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode"
	"unicode/utf8"
)

// The workspace.* and text.* tools used to run in the Python tool worker.
// They are served here now, on the same jail as read / edit / write / grep
// (os.Root under the bound root, the auth secrets folder refused), and keep
// their ids, schemas and result shapes: approvals are fingerprinted on the
// tool id, and the CLI and the local tool API still call them. The model
// never sees them (modelHiddenToolIDs).

// goWorkspaceToolIDs are the tools RegisterWorkspaceTools serves.
var goWorkspaceToolIDs = map[string]struct{}{
	"text.slugify": {}, "text.word_count": {},
	"workspace.read": {}, "workspace.list": {}, "workspace.write": {},
	"workspace.edit": {}, "workspace.search": {},
}

const (
	workspaceReadCharCap  = 512_000
	workspaceListDefault  = 200
	workspaceListMax      = 2000
	workspaceSearchDef    = 50
	workspaceSearchMax    = 500
	workspaceSearchMaxCtx = 5
)

// RegisterWorkspaceTools installs workspace.* and text.* as Go executors.
func RegisterWorkspaceTools(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	execs := map[string]ExecutorFunc{
		"text.slugify":     execTextSlugify,
		"text.word_count":  execTextWordCount,
		"workspace.read":   execWorkspaceRead,
		"workspace.list":   execWorkspaceList,
		"workspace.write":  execWorkspaceWrite,
		"workspace.edit":   execWorkspaceEdit,
		"workspace.search": execWorkspaceSearch,
	}
	for _, r := range nativeToolSpecs() {
		exec, ok := execs[r.id]
		if !ok {
			continue
		}
		desc := r.desc
		if err := registry.Register(Descriptor{
			ID:           r.id,
			Version:      1,
			Description:  desc,
			Runtime:      RuntimeGo,
			Risk:         r.risk,
			InputSchema:  json.RawMessage(r.in),
			OutputSchema: json.RawMessage(r.out),
		}, exec); err != nil {
			return err
		}
	}
	return nil
}

func execTextSlugify(_ context.Context, request Request) (Result, error) {
	var body struct {
		Text string `json:"text"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	out, err := json.Marshal(map[string]any{"slug": slugify(body.Text)})
	return Result{Output: out}, err
}

func execTextWordCount(_ context.Context, request Request) (Result, error) {
	var body struct {
		Text string `json:"text"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	out, err := json.Marshal(map[string]any{"words": wordCount(body.Text)})
	return Result{Output: out}, err
}

// workspaceInput is the binding part every workspace.* call carries. The
// runtime overwrites workspace_root / project_path with the session root
// before execution (bindToolInput), so a model never chooses them.
type workspaceInput struct {
	WorkspaceRoot string `json:"workspace_root"`
	ProjectPath   string `json:"project_path"`
}

// openWorkspace opens the jail for one call: the bound root when the runtime
// gave one, otherwise the owner's configured project, otherwise a narrow
// owner folder. A home folder, a filesystem root or a packaged install
// folder is never a workspace.
func openWorkspace(in workspaceInput) (*boundFS, error) {
	root := workspaceRootFor(in)
	if root == "" {
		return nil, fmt.Errorf("%w: no usable workspace folder could be found", ErrInvalidInput)
	}
	if isSecretAuthPath(root) {
		return nil, fmt.Errorf("%w: the workspace root is an auth secret path", ErrInvalidInput)
	}
	return openBoundFS(root, "")
}

// workspaceRootFor picks the jail in the order the worker launcher used
// (workers.resolveDefaultWorkspace), so an unbound call lands where it did
// when Python served it: the bound root, the workspace env vars, the
// configured project, the repo a dev build runs from, the configured
// default workspace, then ~/Remedy or ~/.remedy/workspace.
func workspaceRootFor(in workspaceInput) string {
	for _, raw := range []string{in.WorkspaceRoot, in.ProjectPath} {
		if c := usableWorkspaceRoot(raw); c != "" {
			return c
		}
	}
	for _, key := range []string{"REMEDY_WORKSPACE", "REMEDY_PROJECT_PATH", "REMEDY_PROJECT", "REMEDY_FILES_ROOT"} {
		if c := usableWorkspaceRoot(os.Getenv(key)); c != "" {
			return c
		}
	}
	home := resolveToolHome()
	cfg := ""
	if home != "" {
		cfg = filepath.Join(home, "config.toml")
		if c := usableWorkspaceRoot(configString(cfg, "project_path")); c != "" {
			return c
		}
	}
	if wd, err := os.Getwd(); err == nil {
		for d := wd; d != "" && d != filepath.Dir(d); d = filepath.Dir(d) {
			if _, err := os.Stat(filepath.Join(d, "pyproject.toml")); err == nil {
				if c := usableWorkspaceRoot(d); c != "" {
					return c
				}
				break
			}
		}
	}
	return defaultOwnerWorkspace(cfg)
}

func usableWorkspaceRoot(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" || raw == "." || raw == "./" {
		return ""
	}
	abs, err := filepath.Abs(expandUserHome(raw))
	if err != nil {
		return ""
	}
	if r, err := filepath.EvalSymlinks(abs); err == nil {
		abs = r
	}
	abs = filepath.Clean(abs)
	if filepath.Dir(abs) == abs || isUserHome(abs) || looksLikeInstallDir(abs) {
		return ""
	}
	return abs
}

func expandUserHome(p string) string {
	if p == "~" || strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, p[1:])
		}
	}
	return p
}

func isUserHome(abs string) bool {
	home, err := os.UserHomeDir()
	if err != nil || home == "" {
		return false
	}
	if r, err := filepath.EvalSymlinks(home); err == nil {
		home = r
	}
	return pathWithin(abs, filepath.Clean(home)) && pathWithin(filepath.Clean(home), abs)
}

// looksLikeInstallDir spots the packaged desktop folder, which is the
// working directory a packaged app often starts in.
func looksLikeInstallDir(abs string) bool {
	for _, name := range []string{"Remedy Desktop.exe", "remedy-runtime.exe", "remedy-runtime", "uninstall.exe"} {
		if _, err := os.Stat(filepath.Join(abs, name)); err == nil {
			return true
		}
	}
	a, errA := os.Stat(filepath.Join(abs, "webui"))
	b, errB := os.Stat(filepath.Join(abs, "windows"))
	return errA == nil && errB == nil && a.IsDir() && b.IsDir()
}

// configString reads one top-level string key from a config.toml without a
// TOML dependency: one key, one line, the way the writers emit it.
func configString(cfg, want string) string {
	data, err := os.ReadFile(cfg)
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(data), "\n") {
		s := strings.TrimSpace(line)
		if s == "" || strings.HasPrefix(s, "#") {
			continue
		}
		key, val, ok := strings.Cut(s, "=")
		if !ok || strings.ToLower(strings.TrimSpace(key)) != want {
			continue
		}
		val = strings.Trim(strings.TrimSpace(val), `"'`)
		// Writers escape Windows paths as C:\\Users\\… in TOML strings.
		return strings.ReplaceAll(strings.TrimSpace(val), `\\`, `\`)
	}
	return ""
}

func defaultOwnerWorkspace(cfg string) string {
	candidates := []string{}
	if cfg != "" {
		if c := usableWorkspaceRoot(configString(cfg, "default_workspace")); c != "" {
			candidates = append(candidates, c)
		}
	}
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		candidates = append(candidates, filepath.Join(home, "Remedy"), filepath.Join(home, ".remedy", "workspace"))
	}
	for _, c := range candidates {
		if err := os.MkdirAll(c, 0o755); err == nil {
			if r, err := filepath.EvalSymlinks(c); err == nil {
				return r
			}
			return c
		}
	}
	return ""
}

// relShown is a result path relative to the workspace root, slash-separated.
func relShown(bound *boundFS, loc located) string {
	if loc.usesHost() {
		return loc.shown
	}
	return filepath.ToSlash(loc.rel)
}

// workspaceSkipDirs are never listed.
var workspaceSkipDirs = map[string]struct{}{
	".git": {}, "__pycache__": {}, "node_modules": {}, ".venv": {}, "venv": {}, ".tox": {},
	".mypy_cache": {}, ".pytest_cache": {}, ".ruff_cache": {}, "zig-cache": {}, "zig-out": {},
}

var credentialNames = map[string]struct{}{
	".env": {}, ".npmrc": {}, ".pypirc": {}, ".netrc": {}, "_netrc": {}, ".git-credentials": {},
	".aws": {}, ".ssh": {}, ".gnupg": {}, ".docker": {}, ".kube": {},
	"credentials.json": {}, "service-account.json": {},
	"local_api_token": {}, "local_api_token.posix": {}, "provider_keys.json": {}, "xai.json": {}, "oauth_tokens.json": {},
}

// isCredentialName reports a well-known credential file or folder by name.
// Templates such as .env.example are not credentials.
func isCredentialName(name string) bool {
	n := strings.ToLower(strings.TrimSpace(name))
	if n == "" {
		return false
	}
	if _, ok := credentialNames[n]; ok {
		return true
	}
	if strings.HasPrefix(n, ".env.") {
		for _, ex := range []string{".example", ".sample", ".template", ".dist"} {
			if strings.HasSuffix(n, ex) {
				return false
			}
		}
		return true
	}
	for _, p := range []string{"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"} {
		if strings.HasPrefix(n, p) {
			return true
		}
	}
	for _, s := range []string{".pem", ".key", ".p12", ".pfx", ".ppk"} {
		if strings.HasSuffix(n, s) {
			return true
		}
	}
	return false
}

func pathHasCredentialPart(p string) bool {
	for _, part := range strings.FieldsFunc(p, func(r rune) bool { return r == '/' || r == '\\' }) {
		if isCredentialName(part) {
			return true
		}
	}
	return false
}

// isJunkListingName hides corrupt or private-use names from listings.
func isJunkListingName(name string) bool {
	if name == "" || name == "." || name == ".." {
		return true
	}
	for _, r := range name {
		if r < 32 || r == 127 || (r >= 0xE000 && r <= 0xF8FF) {
			return true
		}
	}
	return false
}

var windowsReservedRE = regexp.MustCompile(`(?i)^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)`)

// windowsReservedSegment returns the device name when any part of p is a
// Windows reserved device name. Checked on every OS so a Windows tree worked
// from Linux still avoids nul / con.
func windowsReservedSegment(p string) string {
	for _, part := range strings.FieldsFunc(p, func(r rune) bool { return r == '/' || r == '\\' }) {
		if len(part) == 2 && part[1] == ':' {
			continue
		}
		base := strings.TrimRight(strings.TrimSpace(part), ". ")
		if base == "" || base == "." || base == ".." {
			continue
		}
		if m := windowsReservedRE.FindStringSubmatch(base); m != nil {
			return strings.ToUpper(m[1])
		}
	}
	return ""
}

var junkWriteRE = regexp.MustCompile(`(?i)(?:^|/)_ref_[^/]+$|(?:^|/)_ex_[a-z0-9]+(?:\.[^/]+)?$|(?:^|/)_write_[^/]+\.py$|(?:^|/)_patch_[^/]+\.py$|(?:^|/)_vault_tail\.txt$`)

// writeGuard refuses the paths an agent should never write: Windows device
// names, scaffold dumps and shell brace expansions.
func writeGuard(p string) error {
	if dev := windowsReservedSegment(p); dev != "" {
		return fmt.Errorf("%w: path uses Windows reserved device name %q — skip this path; do not open, write, or shell into it", ErrInvalidInput, dev)
	}
	norm := strings.ReplaceAll(strings.TrimSpace(p), `\`, "/")
	if norm == "" {
		return nil
	}
	if junkWriteRE.MatchString(norm) {
		return fmt.Errorf("%w: refusing junk scaffold path %q: do not write _ref_*, _ex_*, _write_*.py, or _patch_*.py into the project", ErrInvalidInput, p)
	}
	if strings.ContainsAny(norm, "{}") {
		return fmt.Errorf("%w: refusing brace/glob path %q: write one concrete path, not shell brace expansion", ErrInvalidInput, p)
	}
	return nil
}

// historyStubMarkers are provider-history placeholders that must never be
// written back to disk as file content.
var historyStubMarkers = []string{
	"[file_write content omitted", "omitted from provider history", "_history_summarized",
	"<<NOT_SOURCE_CODE", "DO_NOT_file_write_this_string", "history_stub kind=", "history_stub_only",
	"body omitted from chat history", "Do NOT re-file_write a history stub", "file body omitted from chat history",
}

func looksLikeHistoryStub(s string) bool {
	if strings.TrimSpace(s) == "" {
		return false
	}
	for _, m := range historyStubMarkers {
		if strings.Contains(s, m) {
			return true
		}
	}
	return false
}

// locateWorkspaceFile resolves p inside the workspace and refuses
// credential-looking names anywhere in it.
func locateWorkspaceFile(bound *boundFS, p, verb string) (located, error) {
	loc, err := bound.locate(p)
	if err != nil {
		return located{}, err
	}
	if pathHasCredentialPart(loc.rel) || pathHasCredentialPart(p) {
		return located{}, fmt.Errorf("%w: credential-looking files are not %s", ErrInvalidInput, verb)
	}
	return loc, nil
}

func execWorkspaceRead(_ context.Context, request Request) (Result, error) {
	var body struct {
		workspaceInput
		Path        string `json:"path"`
		Offset      int    `json:"offset"`
		Limit       *int   `json:"limit"`
		LineNumbers bool   `json:"line_numbers"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if strings.TrimSpace(body.Path) == "" {
		return Result{}, fmt.Errorf("%w: path is required", ErrInvalidInput)
	}
	bound, err := openWorkspace(body.workspaceInput)
	if err != nil {
		return Result{}, err
	}
	defer bound.Close()
	loc, err := locateWorkspaceFile(bound, body.Path, "readable")
	if err != nil {
		return Result{}, err
	}
	info, err := bound.Stat(loc)
	if err != nil {
		return Result{}, statError("workspace.read", body.Path, bound.rootAbs, err)
	}
	if info.IsDir() {
		return Result{}, fmt.Errorf("%w: path is a directory: %s", ErrInvalidInput, body.Path)
	}
	if info.Size() > fsMaxFileBytes {
		return Result{}, fmt.Errorf("%w: %q is %d bytes, over the %d-byte read limit", ErrInvalidInput, body.Path, info.Size(), fsMaxFileBytes)
	}
	data, err := bound.ReadFile(loc)
	if err != nil {
		return Result{}, statError("workspace.read", body.Path, bound.rootAbs, err)
	}
	if bytes.IndexByte(data[:min(len(data), 8192)], 0) >= 0 {
		return Result{}, fmt.Errorf("%w: binary or non-text file: %s", ErrInvalidInput, body.Path)
	}
	text := strings.ToValidUTF8(string(data), "\uFFFD")

	offset := max(1, body.Offset)
	lines := splitKeepEnds(text)
	total := len(lines)
	start := min(offset-1, total)
	end := total
	if body.Limit != nil {
		end = min(total, start+max(1, *body.Limit))
	}
	window := lines[start:end]
	more := end < total

	var b strings.Builder
	if body.LineNumbers {
		width := len(fmt.Sprint(max(end, 1)))
		for i, line := range window {
			fmt.Fprintf(&b, "%*d\t%s", width, start+i+1, line)
		}
	} else {
		for _, line := range window {
			b.WriteString(line)
		}
	}
	content := b.String()
	truncated := more
	if utf8.RuneCountInString(content) > workspaceReadCharCap {
		content = string([]rune(content)[:workspaceReadCharCap])
		truncated = true
		// Characters bounded the window: resume after the last whole line.
		end = start + strings.Count(content, "\n")
		more = end < total
	}
	out := map[string]any{
		"path":        relShown(bound, loc),
		"content":     content,
		"total_lines": total,
		"line_start":  min(start+1, max(total, 1)),
		"line_end":    end,
	}
	if truncated {
		out["truncated"] = true
	}
	if more {
		out["next_offset"] = end + 1
	}
	raw, err := json.Marshal(out)
	return Result{Output: raw}, err
}

// splitKeepEnds splits like Python's str.splitlines(keepends=True) for the
// endings files use (\n, \r\n, \r).
func splitKeepEnds(s string) []string {
	var out []string
	for len(s) > 0 {
		i := strings.IndexAny(s, "\r\n")
		if i < 0 {
			out = append(out, s)
			break
		}
		j := i + 1
		if s[i] == '\r' && j < len(s) && s[j] == '\n' {
			j++
		}
		out = append(out, s[:j])
		s = s[j:]
	}
	return out
}

func execWorkspaceList(_ context.Context, request Request) (Result, error) {
	var body struct {
		workspaceInput
		Path   string `json:"path"`
		Limit  int    `json:"limit"`
		Offset int    `json:"offset"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	p := strings.TrimSpace(body.Path)
	if p == "" {
		p = "."
	}
	limit := body.Limit
	if limit <= 0 {
		limit = workspaceListDefault
	}
	limit = min(limit, workspaceListMax)
	offset := max(0, body.Offset)

	bound, err := openWorkspace(body.workspaceInput)
	if err != nil {
		return Result{}, err
	}
	defer bound.Close()
	loc, err := bound.locate(p)
	if err != nil {
		return Result{}, err
	}
	info, err := bound.Stat(loc)
	if err != nil {
		return Result{}, statError("workspace.list", p, bound.rootAbs, err)
	}
	if !info.IsDir() {
		return Result{}, fmt.Errorf("%w: not a directory: %s", ErrInvalidInput, p)
	}
	var entries []fs.DirEntry
	if loc.usesHost() {
		entries, err = os.ReadDir(loc.hostAbs)
	} else {
		entries, err = fs.ReadDir(bound.root.FS(), filepath.ToSlash(loc.rel))
	}
	if err != nil {
		return Result{}, statError("workspace.list", p, bound.rootAbs, err)
	}
	type item struct {
		name string
		dir  bool
	}
	visible := make([]item, 0, len(entries))
	for _, e := range entries {
		name := e.Name()
		if _, skip := workspaceSkipDirs[name]; skip || isCredentialName(name) || isJunkListingName(name) {
			continue
		}
		dir := e.IsDir()
		if e.Type()&fs.ModeSymlink != 0 {
			if st, err := fs.Stat(bound.root.FS(), path.Join(filepath.ToSlash(loc.rel), name)); err == nil {
				dir = st.IsDir()
			}
		}
		visible = append(visible, item{name: name, dir: dir})
	}
	sort.SliceStable(visible, func(i, j int) bool {
		if visible[i].dir != visible[j].dir {
			return visible[i].dir
		}
		return strings.ToLower(visible[i].name) < strings.ToLower(visible[j].name)
	})
	page := visible[min(offset, len(visible)):min(offset+limit, len(visible))]
	items := make([]map[string]string, 0, len(page))
	for _, it := range page {
		name := it.name
		if !loc.usesHost() {
			name = path.Join(filepath.ToSlash(loc.rel), it.name)
		}
		kind := "file"
		if it.dir {
			kind = "dir"
		}
		items = append(items, map[string]string{"name": name, "kind": kind})
	}
	out := map[string]any{"path": p, "entries": items, "total": len(visible)}
	if offset+len(items) < len(visible) {
		out["truncated"] = true
	}
	raw, err := json.Marshal(out)
	return Result{Output: raw}, err
}

func execWorkspaceWrite(_ context.Context, request Request) (Result, error) {
	var body struct {
		workspaceInput
		Path    string           `json:"path"`
		Content *json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if strings.TrimSpace(body.Path) == "" {
		return Result{}, fmt.Errorf("%w: path is required", ErrInvalidInput)
	}
	if body.Content == nil || string(*body.Content) == "null" {
		return Result{}, fmt.Errorf("%w: content is required", ErrInvalidInput)
	}
	var content string
	if err := json.Unmarshal(*body.Content, &content); err != nil {
		// An object or list is written as its JSON text.
		content = string(*body.Content)
	}
	if err := writeGuard(body.Path); err != nil {
		return Result{}, err
	}
	if looksLikeHistoryStub(content) {
		return Result{}, fmt.Errorf("%w: refusing to write provider-history summary stub as file content", ErrInvalidInput)
	}
	bound, err := openWorkspace(body.workspaceInput)
	if err != nil {
		return Result{}, err
	}
	defer bound.Close()
	loc, err := locateWorkspaceFile(bound, body.Path, "writable")
	if err != nil {
		return Result{}, err
	}
	if !loc.usesHost() && loc.rel == "." {
		return Result{}, fmt.Errorf("%w: path names the workspace root, not a file", ErrInvalidInput)
	}
	created := true
	if info, err := bound.Stat(loc); err == nil {
		if info.IsDir() {
			return Result{}, fmt.Errorf("%w: path is a directory: %s", ErrInvalidInput, body.Path)
		}
		created = false
	}
	if err := bound.MkdirAll(loc, "", 0o755); err != nil {
		return Result{}, fmt.Errorf("%w: could not create parent of %q: %v", ErrInvalidInput, body.Path, err)
	}
	data := []byte(content)
	if err := bound.atomicWrite(loc, data); err != nil {
		return Result{}, err
	}
	raw, err := json.Marshal(map[string]any{
		"path":          relShown(bound, loc),
		"bytes_written": len(data),
		"created":       created,
	})
	return Result{Output: raw}, err
}

type editHunk struct {
	OldString  *string `json:"old_string"`
	NewString  *string `json:"new_string"`
	ReplaceAll bool    `json:"replace_all"`
}

func execWorkspaceEdit(_ context.Context, request Request) (Result, error) {
	var body struct {
		workspaceInput
		Path  string      `json:"path"`
		Edits *[]editHunk `json:"edits"`
		editHunk
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if strings.TrimSpace(body.Path) == "" {
		return Result{}, fmt.Errorf("%w: path is required", ErrInvalidInput)
	}
	if dev := windowsReservedSegment(body.Path); dev != "" {
		return Result{}, fmt.Errorf("%w: path uses Windows reserved device name %q", ErrInvalidInput, dev)
	}
	var hunks []editHunk
	if body.Edits != nil {
		if len(*body.Edits) == 0 {
			return Result{}, fmt.Errorf("%w: edits must be a non-empty list of {old_string,new_string}", ErrInvalidInput)
		}
		hunks = *body.Edits
	} else {
		if body.OldString == nil {
			return Result{}, fmt.Errorf("%w: old_string is required (or pass edits=[...])", ErrInvalidInput)
		}
		if body.NewString == nil {
			return Result{}, fmt.Errorf("%w: new_string is required", ErrInvalidInput)
		}
		hunks = []editHunk{body.editHunk}
	}
	bound, err := openWorkspace(body.workspaceInput)
	if err != nil {
		return Result{}, err
	}
	defer bound.Close()
	loc, err := locateWorkspaceFile(bound, body.Path, "editable")
	if err != nil {
		return Result{}, err
	}
	info, err := bound.Stat(loc)
	if err != nil || info.IsDir() {
		return Result{}, fmt.Errorf("%w: file not found: %s", ErrInvalidInput, body.Path)
	}
	data, err := bound.ReadFile(loc)
	if err != nil {
		return Result{}, statError("workspace.edit", body.Path, bound.rootAbs, err)
	}
	// Byte-exact round trip: keep the BOM and every line ending as found.
	bom := bytes.HasPrefix(data, utf8BOM)
	payload := data
	if bom {
		payload = data[len(utf8BOM):]
	}
	if !utf8.Valid(payload) {
		return Result{}, fmt.Errorf("%w: file is not UTF-8 text: %s", ErrInvalidInput, body.Path)
	}
	original := string(payload)
	crlf := strings.Contains(original, "\r\n")
	text := original
	occurrences := 0
	for i, h := range hunks {
		if h.OldString == nil || h.NewString == nil {
			return Result{}, fmt.Errorf("%w: hunk %d needs old_string and new_string", ErrInvalidInput, i+1)
		}
		next, n, err := applyHunk(text, *h.OldString, *h.NewString, h.ReplaceAll, crlf)
		if err != nil {
			if len(hunks) > 1 {
				return Result{}, fmt.Errorf("%w: hunk %d: %v; the file was not changed", ErrInvalidInput, i+1, err)
			}
			return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
		}
		text = next
		occurrences += n
	}
	changed := text != original
	if changed {
		out := []byte(text)
		if bom {
			out = append(append([]byte(nil), utf8BOM...), out...)
		}
		if err := bound.atomicWrite(loc, out); err != nil {
			return Result{}, err
		}
	}
	msg := fmt.Sprintf("replaced %d occurrence(s)", occurrences)
	if len(hunks) > 1 {
		msg = fmt.Sprintf("applied %d hunks (%d occurrence(s))", len(hunks), occurrences)
	}
	raw, err := json.Marshal(map[string]any{
		"path":          relShown(bound, loc),
		"occurrences":   occurrences,
		"hunks_applied": len(hunks),
		"message":       msg,
		"changed":       changed,
	})
	return Result{Output: raw}, err
}

// applyHunk replaces old with new in text. old must match once unless
// replaceAll; a match that only differs by whitespace at line ends counts
// when it is unique. In a CRLF file both strings move to CRLF first.
func applyHunk(text, old, repl string, replaceAll, crlf bool) (string, int, error) {
	if old == "" {
		return "", 0, errors.New("old_string is empty — use workspace.write to replace a whole file")
	}
	if crlf {
		old, repl = toCRLF(old), toCRLF(repl)
	}
	count := strings.Count(text, old)
	if count == 0 {
		if start, end, n := matchIgnoringLineEnds(text, old); n == 1 {
			return text[:start] + repl + text[end:], 1, nil
		}
		return "", 0, fmt.Errorf("old_string was not found (0 matches). Copy the exact text from the file, including indentation and line breaks.%s", closestTextHint(text, old))
	}
	if count > 1 && !replaceAll {
		return "", 0, fmt.Errorf("old_string matches %d times. Add surrounding lines until it is unique, or set replace_all:true to change all %d", count, count)
	}
	if replaceAll {
		return strings.ReplaceAll(text, old, repl), count, nil
	}
	return strings.Replace(text, old, repl, 1), 1, nil
}

func execWorkspaceSearch(ctx context.Context, request Request) (Result, error) {
	var body struct {
		workspaceInput
		Pattern         string `json:"pattern"`
		Path            string `json:"path"`
		Glob            string `json:"glob"`
		MaxMatches      *int   `json:"max_matches"`
		CaseInsensitive bool   `json:"case_insensitive"`
		Context         int    `json:"context"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	pattern := strings.TrimSpace(body.Pattern)
	if pattern == "" {
		return Result{}, fmt.Errorf("%w: pattern is required", ErrInvalidInput)
	}
	maxMatches := workspaceSearchDef
	if body.MaxMatches != nil {
		maxMatches = *body.MaxMatches
	}
	maxMatches = max(1, min(workspaceSearchMax, maxMatches))
	around := max(0, min(workspaceSearchMaxCtx, body.Context))

	root := workspaceRootFor(body.workspaceInput)
	if root == "" {
		return Result{}, fmt.Errorf("%w: no usable workspace folder could be found", ErrInvalidInput)
	}
	// Ask grep for one more than the cap so a full page reads as capped.
	in, err := json.Marshal(map[string]any{
		"pattern":          pattern,
		"path":             body.Path,
		"glob":             body.Glob,
		"context":          around,
		"max_results":      maxMatches + 1,
		"case_insensitive": body.CaseInsensitive,
		"workspace_root":   root,
	})
	if err != nil {
		return Result{}, err
	}
	res, err := executeGrep(ctx, Request{ToolID: "grep", Version: 1, Input: in})
	if err != nil {
		if strings.Contains(err.Error(), "not a valid regular expression") {
			return Result{}, fmt.Errorf("%w: invalid regex: %v", ErrInvalidInput, err)
		}
		return Result{}, err
	}
	var g struct {
		Matches []struct {
			Path   string   `json:"path"`
			Line   int      `json:"line"`
			Text   string   `json:"text"`
			Before []string `json:"before"`
			After  []string `json:"after"`
		} `json:"matches"`
		Truncated bool `json:"truncated"`
	}
	if err := json.Unmarshal(res.Output, &g); err != nil {
		return Result{}, err
	}
	capped := len(g.Matches) > maxMatches
	if capped {
		g.Matches = g.Matches[:maxMatches]
	}
	matches := make([]map[string]any, 0, len(g.Matches))
	rootSlash := filepath.ToSlash(root)
	for _, m := range g.Matches {
		rel := strings.TrimPrefix(strings.TrimPrefix(m.Path, rootSlash), "/")
		if pathHasCredentialPart(rel) {
			continue
		}
		text := m.Text
		if around > 0 {
			text = strings.Join(append(append(append([]string{}, m.Before...), m.Text), m.After...), "\n")
		}
		matches = append(matches, map[string]any{"path": rel, "line": m.Line, "text": text})
	}
	raw, err := json.Marshal(map[string]any{
		"pattern":   pattern,
		"engine":    "go",
		"matches":   matches,
		"total":     len(matches),
		"capped":    capped,
		"truncated": capped || g.Truncated,
	})
	return Result{Output: raw}, err
}

func slugify(text string) string {
	var b strings.Builder
	lastHyphen := true
	for _, r := range strings.ToLower(text) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			lastHyphen = false
			continue
		}
		if !lastHyphen {
			b.WriteByte('-')
			lastHyphen = true
		}
	}
	return strings.Trim(b.String(), "-")
}

func wordCount(text string) int {
	fields := strings.Fields(text)
	return len(fields)
}
