package prompt

import (
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/sysres"
)

// Project folder, access scope and the workspace block (core/workspace.py,
// BasicRuntime.effective_project_path / access_scope / allowed_roots).

// resolvePath is Path(p).expanduser().resolve() (symlinks followed when the
// path exists).
func resolvePath(p, userHome string) string {
	if p == "~" || strings.HasPrefix(p, "~/") || strings.HasPrefix(p, `~\`) {
		p = filepath.Join(userHome, p[1:])
	}
	abs, err := filepath.Abs(p)
	if err != nil {
		return filepath.Clean(p)
	}
	if real, err := filepath.EvalSymlinks(abs); err == nil {
		return real
	}
	return abs
}

func samePath(a, b string) bool {
	return filepath.Clean(a) == filepath.Clean(b)
}

// isVolumeRootPath is workspace.is_volume_root_path.
func isVolumeRootPath(raw, userHome string) bool {
	text := pyStrip(raw)
	if text == "" {
		return false
	}
	if text == "/" || text == `\` {
		return true
	}
	if cpLen(text) <= 3 && len(text) >= 2 && isASCIILetter(text[0]) && text[1] == ':' {
		if strings.Trim(text[2:], `\/`) == "" {
			return true
		}
	}
	p := resolvePath(text, userHome)
	return filepath.Dir(p) == p
}

func isASCIILetter(b byte) bool { return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') }

// isUnsetProjectPath is workspace.is_unset_project_path.
func isUnsetProjectPath(raw, userHome string) bool {
	text := pyStrip(raw)
	if text == "" || text == "." || text == "./" || isVolumeRootPath(text, userHome) {
		return true
	}
	return samePath(resolvePath(text, userHome), resolvePath(userHome, userHome))
}

// defaultOwnerProjectPath is workspace.default_owner_project_path(create=True).
func defaultOwnerProjectPath(userHome string) string {
	uh := resolvePath(userHome, userHome)
	target := filepath.Join(uh, "Remedy")
	if err := os.MkdirAll(target, 0o755); err == nil {
		return resolvePath(target, userHome)
	}
	fallback := filepath.Join(uh, ".remedy", "workspace")
	if err := os.MkdirAll(fallback, 0o755); err == nil {
		return resolvePath(fallback, userHome)
	}
	return resolvePath(target, userHome)
}

// resolveProjectPath is workspace.resolve_project_path(raw).
func resolveProjectPath(raw, userHome string) string {
	if isUnsetProjectPath(raw, userHome) {
		return defaultOwnerProjectPath(userHome)
	}
	p := resolvePath(pyStrip(raw), userHome)
	if isUnsetProjectPath(p, userHome) {
		return defaultOwnerProjectPath(userHome)
	}
	return p
}

// ensureProjectDir is workspace.ensure_project_dir. A path that exists but
// is not a folder is an error, as in Python (SecurityError).
func ensureProjectDir(p, userHome string) (string, error) {
	p = resolvePath(p, userHome)
	st, err := os.Stat(p)
	if os.IsNotExist(err) {
		if err := os.MkdirAll(p, 0o755); err != nil {
			return "", err
		}
		return p, nil
	}
	if err != nil {
		return "", err
	}
	if !st.IsDir() {
		return "", &os.PathError{Op: "project", Path: p, Err: os.ErrInvalid}
	}
	return p, nil
}

// normalizeAccessScope is workspace.normalize_access_scope.
func normalizeAccessScope(raw string) string {
	s := strings.ToLower(pyStrip(raw))
	if s == "" {
		s = "project"
	}
	switch s {
	case "home", "user", "project+home", "project_home":
		return "home"
	case "full", "machine", "all", "unrestricted":
		return "full"
	case "untrusted", "sandbox", "strict", "download":
		return "untrusted"
	case "workspace", "default", "default_workspace", "owner_workspace":
		return "workspace"
	}
	return "project"
}

// project is the turn's workspace binding.
type project struct {
	Raw      string // the owner's project path ("" = unset)
	Active   string // resolved focus folder
	Unset    bool
	Scope    string // effective access scope
	ReadRoot []string
}

// resolveProject follows BasicRuntime.__init__ and prompt_assemble: the
// request's project_path, else config project_path, else ~/Remedy.
func resolveProject(env Env, requested string) (project, error) {
	raw := requested
	if raw == "" {
		raw = cfgStr(env.Config, "project_path")
	}
	active, err := ensureProjectDir(resolveProjectPath(raw, env.UserHome), env.UserHome)
	if err != nil {
		return project{}, err
	}
	unset := isUnsetProjectPath(raw, env.UserHome)
	configured := normalizeAccessScope(cfgStr(env.Config, "access_scope"))
	scope := configured
	if unset {
		scope = "workspace"
		if configured == "full" {
			scope = "full"
		}
	}
	return project{
		Raw: raw, Active: active, Unset: unset, Scope: scope,
		ReadRoot: allowedRoots(scope, active, env),
	}, nil
}

// allowedRoots is workspace.allowed_roots_for_scope.
func allowedRoots(scope, projectRoot string, env Env) []string {
	roots := []string{projectRoot}
	add := func(p string) {
		for _, r := range roots {
			if samePath(r, p) {
				return
			}
		}
		roots = append(roots, p)
	}
	scope = normalizeAccessScope(scope)
	uh := resolvePath(env.UserHome, env.UserHome)
	if scope != "untrusted" && scope != "workspace" {
		for _, p := range sysres.UserFolders(uh) {
			add(resolvePath(p, env.UserHome))
		}
	}
	if scope == "home" || scope == "full" {
		add(uh)
	}
	for _, hr := range env.HelpRoots {
		add(hr)
	}
	return roots
}

var skipDirNames = map[string]bool{
	".git": true, ".hg": true, ".svn": true, "__pycache__": true, "node_modules": true,
	".venv": true, "venv": true, "dist": true, "build": true, ".idea": true, ".vs": true, "target": true,
}

// listWorkspaceEntries is workspace.list_workspace_entries (dirs first,
// then by lower-cased name; dot-names and build dirs hidden; 40 max).
func listWorkspaceEntries(root string) []string {
	ents, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	type ent struct {
		name string
		dir  bool
	}
	all := make([]ent, 0, len(ents))
	for _, e := range ents {
		dir := e.IsDir()
		if e.Type()&os.ModeSymlink != 0 {
			if st, err := os.Stat(filepath.Join(root, e.Name())); err == nil {
				dir = st.IsDir()
			}
		}
		all = append(all, ent{e.Name(), dir})
	}
	sort.SliceStable(all, func(i, j int) bool {
		if all[i].dir != all[j].dir {
			return all[i].dir
		}
		return strings.ToLower(all[i].name) < strings.ToLower(all[j].name)
	})
	var out []string
	for _, e := range all {
		if strings.HasPrefix(e.name, ".") || skipDirNames[e.name] {
			continue
		}
		if e.dir {
			out = append(out, e.name+"/")
		} else {
			out = append(out, e.name)
		}
		if len(out) >= 40 {
			break
		}
	}
	return out
}

// workspaceBlock is workspace.workspace_context_block without the two
// remedy_core host lines (stretch census, host dialect), which are reported
// missing by the caller.
func workspaceBlock(p project) string {
	scope := normalizeAccessScope(p.Scope)
	if p.Unset {
		scope = "full"
	}
	lines := []string{
		"Default working directory (focus): " + p.Active,
		"Access scope: " + scope,
	}
	switch {
	case p.Unset:
		lines = append(lines, "No focus folder is set — default cwd is ~/Remedy "+
			"(or ~/.remedy/workspace); access uses the default workspace unless you chose Full. "+
			"Use absolute paths for any tree you work on; relative paths resolve "+
			"from the default cwd. A focus folder is optional convenience, not required.")
	case scope == "project":
		lines = append(lines, "Relative paths resolve under the focus folder. **Reads are never "+
			"jailed** — read/list/search any path on the machine (Downloads, "+
			"Documents, other trees) and copy files *into* the project from "+
			"anywhere; but **file_write / file_edit and mutating shell commands "+
			"stay inside the focus folder only**. Only the owner raises access "+
			"scope in Settings (home/full) when multi-tree edits are intended.")
	case scope == "home":
		lines = append(lines, "Focus folder plus user home profile are allowed for edits. "+
			"Absolute paths are fine; relative paths resolve from the focus folder.")
	case scope == "untrusted":
		lines = append(lines, "Untrusted scope: focus folder only for reads and writes; "+
			"high-impact tools stay on Ask.")
	default:
		lines = append(lines, "Access scope is **full**: reads and writes across the user machine "+
			"(no silent admin elevation). Prefer the focus folder for this "+
			"project's edits. Never writable: Remedy's auth secrets and "+
			"Remedy's own installed code (frozen sidecar / ~/.remedy/voice/runtime).")
	}
	if len(p.ReadRoot) > 0 {
		roots := p.ReadRoot
		if len(roots) > 6 {
			roots = roots[:6]
		}
		lines = append(lines, "Read roots: "+strings.Join(roots, ", "))
	}
	if p.Unset {
		lines = append(lines, "Coding without a focus folder is first-class — list_dir / repo_search / "+
			"file_* with absolute paths anywhere in scope.")
	} else {
		lines = append(lines, "Prefer relative paths under the focus folder for edits. Absolute paths "+
			"are OK for reading other trees in read scope; do not write outside "+
			"the project unless access scope is home/full.")
	}
	if entries := listWorkspaceEntries(p.Active); len(entries) > 0 {
		lines = append(lines, "Top-level: "+strings.Join(entries, ", "))
	} else {
		lines = append(lines, "Top-level: (empty or unreadable)")
	}
	return strings.Join(lines, "\n")
}

// isolationLine is session_continuity.session_isolation_system_line.
func isolationLine(sessionID, projectPath string) string {
	if sessionID == "" && projectPath == "" {
		return ""
	}
	parts := []string{
		"[Session isolation — hard rule]",
		"Use ONLY this chat session's history, Session Brief, Partner State, and project.",
		"Do NOT continue work, paths, or assumptions from other chat tabs/sessions.",
	}
	if sessionID != "" {
		parts = append(parts, "session_id="+sessionID)
	}
	if projectPath != "" {
		parts = append(parts, "project="+projectPath)
	}
	parts = append(parts, "If a path is outside the project jail, fix scope or switch project — "+
		"do not invent success from another repo.")
	return strings.Join(parts, "\n")
}
