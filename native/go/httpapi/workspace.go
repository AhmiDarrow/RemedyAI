package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
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
	return strings.TrimSpace(*sess.ProjectPath)
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
	if uh := s.userHomeForWorkspace(); uh != "" {
		return clampFilesBase(uh), "home"
	}
	cwd, _ := os.Getwd()
	return clampFilesBase(cwd), "cwd"
}

func listFileEntries(listed, base string) []fileEntry {
	entries := make([]fileEntry, 0, 32)
	dirents, err := os.ReadDir(listed)
	if err != nil {
		return entries
	}
	names := make([]string, 0, len(dirents))
	for _, d := range dirents {
		names = append(names, d.Name())
	}
	sort.Strings(names)
	for _, name := range names {
		if strings.HasPrefix(name, ".") {
			continue
		}
		full := filepath.Join(listed, name)
		rel, err := filepath.Rel(base, full)
		if err != nil {
			continue
		}
		rel = filepath.ToSlash(rel)
		info, err := os.Stat(full)
		if err != nil {
			continue
		}
		entries = append(entries, fileEntry{
			Name:  name,
			Path:  rel,
			IsDir: info.IsDir(),
		})
		if len(entries) >= 200 {
			break
		}
	}
	return entries
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
	return map[string]any{
		"files": listFileEntries(listed, base),
		"path":  shown,
		"root":  base,
	}
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
	base := jailBase
	if !pathUnder(listed, jailBase) {
		base = listed
	}
	writeJSON(w, http.StatusOK, filesPayload(listed, base, path))
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
		if strings.HasPrefix(name, ".") {
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
