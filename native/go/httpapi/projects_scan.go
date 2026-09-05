package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

var projectScanExts = map[string]string{
	".py": "python", ".js": "javascript", ".jsx": "javascript",
	".ts": "typescript", ".tsx": "typescript", ".mjs": "javascript",
	".rs": "rust",
	".c": "other", ".cpp": "other", ".h": "other",
	".json": "other", ".yaml": "other", ".yml": "other",
	".toml": "other", ".md": "other", ".txt": "other",
	".css": "other", ".html": "other",
}

var projectScanIgnored = map[string]struct{}{
	".git": {}, "__pycache__": {}, "node_modules": {}, ".venv": {}, "venv": {},
	"dist": {}, "build": {}, ".next": {}, "target": {}, "auth": {},
}

func (s *Server) handleProjectsScan(w http.ResponseWriter, r *http.Request) {
	raw := strings.TrimSpace(r.URL.Query().Get("path"))
	if raw == "" {
		raw = "."
	}
	scope := effectiveAccessScope(s.configAccessScope(), s.configProjectRaw())
	proj := s.configProjectRaw()
	if isUnsetProjectPath(proj) {
		if owner := defaultOwnerFilesBase(); owner != "" {
			proj = owner
		} else if uh := s.userHomeForWorkspace(); uh != "" {
			proj = uh
		} else {
			cwd, _ := os.Getwd()
			if !isPackagedInstallDir(cwd) {
				proj = cwd
			}
		}
	}
	roots := allowedReadRoots(scope, proj, s.userHomeForWorkspace())
	target, err := resolveUnderRoots(raw, roots, scope)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Path not allowed: " + err.Error()})
		return
	}
	if isProtectedSecretPath(target, s.remedyHomeDir()) {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": "Path not allowed: protected Remedy secrets location",
		})
		return
	}
	st, err := os.Stat(target)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Path not found: " + raw})
		return
	}
	if !st.IsDir() {
		target = filepath.Dir(target)
		if isProtectedSecretPath(target, s.remedyHomeDir()) {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"detail": "Path not allowed: protected Remedy secrets location",
			})
			return
		}
		if _, err := resolveUnderRoots(target, roots, scope); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Path not allowed: " + err.Error()})
			return
		}
	}

	files := map[string][]string{
		"python": {}, "javascript": {}, "typescript": {}, "rust": {}, "other": {},
	}
	_ = filepath.WalkDir(target, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		name := d.Name()
		if d.IsDir() {
			if _, skip := projectScanIgnored[name]; skip {
				return filepath.SkipDir
			}
			return nil
		}
		parts := splitPathParts(path)
		for _, p := range parts {
			if _, skip := projectScanIgnored[p]; skip {
				return nil
			}
		}
		if isProtectedSecretPath(path, s.remedyHomeDir()) {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(name))
		cat := projectScanExts[ext]
		if cat == "" {
			cat = "other"
		}
		rel, err := filepath.Rel(target, path)
		if err != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		if len(files[cat]) < 100 {
			files[cat] = append(files[cat], rel)
		}
		return nil
	})

	counts := map[string]int{}
	for k, v := range files {
		counts[k] = len(v)
	}
	summary := map[string]any{
		"path":        target,
		"file_counts": counts,
		"top_files":   files,
		"python_deps": "",
		"js_deps":     "",
	}
	if pp := filepath.Join(target, "pyproject.toml"); fileExists(pp) && !isProtectedSecretPath(pp, s.remedyHomeDir()) {
		if raw, err := os.ReadFile(pp); err == nil {
			s := string(raw)
			if len(s) > 2000 {
				s = s[:2000]
			}
			summary["python_deps"] = s
		}
	}
	if pj := filepath.Join(target, "package.json"); fileExists(pj) && !isProtectedSecretPath(pj, s.remedyHomeDir()) {
		if raw, err := os.ReadFile(pj); err == nil {
			s := string(raw)
			if len(s) > 2000 {
				s = s[:2000]
			}
			summary["js_deps"] = s
		}
	}
	writeJSON(w, http.StatusOK, summary)
}
