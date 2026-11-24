package httpapi

import (
	"crypto/sha256"
	"encoding/base64"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
)

func init() {
	// Windows may register .js as a non-script type; FileServer + nosniff
	// would then fail dynamic import() of lazy chunks such as SetupFlow.
	_ = mime.AddExtensionType(".js", "text/javascript; charset=utf-8")
	_ = mime.AddExtensionType(".mjs", "text/javascript; charset=utf-8")
	_ = mime.AddExtensionType(".cjs", "text/javascript; charset=utf-8")
	_ = mime.AddExtensionType(".css", "text/css; charset=utf-8")
	_ = mime.AddExtensionType(".wasm", "application/wasm")
	_ = mime.AddExtensionType(".woff2", "font/woff2")
	_ = mime.AddExtensionType(".woff", "font/woff")
}

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
    <p>pip install: upgrade with <code>pip install --upgrade remedy-ai</code> (wheels include the UI from 0.69.2).</p>
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

// Hashed Vite assets (name-<hash>.js) can be cached forever; the filename changes on rebuild.
const webUIHashedAssetCacheControl = "public, max-age=31536000, immutable"

var webUIHTMLAssetRefRE = regexp.MustCompile(`(?i)(?:src|href)\s*=\s*["']([^"']+)["']`)
var webUIJSImportRE = regexp.MustCompile(`["'](\./[A-Za-z0-9_./@-]+\.(?:js|mjs|css|wasm))["']`)
var webUIHashedAssetRE = regexp.MustCompile(`(?i)^[A-Za-z0-9_.@-]+-[A-Za-z0-9_-]{6,}\.(js|mjs|cjs|css|woff2?|wasm|map)$`)

// webUICSPBase mirrors the desktop CSP in tauri.conf.json for the browser
// mode, minus the Tauri-only ipc:/asset: sources. Inline scripts are allowed
// only by sha256 hash (see webUICSP); frame-ancestors blocks framing.
const webUICSPBase = "default-src 'self'; " +
	"style-src 'self' 'unsafe-inline'; " +
	"connect-src 'self' https: http:; " +
	"img-src 'self' data: blob: https: http:; " +
	"font-src 'self' data:; " +
	"media-src 'self' blob: data:; " +
	"worker-src 'self' blob:; " +
	"frame-src https: http: data: blob:; " +
	"child-src https: http: data: blob:; " +
	"object-src 'none'; " +
	"base-uri 'self'; " +
	"form-action 'self'; " +
	"frame-ancestors 'none'"

// inlineScriptRE matches <script> blocks without a src attribute.
var inlineScriptRE = regexp.MustCompile(`(?is)<script\b([^>]*)>(.*?)</script>`)
var scriptSrcAttrRE = regexp.MustCompile(`(?i)\bsrc\s*=`)

// webUICSP returns the CSP for a WebUI document. Inline <script> blocks in
// html are allowed by their sha256 hash only, never 'unsafe-inline'.
func webUICSP(html []byte) string {
	sources := []string{"'self'"}
	for _, m := range inlineScriptRE.FindAllSubmatch(html, -1) {
		if scriptSrcAttrRE.Match(m[1]) {
			continue
		}
		sum := sha256.Sum256(m[2])
		sources = append(sources, "'sha256-"+base64.StdEncoding.EncodeToString(sum[:])+"'")
	}
	return "script-src " + strings.Join(sources, " ") + "; " + webUICSPBase
}

// setWebUISecurityHeaders applies the browser-mode hardening headers.
func setWebUISecurityHeaders(h http.Header, csp string) {
	h.Set("Content-Security-Policy", csp)
	h.Set("X-Content-Type-Options", "nosniff")
	h.Set("X-Frame-Options", "DENY")
	// A one-time ?bootstrap= link must never leak through a Referer header.
	h.Set("Referrer-Policy", "no-referrer")
}

func withWebUISecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		setWebUISecurityHeaders(w.Header(), webUICSP(nil))
		next.ServeHTTP(w, r)
	})
}

// FindWebUIDir locates built desktop SPA assets (Python find_webui_dir parity).
// Prefer REMEDY_WEBUI_DIR, then a complete webui/ui folder next to the
// executable (the installed layout), then live desktop/dist for dev, then the
// copy bundled in the pip wheel (REMEDY_BUNDLED_WEBUI_DIR, set by `remedy serve`).
// Incomplete dirs (index.html that references a chunk that is not on disk) are
// skipped so a stale workspace checkout cannot shadow the packaged bundle.
func FindWebUIDir() string {
	candidates := make([]string, 0, 32)
	if env := strings.TrimSpace(os.Getenv("REMEDY_WEBUI_DIR")); env != "" {
		candidates = append(candidates, expandUser(env))
	}
	if exe, err := os.Executable(); err == nil {
		exe, _ = filepath.EvalSymlinks(exe)
		exeDir := filepath.Dir(exe)
		// Installed runtime: $INSTDIR/webui next to remedy-runtime.exe.
		// Prefer this over cwd-walked desktop/dist, which may be a stale checkout.
		candidates = append(candidates,
			filepath.Join(exeDir, "webui"),
			filepath.Join(exeDir, "ui"),
			filepath.Join(exeDir, "resources", "webui"),
			filepath.Join(filepath.Dir(exeDir), "webui"),
			filepath.Join(filepath.Dir(exeDir), "resources", "webui"),
		)
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
		// desktop/src-tauri/target/debug/*.exe → desktop/dist via parents
		for _, p := range parentsOf(exe) {
			candidates = append(candidates, filepath.Join(p, "desktop", "dist"))
			candidates = append(candidates, filepath.Join(p, "dist"))
		}
		candidates = append(candidates, filepath.Join(exeDir, "desktop", "dist"))
	}
	if bundled := strings.TrimSpace(os.Getenv("REMEDY_BUNDLED_WEBUI_DIR")); bundled != "" {
		candidates = append(candidates, expandUser(bundled))
	}
	if cwd, err := os.Getwd(); err == nil {
		for _, p := range parentsOf(cwd) {
			candidates = append(candidates, filepath.Join(p, "ui"), filepath.Join(p, "webui"))
		}
	}
	return firstCompleteWebUIDir(candidates)
}

func firstCompleteWebUIDir(candidates []string) string {
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
	return len(webUIMissingAssets(dir)) == 0
}

// webUIMissingAssets returns relative paths that index.html (and its JS
// chunks) reference but that are not present under dir. A missing index.html
// is reported as "index.html". Used so packaging and FindWebUIDir refuse a
// mismatched bundle (index points at SetupFlow-*.js that was never copied).
func webUIMissingAssets(dir string) []string {
	st, err := os.Stat(dir)
	if err != nil || !st.IsDir() {
		return []string{"index.html"}
	}
	index := filepath.Join(dir, "index.html")
	st, err = os.Stat(index)
	if err != nil || st.IsDir() {
		return []string{"index.html"}
	}
	html, err := os.ReadFile(index)
	if err != nil {
		return []string{"index.html"}
	}
	root, err := filepath.Abs(dir)
	if err != nil {
		root = dir
	}
	var missing []string
	seen := map[string]struct{}{}
	var queue []string
	enqueue := func(rel string) {
		rel = strings.TrimSpace(rel)
		if rel == "" || rel == "index.html" {
			return
		}
		if i := strings.IndexAny(rel, "?#"); i >= 0 {
			rel = rel[:i]
		}
		rel = strings.TrimPrefix(rel, "./")
		rel = strings.TrimPrefix(rel, "/")
		if rel == "" || strings.Contains(rel, "..") {
			return
		}
		if strings.HasPrefix(rel, "http:") || strings.HasPrefix(rel, "https:") ||
			strings.HasPrefix(rel, "data:") || strings.HasPrefix(rel, "mailto:") {
			return
		}
		if _, ok := seen[rel]; ok {
			return
		}
		seen[rel] = struct{}{}
		path := filepath.Join(dir, filepath.FromSlash(rel))
		abs, err := filepath.Abs(path)
		if err != nil || !(abs == root || strings.HasPrefix(abs, root+string(os.PathSeparator))) {
			return
		}
		st, err := os.Stat(path)
		if err != nil || st.IsDir() {
			missing = append(missing, rel)
			return
		}
		low := strings.ToLower(rel)
		if strings.HasSuffix(low, ".js") || strings.HasSuffix(low, ".mjs") {
			queue = append(queue, rel)
		}
	}
	for _, m := range webUIHTMLAssetRefRE.FindAllSubmatch(html, -1) {
		ref := string(m[1])
		if strings.HasPrefix(ref, "http:") || strings.HasPrefix(ref, "https:") ||
			strings.HasPrefix(ref, "data:") || strings.HasPrefix(ref, "mailto:") ||
			strings.HasPrefix(ref, "javascript:") || strings.HasPrefix(ref, "#") {
			continue
		}
		enqueue(ref)
	}
	for len(queue) > 0 {
		rel := queue[0]
		queue = queue[1:]
		body, err := os.ReadFile(filepath.Join(dir, filepath.FromSlash(rel)))
		if err != nil {
			continue
		}
		base := filepath.ToSlash(filepath.Dir(rel))
		for _, m := range webUIJSImportRE.FindAllSubmatch(body, -1) {
			imp := string(m[1])
			joined := imp
			if strings.HasPrefix(imp, "./") {
				if base == "." || base == "" {
					joined = strings.TrimPrefix(imp, "./")
				} else {
					joined = base + "/" + strings.TrimPrefix(imp, "./")
				}
			}
			enqueue(joined)
		}
	}
	return missing
}

func setWebUIAssetCacheHeaders(h http.Header, name string) {
	if webUIHashedAssetRE.MatchString(name) {
		h.Set("Cache-Control", webUIHashedAssetCacheControl)
		h.Del("Pragma")
		return
	}
	for k, v := range webUIHTMLHeaders {
		h.Set(k, v)
	}
}

func withWebUIAssetHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		setWebUISecurityHeaders(w.Header(), webUICSP(nil))
		setWebUIAssetCacheHeaders(w.Header(), filepath.Base(r.URL.Path))
		next.ServeHTTP(w, r)
	})
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
		s.mux.Handle("GET /assets/", withWebUIAssetHeaders(http.StripPrefix("/assets/", fileServer)))
	}
	// GET only — separate HEAD wildcards conflict with more-specific GET patterns on ServeMux.
	s.mux.HandleFunc("GET /{$}", s.handleWebUIIndex)
	s.mux.HandleFunc("GET /{path...}", s.handleWebUISPA)
}

func (s *Server) handleWebUIMissing(w http.ResponseWriter, _ *http.Request) {
	setWebUISecurityHeaders(w.Header(), webUICSP(nil))
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
	// Hash the file being served so a rebuilt desktop/dist stays allowed.
	html, _ := os.ReadFile(index)
	setWebUISecurityHeaders(w.Header(), webUICSP(html))
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
				setWebUISecurityHeaders(w.Header(), webUICSP(nil))
				setWebUIAssetCacheHeaders(w.Header(), filepath.Base(abs))
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
