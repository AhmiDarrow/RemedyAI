package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
)

type fileEntry struct {
	Name  string `json:"name"`
	Path  string `json:"path"`
	IsDir bool   `json:"is_dir"`
}

type workspaceEntry struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

func (s *Server) userHomeForWorkspace() string {
	cfg := LoadConfig(s.homeDir)
	if h := strings.TrimSpace(cfgString(cfg, "home_dir", "")); h != "" {
		if abs, err := resolveAbsPath(h); err == nil {
			return abs
		}
		return h
	}
	if home := ResolveHomeDir(s.homeDir); home != "" {
		// ResolveHomeDir is ~/.remedy — user profile is its parent when default.
		if abs, err := resolveAbsPath(home); err == nil {
			base := filepath.Base(abs)
			if strings.EqualFold(base, ".remedy") {
				return filepath.Dir(abs)
			}
			// Explicit REMEDY_HOME / portable home: use OS user home for Desktop etc.
		}
	}
	if uh, err := os.UserHomeDir(); err == nil {
		return uh
	}
	return ""
}

func (s *Server) remedyHomeDir() string {
	cfg := LoadConfig(s.homeDir)
	if h := strings.TrimSpace(cfgString(cfg, "home_dir", "")); h != "" {
		if abs, err := resolveAbsPath(h); err == nil {
			return abs
		}
		return h
	}
	return ResolveHomeDir(s.homeDir)
}

func (s *Server) configProjectRaw() string {
	cfg := LoadConfig(s.homeDir)
	return strings.TrimSpace(cfgString(cfg, "project_path", ""))
}

func (s *Server) configAccessScope() string {
	cfg := LoadConfig(s.homeDir)
	return cfgString(cfg, "access_scope", "project")
}

func (s *Server) sessionProjectPath(sessionID string) string {
	sid := strings.TrimSpace(sessionID)
	if sid == "" || s.sessions == nil {
		return ""
	}
	sess, ok, err := s.sessions.Get(sid)
	if err != nil || !ok || sess.ProjectPath == nil {
		return ""
	}
	return effectiveTurnProjectPath(*sess.ProjectPath)
}

// resolveFilesBase picks the files-rail jail root (clamped off volume roots).
func (s *Server) resolveFilesBase(sessionID string) (base string, source string) {
	if env := filesEnvRoot(); env != "" {
		if sess := s.sessionProjectPath(sessionID); sess != "" {
			if abs, err := ensureDir(sess); err == nil {
				return clampFilesBase(abs), "session"
			}
		}
		if abs, err := ensureDir(env); err == nil {
			return clampFilesBase(abs), "env"
		}
		return clampFilesBase(env), "env"
	}
	if sess := s.sessionProjectPath(sessionID); sess != "" && !isUnsetProjectPath(sess) {
		if abs, err := ensureDir(sess); err == nil {
			return clampFilesBase(abs), "session"
		}
		return clampFilesBase(sess), "session"
	}
	raw := s.configProjectRaw()
	if !isUnsetProjectPath(raw) {
		if abs, err := ensureDir(raw); err == nil {
			return clampFilesBase(abs), "config"
		}
		return clampFilesBase(raw), "config"
	}
	if owner := defaultOwnerFilesBase(); owner != "" {
		return clampFilesBase(owner), "owner_workspace"
	}
	cwd, _ := os.Getwd()
	if isPackagedInstallDir(cwd) || isUnsetProjectPath(cwd) {
		return clampFilesBase(""), "unset"
	}
	return clampFilesBase(cwd), "cwd"
}

// defaultOwnerFilesBase mirrors the RMDY worker default: ~/Remedy
// (or ~/.remedy/workspace), not the entire user profile. A non-empty
// default_workspace config overrides the folder.
func defaultOwnerFilesBase() string {
	return defaultOwnerFilesBaseConfigured("")
}

func defaultOwnerFilesBaseConfigured(configured string) string {
	uh, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(uh) == "" {
		return ""
	}
	target := strings.TrimSpace(configured)
	if target == "" || isUnsetProjectPath(target) {
		target = filepath.Join(uh, "Remedy")
	}
	if abs, err := ensureDir(target); err == nil {
		return abs
	}
	fallback := filepath.Join(uh, ".remedy", "workspace")
	if abs, err := ensureDir(fallback); err == nil {
		return abs
	}
	return ""
}

// isSystemStartDir reports OS directories a packaged Desktop is merely launched
// from (Windows System32 / SysWOW64 / the Windows root, POSIX filesystem root).
// They are never a working folder the owner meant, so a shell started there is
// rebound to the session root in every access scope.
func isSystemStartDir(path string) bool {
	p := strings.TrimSpace(path)
	if p == "" {
		return false
	}
	p = filepath.Clean(p)
	if p == string(filepath.Separator) {
		return true
	}
	winRoot := strings.TrimSpace(os.Getenv("SystemRoot"))
	if winRoot == "" {
		winRoot = `C:\Windows`
	}
	winRoot = filepath.Clean(winRoot)
	for _, candidate := range []string{
		winRoot,
		filepath.Join(winRoot, "System32"),
		filepath.Join(winRoot, "SysWOW64"),
	} {
		if strings.EqualFold(p, candidate) {
			return true
		}
	}
	return false
}

func isPackagedInstallDir(path string) bool {
	p := filepath.Clean(strings.TrimSpace(path))
	if p == "" {
		return false
	}
	for _, name := range []string{
		"Remedy Desktop.exe",
		"remedy-runtime.exe",
		"remedy-runtime",
		"uninstall.exe",
	} {
		if st, err := os.Stat(filepath.Join(p, name)); err == nil && !st.IsDir() {
			return true
		}
	}
	webui := filepath.Join(p, "webui")
	windows := filepath.Join(p, "windows")
	if st, err := os.Stat(webui); err == nil && st.IsDir() {
		if st2, err2 := os.Stat(windows); err2 == nil && st2.IsDir() {
			return true
		}
	}
	return false
}

// maxListed bounds one folder listing; a longer folder is cut and reported as truncated.
const maxListed = 1000

// listFileEntries lists `listed`. Entry paths are relative to `base`, or absolute
// (forward slashes) when base is empty: the form a folder outside the project needs,
// so opening an entry never resolves against the project root.
func listFileEntries(listed, base string) ([]fileEntry, bool) {
	entries := make([]fileEntry, 0, 32)
	dirents, err := os.ReadDir(listed)
	if err != nil {
		return entries, false
	}
	names := make([]string, 0, len(dirents))
	for _, d := range dirents {
		names = append(names, d.Name())
	}
	sort.Strings(names)
	for _, name := range names {
		if strings.HasPrefix(name, ".") || isJunkListingName(name) {
			continue
		}
		full := filepath.Join(listed, name)
		rel := full
		if base != "" {
			if rel, err = filepath.Rel(base, full); err != nil {
				continue
			}
		}
		info, err := os.Stat(full)
		if err != nil {
			continue
		}
		if len(entries) >= maxListed {
			return entries, true
		}
		entries = append(entries, fileEntry{
			Name:  name,
			Path:  filepath.ToSlash(rel),
			IsDir: info.IsDir(),
		})
	}
	return entries, false
}

func filesPayload(listed, base, requestPath string) map[string]any {
	shown := "."
	if listed != base {
		if rel, err := filepath.Rel(base, listed); err == nil {
			shown = filepath.ToSlash(rel)
		} else {
			shown = listed
			base = listed
		}
	}
	raw := strings.TrimSpace(requestPath)
	if raw != "" && (filepath.IsAbs(raw) || (len(raw) > 1 && raw[1] == ':' && isDriveLetter(raw[0]))) {
		shown = listed
	}
	files, truncated := listFileEntries(listed, base)
	return map[string]any{
		"files":     files,
		"path":      shown,
		"root":      base,
		"truncated": truncated,
	}
}

// filesOutsidePayload lists a folder outside the project: the path and every entry are
// absolute, and root stays the project so "." still leads home.
func filesOutsidePayload(listed, project string) map[string]any {
	files, truncated := listFileEntries(listed, "")
	return map[string]any{
		"files":     files,
		"path":      filepath.ToSlash(listed),
		"root":      project,
		"truncated": truncated,
	}
}

type filePlace struct {
	Name string `json:"name"`
	Path string `json:"path"`
}

// filePlaces are the starting points the access scope allows: the project, the user's
// folders, and with full access every drive.
func filePlaces(scope string, roots []string) []filePlace {
	places := []filePlace{{Name: "Project", Path: "."}}
	for _, r := range roots[1:] {
		places = append(places, filePlace{Name: filepath.Base(r), Path: filepath.ToSlash(r)})
	}
	if normalizeAccessScope(scope) == "full" {
		if runtime.GOOS == "windows" {
			for c := 'A'; c <= 'Z'; c++ {
				d := string(c) + ":/"
				if _, err := os.Stat(d); err == nil {
					places = append(places, filePlace{Name: string(c) + ":", Path: d})
				}
			}
		} else {
			places = append(places, filePlace{Name: "/", Path: "/"})
		}
	}
	return places
}

func filesDenied(path, base string) map[string]any {
	return map[string]any{
		"files": []fileEntry{},
		"path":  path,
		"error": "path outside allowed directory",
		"root":  base,
	}
}

func (s *Server) handleListFiles(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Query().Get("path")
	if strings.TrimSpace(path) == "" {
		path = "."
	}
	sessionID := r.URL.Query().Get("session_id")
	envRoot := filesEnvRoot()
	jailBase, _ := s.resolveFilesBase(sessionID)

	if envRoot != "" {
		listed, err := jailPath(path, jailBase)
		if err != nil {
			writeJSON(w, http.StatusOK, filesDenied(path, jailBase))
			return
		}
		st, err := os.Stat(listed)
		if err != nil {
			if os.IsNotExist(err) {
				writeJSON(w, http.StatusOK, map[string]any{
					"files": []fileEntry{},
					"path":  path,
					"root":  jailBase,
					"error": "not found",
				})
				return
			}
			writeJSON(w, http.StatusOK, map[string]any{
				"files": []fileEntry{},
				"path":  path,
				"root":  jailBase,
			})
			return
		}
		if !st.IsDir() {
			writeJSON(w, http.StatusOK, map[string]any{
				"files": []fileEntry{},
				"path":  path,
				"root":  jailBase,
				"error": "not a directory",
			})
			return
		}
		writeJSON(w, http.StatusOK, filesPayload(listed, jailBase, path))
		return
	}

	projectRaw := s.sessionProjectPath(sessionID)
	if projectRaw == "" {
		projectRaw = s.configProjectRaw()
	}
	scope := effectiveAccessScope(s.configAccessScope(), projectRaw)
	roots := allowedReadRoots(scope, jailBase, s.userHomeForWorkspace())
	listed, err := resolveUnderRoots(path, roots, scope)
	if err != nil {
		writeJSON(w, http.StatusOK, filesDenied(path, jailBase))
		return
	}
	st, err := os.Stat(listed)
	if err != nil {
		if os.IsNotExist(err) {
			writeJSON(w, http.StatusOK, map[string]any{
				"files": []fileEntry{},
				"path":  path,
				"root":  jailBase,
				"error": "not found",
			})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"files": []fileEntry{},
			"path":  path,
			"root":  jailBase,
		})
		return
	}
	if !st.IsDir() {
		writeJSON(w, http.StatusOK, map[string]any{
			"files": []fileEntry{},
			"path":  path,
			"root":  jailBase,
			"error": "not a directory",
		})
		return
	}
	payload := filesPayload(listed, jailBase, path)
	if !pathUnder(listed, jailBase) {
		payload = filesOutsidePayload(listed, jailBase)
	}
	payload["places"] = filePlaces(scope, roots)
	writeJSON(w, http.StatusOK, payload)
}

func (s *Server) handleSearchFiles(w http.ResponseWriter, r *http.Request) {
	q := strings.TrimSpace(r.URL.Query().Get("query"))
	if q == "" {
		q = strings.TrimSpace(r.URL.Query().Get("q"))
	}
	if q == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "query (or q) is required"})
		return
	}
	sessionID := r.URL.Query().Get("session_id")
	pathOpt := strings.TrimSpace(r.URL.Query().Get("path"))

	base, _ := s.resolveFilesBase(sessionID)
	if pathOpt != "" {
		jailed, err := jailPath(pathOpt, base)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "path outside allowed directory"})
			return
		}
		st, err := os.Stat(jailed)
		if err != nil || !st.IsDir() {
			writeJSON(w, http.StatusOK, map[string]any{
				"query":   q,
				"results": []fileEntry{},
				"root":    jailed,
			})
			return
		}
		base = jailed
	}

	resolved, err := resolveAbsPath(base)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"query":   q,
			"results": []fileEntry{},
			"root":    base,
		})
		return
	}
	if filepath.Dir(resolved) == resolved {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "path is a drive root; pick a project folder before searching",
		})
		return
	}
	if hasBlockedSearchPart(resolved) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "path outside allowed directory"})
		return
	}

	safeQuery := strings.ReplaceAll(q, "/", "")
	safeQuery = strings.ReplaceAll(safeQuery, `\`, "")
	safeQuery = strings.ReplaceAll(safeQuery, "..", "")
	if safeQuery == "" {
		writeJSON(w, http.StatusOK, map[string]any{
			"query":   q,
			"results": []fileEntry{},
			"root":    base,
		})
		return
	}

	const maxResults = 50
	const maxScanned = 8000
	const maxDepth = 8
	needle := strings.ToLower(safeQuery)
	results := make([]fileEntry, 0, 16)
	scanned := 0

	_ = filepath.WalkDir(resolved, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return nil
		}
		if path == resolved {
			return nil
		}
		rel, err := filepath.Rel(resolved, path)
		if err != nil {
			return nil
		}
		depth := 0
		if rel != "." {
			depth = len(strings.FieldsFunc(rel, func(r rune) bool {
				return r == '/' || r == '\\'
			}))
		}
		name := d.Name()
		if d.IsDir() {
			if _, skip := filesSearchSkipDirs[name]; skip || strings.HasPrefix(name, ".") {
				return filepath.SkipDir
			}
			if depth >= maxDepth {
				return filepath.SkipDir
			}
		}
		scanned++
		if scanned > maxScanned {
			return filepath.SkipAll
		}
		if strings.HasPrefix(name, ".") {
			return nil
		}
		if !strings.Contains(strings.ToLower(name), needle) {
			return nil
		}
		results = append(results, fileEntry{
			Name:  name,
			Path:  filepath.ToSlash(rel),
			IsDir: d.IsDir(),
		})
		if len(results) >= maxResults {
			return filepath.SkipAll
		}
		return nil
	})

	sort.Slice(results, func(i, j int) bool {
		return len(results[i].Path) < len(results[j].Path)
	})
	writeJSON(w, http.StatusOK, map[string]any{
		"query":   q,
		"results": results,
		"root":    base,
		"scanned": scanned,
	})
}

var workspaceSkipNames = map[string]struct{}{
	".git": {}, ".hg": {}, ".svn": {}, "__pycache__": {}, "node_modules": {},
	".venv": {}, "venv": {}, "dist": {}, "build": {}, ".idea": {}, ".vs": {}, "target": {},
}

func listWorkspaceEntries(root string, limit int) []workspaceEntry {
	if limit <= 0 {
		limit = 40
	}
	entries := make([]workspaceEntry, 0, limit)
	dirents, err := os.ReadDir(root)
	if err != nil {
		return entries
	}
	sort.Slice(dirents, func(i, j int) bool {
		if dirents[i].IsDir() != dirents[j].IsDir() {
			return dirents[i].IsDir()
		}
		return strings.ToLower(dirents[i].Name()) < strings.ToLower(dirents[j].Name())
	})
	for _, d := range dirents {
		name := d.Name()
		if strings.HasPrefix(name, ".") || isJunkListingName(name) {
			continue
		}
		if _, skip := workspaceSkipNames[name]; skip {
			continue
		}
		typ := "file"
		if d.IsDir() {
			typ = "dir"
		}
		entries = append(entries, workspaceEntry{Name: name, Type: typ})
		if len(entries) >= limit {
			break
		}
	}
	return entries
}

func (s *Server) handleGetWorkspace(w http.ResponseWriter, r *http.Request) {
	sessionID := r.URL.Query().Get("session_id")
	root, source := s.resolveFilesBase(sessionID)
	if abs, err := ensureDir(root); err == nil {
		root = abs
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"project_path": root,
		"source":       source,
		"entries":      listWorkspaceEntries(root, 40),
	})
}
