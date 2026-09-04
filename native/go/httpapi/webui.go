package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

const webUIMissingHTML = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Remedy WebUI</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #0a0a1a; color: #e0e0e0;
           display: flex; min-height: 100vh; align-items: center; justify-content: center; margin: 0; }
    .card { max-width: 420px; padding: 1.75rem; border: 1px solid #1e1e3e; border-radius: 12px;
            background: #12122a; }
    h1 { color: #7c3aed; font-size: 1.35rem; margin: 0 0 0.75rem; }
    p { color: #aaa; font-size: 0.95rem; line-height: 1.45; margin: 0 0 0.75rem; }
    a { color: #a78bfa; }
    code { font-size: 0.85rem; color: #c4b5fd; }
  </style>
</head>
<body>
  <div class="card">
    <h1>WebUI assets not bundled</h1>
    <p>The local API is running, but the chat WebUI files were not found next to the server.</p>
    <p>Use the desktop app, or build with <code>cd desktop &amp;&amp; npm run build</code>.</p>
    <p>Or set <code>REMEDY_WEBUI_DIR</code> to a folder that contains <code>index.html</code>.</p>
  </div>
</body>
</html>
`

var webUIHTMLHeaders = map[string]string{
	"Cache-Control": "no-cache, no-store, must-revalidate",
	"Pragma":        "no-cache",
}

// FindWebUIDir locates built desktop SPA assets (Python find_webui_dir parity).
// Prefer REMEDY_WEBUI_DIR, then live desktop/dist, then staged webui/ui next to the binary.
func FindWebUIDir() string {
	candidates := make([]string, 0, 24)
	if env := strings.TrimSpace(os.Getenv("REMEDY_WEBUI_DIR")); env != "" {
		candidates = append(candidates, expandUser(env))
	}
	if root := strings.TrimSpace(os.Getenv("REMEDY_DEV_ROOT")); root != "" {
		candidates = append(candidates, filepath.Join(expandUser(root), "desktop", "dist"))
	}
	if cwd, err := os.Getwd(); err == nil {
		for _, p := range parentsOf(cwd) {
			candidates = append(candidates, filepath.Join(p, "desktop", "dist"))
		}
	}
	if exe, err := os.Executable(); err == nil {
		exe, _ = filepath.EvalSymlinks(exe)
		exeDir := filepath.Dir(exe)
		// desktop/src-tauri/target/debug/*.exe → desktop/dist via parents[3]
		for _, p := range parentsOf(exe) {
			candidates = append(candidates, filepath.Join(p, "desktop", "dist"))
			candidates = append(candidates, filepath.Join(p, "dist"))
		}
		candidates = append(candidates,
			filepath.Join(exeDir, "ui"),
			filepath.Join(exeDir, "webui"),
			filepath.Join(exeDir, "desktop", "dist"),
			filepath.Join(exeDir, "resources", "webui"),
			filepath.Join(filepath.Dir(exeDir), "webui"),
			filepath.Join(filepath.Dir(exeDir), "resources", "webui"),
		)
	}
	if cwd, err := os.Getwd(); err == nil {
		for _, p := range parentsOf(cwd) {
			candidates = append(candidates, filepath.Join(p, "ui"), filepath.Join(p, "webui"))
		}
	}

	seen := map[string]struct{}{}
	for _, c := range candidates {
		if c == "" {
			continue
		}
		key := c
		if abs, err := filepath.Abs(c); err == nil {
			key = abs
		}
		if _, ok := seen[key]; ok {
			continue
		}
		seen[key] = struct{}{}
		if webUIDirOK(c) {
			return key
		}
	}
	return ""
}

func webUIDirOK(dir string) bool {
	st, err := os.Stat(dir)
	if err != nil || !st.IsDir() {
		return false
	}
	st, err = os.Stat(filepath.Join(dir, "index.html"))
	return err == nil && !st.IsDir()
}

func parentsOf(path string) []string {
	abs, err := filepath.Abs(path)
	if err != nil {
		abs = path
	}
	out := make([]string, 0, 8)
	cur := abs
	for i := 0; i < 8; i++ {
		out = append(out, cur)
		parent := filepath.Dir(cur)
		if parent == cur {
			break
		}
		cur = parent
	}
	return out
}

func expandUser(p string) string {
	p = strings.TrimSpace(p)
	if p == "" {
		return ""
	}
	if strings.HasPrefix(p, "~"+string(os.PathSeparator)) || p == "~" {
		home, err := os.UserHomeDir()
		if err != nil || home == "" {
			return p
		}
		if p == "~" {
			return home
		}
		return filepath.Join(home, p[2:])
	}
	return p
}

func (s *Server) mountWebUI() {
	override := strings.TrimSpace(s.webUIDir)
	var webDir string
	if override != "" {
		// Explicit Config.WebUIDir / tests: do not fall back to discovery.
		if webUIDirOK(override) {
			webDir = override
			if abs, err := filepath.Abs(override); err == nil {
				webDir = abs
			}
		}
	} else {
		webDir = FindWebUIDir()
	}
	s.webUIDir = webDir
	if webDir == "" {
		s.mux.HandleFunc("GET /{$}", s.handleWebUIMissing)
		return
	}
	assets := filepath.Join(webDir, "assets")
	if st, err := os.Stat(assets); err == nil && st.IsDir() {
		fileServer := http.FileServer(http.Dir(assets))
		s.mux.Handle("GET /assets/", http.StripPrefix("/assets/", fileServer))
	}
	// GET only — separate HEAD wildcards conflict with more-specific GET patterns on ServeMux.
	s.mux.HandleFunc("GET /{$}", s.handleWebUIIndex)
	s.mux.HandleFunc("GET /{path...}", s.handleWebUISPA)
}

func (s *Server) handleWebUIMissing(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	for k, v := range webUIHTMLHeaders {
		w.Header().Set(k, v)
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(webUIMissingHTML))
}

func (s *Server) handleWebUIIndex(w http.ResponseWriter, r *http.Request) {
	index := filepath.Join(s.webUIDir, "index.html")
	for k, v := range webUIHTMLHeaders {
		w.Header().Set(k, v)
	}
	http.ServeFile(w, r, index)
}

func (s *Server) handleWebUISPA(w http.ResponseWriter, r *http.Request) {
	fullPath := strings.Trim(r.PathValue("path"), "/")
	if spaReservedPrefix(fullPath) {
		http.NotFound(w, r)
		return
	}
	if fullPath == "" {
		s.handleWebUIIndex(w, r)
		return
	}
	candidate := filepath.Join(s.webUIDir, filepath.FromSlash(fullPath))
	if abs, err := filepath.Abs(candidate); err == nil {
		root, _ := filepath.Abs(s.webUIDir)
		if root != "" && strings.HasPrefix(abs, root+string(os.PathSeparator)) {
			if st, err := os.Stat(abs); err == nil && !st.IsDir() {
				http.ServeFile(w, r, abs)
				return
			}
		}
	}
	s.handleWebUIIndex(w, r)
}

func spaReservedPrefix(fullPath string) bool {
	first, _, _ := strings.Cut(fullPath, "/")
	switch first {
	case "api", "docs", "redoc", "dashboard", "openapi", "assets", "connect":
		return true
	default:
		return false
	}
}
