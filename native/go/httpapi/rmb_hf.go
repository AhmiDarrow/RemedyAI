package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
)

const (
	hfAPI              = "https://huggingface.co"
	hfUA               = "RemedyAI/1.0"
	maxHFJSONBytes     = 8 * 1024 * 1024
	maxHFDownloadBytes = 80 * 1024 * 1024 * 1024
)

var (
	hfRepoRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)+$`)
	hfHosts  = map[string]struct{}{
		"huggingface.co": {}, "www.huggingface.co": {},
		"hf.co": {}, "www.hf.co": {},
	}
	hfQuantRank = []string{
		"q4_k_m", "q4_k_s", "q5_k_m", "q5_k_s", "q4_k", "q5_k", "q6_k", "q8_0",
		"q3_k_m", "q4_0", "q5_0", "q3_k", "iq4_xs", "iq4_nl", "q2_k",
	}
)

type hfProgress struct {
	mu   sync.Mutex
	data map[string]any
	cancel chan struct{}
	pulling bool
}

func newHFProgress() *hfProgress {
	return &hfProgress{data: idleHFProgress()}
}

func idleHFProgress() map[string]any {
	return map[string]any{
		"phase": "idle", "query": "", "repo": "", "filename": "",
		"bytes_done": 0, "bytes_total": 0, "pct": 0,
		"error": nil, "path": nil, "message": "",
	}
}

func (p *hfProgress) snapshot() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make(map[string]any, len(p.data))
	for k, v := range p.data {
		out[k] = v
	}
	return out
}

func (p *hfProgress) set(kv map[string]any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for k, v := range kv {
		p.data[k] = v
	}
	total := anyInt(p.data["bytes_total"], 0)
	done := anyInt(p.data["bytes_done"], 0)
	if total > 0 {
		p.data["pct"] = done * 100 / total
	} else {
		p.data["pct"] = 0
	}
}

func (p *hfProgress) reset() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.data = idleHFProgress()
	p.cancel = nil
	p.pulling = false
}

func hfHostAllowed(host string) bool {
	h := strings.ToLower(strings.TrimSpace(host))
	if i := strings.Index(h, "@"); i >= 0 {
		h = h[i+1:]
	}
	if j := strings.Index(h, ":"); j >= 0 {
		h = h[:j]
	}
	h = strings.Trim(h, ".")
	if _, ok := hfHosts[h]; ok {
		return true
	}
	return strings.HasSuffix(h, ".huggingface.co") || strings.HasSuffix(h, ".hf.co")
}

func sanitizeHFRepo(repo string) (string, error) {
	r := strings.Trim(strings.ReplaceAll(strings.TrimSpace(repo), `\`, "/"), "/")
	if !hfRepoRE.MatchString(r) {
		return "", fmt.Errorf("invalid Hugging Face repo id")
	}
	return r, nil
}

func sanitizeHFFilename(name string) (string, error) {
	n := strings.ReplaceAll(strings.TrimSpace(name), `\`, "/")
	n = strings.TrimPrefix(n, "/")
	if n == "" || strings.Contains(n, "..") {
		return "", fmt.Errorf("invalid filename")
	}
	return n, nil
}

func sanitizeHFRevision(rev string) string {
	r := strings.TrimSpace(rev)
	if r == "" {
		return "main"
	}
	return r
}

func hfAuthHeaders() http.Header {
	h := make(http.Header)
	h.Set("User-Agent", hfUA)
	h.Set("Accept", "*/*")
	tok := strings.TrimSpace(os.Getenv("HF_TOKEN"))
	if tok == "" {
		tok = strings.TrimSpace(os.Getenv("HUGGING_FACE_HUB_TOKEN"))
	}
	if tok != "" {
		h.Set("Authorization", "Bearer "+tok)
	}
	return h
}

func resolveHFURL(repo, filename, revision string) string {
	rev := sanitizeHFRevision(revision)
	parts := strings.Split(filename, "/")
	enc := make([]string, 0, len(parts))
	for _, p := range parts {
		enc = append(enc, url.PathEscape(p))
	}
	return fmt.Sprintf("%s/%s/resolve/%s/%s", hfAPI, repo, url.PathEscape(rev), strings.Join(enc, "/"))
}

type hfHint struct {
	Kind     string
	Query    string
	Repo     string
	Revision string
	Filename string
	URL      string
}

func (h hfHint) public() map[string]any {
	var repo, rev, file, u any
	if h.Repo != "" {
		repo = h.Repo
	}
	if h.Revision != "" {
		rev = h.Revision
	}
	if h.Filename != "" {
		file = h.Filename
	}
	if h.URL != "" {
		u = h.URL
	}
	return map[string]any{
		"kind": h.Kind, "query": h.Query,
		"repo": repo, "revision": rev, "filename": file, "url": u,
	}
}

func parseHFHint(raw string) (hfHint, error) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return hfHint{}, fmt.Errorf("Enter a model name, owner/repo, or Hugging Face URL")
	}
	if len(text) > 500 {
		return hfHint{}, fmt.Errorf("Query is too long")
	}
	text = strings.TrimSuffix(text, "?download=true")
	if strings.Contains(text, "://") {
		u, err := url.Parse(strings.Fields(text)[0])
		if err != nil {
			return hfHint{}, fmt.Errorf("Only huggingface.co / hf.co URLs are allowed")
		}
		if u.Scheme != "http" && u.Scheme != "https" {
			return hfHint{}, fmt.Errorf("Only http(s) Hugging Face URLs are allowed")
		}
		if !hfHostAllowed(u.Host) {
			return hfHint{}, fmt.Errorf("Only huggingface.co / hf.co URLs are allowed")
		}
		parts := strings.Split(strings.Trim(u.Path, "/"), "/")
		clean := make([]string, 0, len(parts))
		for _, p := range parts {
			if p != "" {
				clean = append(clean, p)
			}
		}
		if len(clean) < 2 {
			return hfHint{}, fmt.Errorf("URL is missing owner/repo")
		}
		repo, err := sanitizeHFRepo(clean[0] + "/" + clean[1])
		if err != nil {
			return hfHint{}, err
		}
		rest := clean[2:]
		revision := "main"
		var filename string
		if len(rest) > 0 && (rest[0] == "resolve" || rest[0] == "blob" || rest[0] == "tree") {
			if len(rest) >= 2 {
				revision = sanitizeHFRevision(rest[1])
			}
			if (rest[0] == "resolve" || rest[0] == "blob") && len(rest) >= 3 {
				filename, _ = sanitizeHFFilename(strings.Join(rest[2:], "/"))
			}
		} else if len(rest) > 0 && strings.HasSuffix(strings.ToLower(rest[len(rest)-1]), ".gguf") {
			filename, _ = sanitizeHFFilename(strings.Join(rest, "/"))
		}
		hint := hfHint{Kind: "repo", Query: text, Repo: repo, Revision: revision, Filename: filename}
		if filename != "" {
			hint.Kind = "url"
			hint.URL = resolveHFURL(repo, filename, revision)
		}
		return hint, nil
	}
	cleaned := strings.Trim(strings.ReplaceAll(text, `\`, "/"), "/")
	bits := strings.Split(cleaned, "/")
	parts := make([]string, 0, len(bits))
	for _, b := range bits {
		if b != "" {
			parts = append(parts, b)
		}
	}
	if len(parts) >= 2 && hfRepoRE.MatchString(parts[0]+"/"+parts[1]) {
		repo, err := sanitizeHFRepo(parts[0] + "/" + parts[1])
		if err != nil {
			return hfHint{}, err
		}
		rest := parts[2:]
		var filename string
		if len(rest) > 0 && strings.HasSuffix(strings.ToLower(rest[len(rest)-1]), ".gguf") {
			filename, _ = sanitizeHFFilename(strings.Join(rest, "/"))
		}
		if filename != "" {
			return hfHint{
				Kind: "url", Query: text, Repo: repo, Revision: "main",
				Filename: filename, URL: resolveHFURL(repo, filename, "main"),
			}, nil
		}
		return hfHint{Kind: "repo", Query: text, Repo: repo, Revision: "main"}, nil
	}
	return hfHint{Kind: "search", Query: text}, nil
}

func hfGetJSON(rawURL string, timeout time.Duration) (any, http.Header, error) {
	client := &http.Client{Timeout: timeout}
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, nil, err
	}
	req.Header = hfAuthHeaders()
	req.Header.Set("Accept", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return nil, nil, fmt.Errorf("Hugging Face API unreachable: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, nil, fmt.Errorf("Hugging Face API HTTP %d", resp.StatusCode)
	}
	limited := io.LimitReader(resp.Body, maxHFJSONBytes+1)
	raw, err := io.ReadAll(limited)
	if err != nil {
		return nil, nil, err
	}
	if len(raw) > maxHFJSONBytes {
		return nil, nil, fmt.Errorf("Hugging Face API response too large")
	}
	var data any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, nil, fmt.Errorf("Hugging Face API returned invalid JSON")
	}
	return data, resp.Header.Clone(), nil
}

func searchGGUFRepos(query string, limit int) ([]map[string]any, error) {
	q := strings.TrimSpace(query)
	if q == "" {
		return nil, fmt.Errorf("Enter a model name to search")
	}
	if len(q) > 200 {
		return nil, fmt.Errorf("Search query is too long")
	}
	if limit < 1 {
		limit = 12
	}
	if limit > 30 {
		limit = 30
	}
	seen := map[string]struct{}{}
	out := make([]map[string]any, 0)
	ingest := func(rows any) {
		list, ok := rows.([]any)
		if !ok {
			return
		}
		for _, row := range list {
			m, ok := row.(map[string]any)
			if !ok {
				continue
			}
			rid := strings.TrimSpace(anyString(m["id"]))
			if rid == "" {
				continue
			}
			safe, err := sanitizeHFRepo(rid)
			if err != nil {
				continue
			}
			if _, ok := seen[safe]; ok {
				continue
			}
			seen[safe] = struct{}{}
			tags := []string{}
			if raw, ok := m["tags"].([]any); ok {
				for i, t := range raw {
					if i >= 16 {
						break
					}
					tags = append(tags, fmt.Sprint(t))
				}
			}
			out = append(out, map[string]any{
				"id": safe, "downloads": anyInt(m["downloads"], 0),
				"likes": anyInt(m["likes"], 0), "tags": tags,
				"pipeline_tag": m["pipeline_tag"], "private": anyBoolDef(m["private"], false),
			})
		}
	}
	params := []string{
		fmt.Sprintf("search=%s&filter=gguf&sort=downloads&direction=-1&limit=%d", url.QueryEscape(q), limit),
		fmt.Sprintf("search=%s&sort=downloads&direction=-1&limit=%d", url.QueryEscape("GGUF "+q), limit),
		fmt.Sprintf("search=%s&sort=downloads&direction=-1&limit=%d", url.QueryEscape(q), max(6, limit/2)),
	}
	for _, qs := range params {
		data, _, err := hfGetJSON(hfAPI+"/api/models?"+qs, 30*time.Second)
		if err != nil {
			continue
		}
		ingest(data)
		if len(out) >= 3 {
			break
		}
	}
	sort.Slice(out, func(i, j int) bool {
		di, dj := anyInt(out[i]["downloads"], 0), anyInt(out[j]["downloads"], 0)
		if di != dj {
			return di > dj
		}
		return anyString(out[i]["id"]) < anyString(out[j]["id"])
	})
	if len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}

func quantKey(name string) (int, string) {
	stem := strings.ToLower(strings.TrimSuffix(name, ".gguf"))
	for i, tag := range hfQuantRank {
		if strings.Contains(stem, tag) {
			return i, stem
		}
	}
	return len(hfQuantRank) + 1, stem
}

func fileRole(path string) string {
	name := strings.ToLower(filepath.Base(path))
	if strings.Contains(name, "mmproj") {
		return "mmproj"
	}
	if strings.Contains(name, "-00001-of-") {
		return "shard"
	}
	return "weights"
}

func isMultipartSkip(path string) bool {
	lower := strings.ToLower(path)
	if !strings.Contains(lower, "-of-0") && !strings.Contains(lower, "-of-00") {
		return false
	}
	name := strings.ToLower(filepath.Base(path))
	return !strings.Contains(name, "00001") && !strings.Contains(name, "00000")
}

func listGGUFFiles(repo string, revision string) ([]map[string]any, error) {
	repoID, err := sanitizeHFRepo(repo)
	if err != nil {
		return nil, err
	}
	rev := sanitizeHFRevision(revision)
	items := make([]map[string]any, 0)
	cursor := ""
	for i := 0; i < 20; i++ {
		u := fmt.Sprintf("%s/api/models/%s/tree/%s?recursive=true", hfAPI, repoID, url.PathEscape(rev))
		if cursor != "" {
			u += "&cursor=" + url.QueryEscape(cursor)
		}
		data, headers, err := hfGetJSON(u, 45*time.Second)
		if err != nil {
			return nil, err
		}
		rows, _ := data.([]any)
		for _, row := range rows {
			m, ok := row.(map[string]any)
			if !ok {
				continue
			}
			if anyString(m["type"]) != "file" {
				continue
			}
			path := anyString(m["path"])
			if !strings.Contains(strings.ToLower(path), ".gguf") {
				continue
			}
			if isMultipartSkip(path) {
				continue
			}
			safe, err := sanitizeHFFilename(path)
			if err != nil {
				continue
			}
			size := anyInt(m["size"], 0)
			role := fileRole(safe)
			items = append(items, map[string]any{
				"path": safe, "name": filepath.Base(safe),
				"size": size, "size_gb": float64(int(float64(size)/(1024*1024*1024)*100+0.5)) / 100,
				"role": role, "recommended": false,
				"url": resolveHFURL(repoID, safe, rev),
			})
		}
		cursor = headers.Get("X-Next-Cursor")
		if cursor == "" || len(rows) == 0 {
			break
		}
	}
	sort.Slice(items, func(i, j int) bool {
		ki, si := quantKey(anyString(items[i]["name"]))
		kj, sj := quantKey(anyString(items[j]["name"]))
		if ki != kj {
			return ki < kj
		}
		return si < sj
	})
	for _, row := range items {
		if anyString(row["role"]) == "weights" {
			row["recommended"] = true
			break
		}
	}
	if len(items) > 0 {
		anyRec := false
		for _, row := range items {
			if anyBoolDef(row["recommended"], false) {
				anyRec = true
				break
			}
		}
		if !anyRec {
			items[0]["recommended"] = true
		}
	}
	return items, nil
}

func resolveQuery(query string) (map[string]any, error) {
	hint, err := parseHFHint(query)
	if err != nil {
		return nil, err
	}
	out := map[string]any{"ok": true, "hint": hint.public(), "repos": []any{}, "files": []any{}}
	switch hint.Kind {
	case "search":
		repos, err := searchGGUFRepos(hint.Query, 12)
		if err != nil {
			return map[string]any{"ok": false, "error": err.Error(), "repos": []any{}, "files": []any{}}, nil
		}
		out["repos"] = repos
		out["need"] = "repo"
	case "repo":
		files, err := listGGUFFiles(hint.Repo, hint.Revision)
		if err != nil {
			return map[string]any{"ok": false, "error": err.Error(), "files": []any{}}, nil
		}
		out["files"] = files
		out["repo"] = hint.Repo
		out["need"] = "file"
	case "url":
		out["need"] = "pull"
		out["repo"] = hint.Repo
		out["filename"] = hint.Filename
		out["url"] = hint.URL
	}
	return out, nil
}

func hfDestPath(home, repo, filename string) string {
	base := filepath.Base(filename)
	root := rmbModelsDir(home)
	if repo == "" {
		return filepath.Join(root, base)
	}
	safe, err := sanitizeHFRepo(repo)
	if err != nil {
		return filepath.Join(root, base)
	}
	slug := strings.ReplaceAll(safe, "/", "--")
	return filepath.Join(root, slug+"--"+base)
}

func (s *Server) startHFPull(home string, body map[string]any) map[string]any {
	repo := strings.TrimSpace(anyString(body["repo"]))
	filename := strings.TrimSpace(anyString(body["filename"]))
	revision := strings.TrimSpace(anyString(body["revision"]))
	rawURL := strings.TrimSpace(anyString(body["url"]))
	query := strings.TrimSpace(anyString(body["query"]))
	expected := anyInt(body["expected_size"], 0)
	raw := query
	if raw == "" {
		raw = rawURL
	}
	if raw != "" && (repo == "" || filename == "") {
		hint, err := parseHFHint(raw)
		if err != nil {
			return map[string]any{"ok": false, "error": err.Error()}
		}
		if repo == "" {
			repo = hint.Repo
		}
		if filename == "" {
			filename = hint.Filename
		}
		if revision == "" {
			revision = hint.Revision
		}
		if rawURL == "" {
			rawURL = hint.URL
		}
	}
	if filename == "" || (rawURL == "" && repo == "") {
		return map[string]any{"ok": false, "error": "Need a repo + filename or a Hugging Face file URL"}
	}
	if rawURL == "" {
		rawURL = resolveHFURL(repo, filename, revision)
	}
	u, err := url.Parse(rawURL)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") || !hfHostAllowed(u.Host) {
		return map[string]any{"ok": false, "error": "Only huggingface.co / hf.co URLs are allowed"}
	}
	s.hf.mu.Lock()
	if s.hf.pulling {
		s.hf.mu.Unlock()
		return map[string]any{"ok": true, "started": false, "progress": s.hf.snapshot()}
	}
	cancel := make(chan struct{})
	s.hf.cancel = cancel
	s.hf.pulling = true
	s.hf.mu.Unlock()
	s.hf.set(map[string]any{
		"phase": "downloading", "query": query, "repo": repo, "filename": filename,
		"bytes_done": 0, "bytes_total": expected, "error": nil, "path": nil,
		"message": "Downloading " + filepath.Base(filename),
	})
	dest := hfDestPath(home, repo, filename)
	go s.runHFDownload(rawURL, dest, expected, cancel)
	return map[string]any{"ok": true, "started": true, "progress": s.hf.snapshot()}
}

func (s *Server) runHFDownload(rawURL, dest string, expected int, cancel <-chan struct{}) {
	defer func() {
		s.hf.mu.Lock()
		s.hf.pulling = false
		s.hf.cancel = nil
		s.hf.mu.Unlock()
	}()
	_ = os.MkdirAll(filepath.Dir(dest), 0o700)
	if expected > 0 && expected > maxHFDownloadBytes {
		s.hf.set(map[string]any{"phase": "error", "error": "File exceeds pull cap", "message": "File exceeds pull cap"})
		return
	}
	if st, err := os.Stat(dest); err == nil && st.Size() > 64 {
		if expected == 0 || int(st.Size()) == expected {
			s.hf.set(map[string]any{
				"phase": "done", "bytes_done": int(st.Size()), "bytes_total": int(st.Size()),
				"path": dest, "message": "Already on disk", "error": nil,
			})
			return
		}
	}
	partial := dest + ".partial"
	existing := 0
	if st, err := os.Stat(partial); err == nil {
		existing = int(st.Size())
	}
	client := &http.Client{Timeout: 0}
	req, err := http.NewRequest(http.MethodGet, rawURL, nil)
	if err != nil {
		s.hf.set(map[string]any{"phase": "error", "error": err.Error(), "message": err.Error()})
		return
	}
	req.Header = hfAuthHeaders()
	mode := os.O_CREATE | os.O_WRONLY | os.O_TRUNC
	if existing > 0 {
		req.Header.Set("Range", fmt.Sprintf("bytes=%d-", existing))
		mode = os.O_CREATE | os.O_WRONLY | os.O_APPEND
	}
	resp, err := client.Do(req)
	if err != nil {
		s.hf.set(map[string]any{"phase": "error", "error": err.Error(), "message": err.Error()})
		return
	}
	defer resp.Body.Close()
	if existing > 0 && resp.StatusCode == 200 {
		_ = os.Remove(partial)
		existing = 0
		mode = os.O_CREATE | os.O_WRONLY | os.O_TRUNC
	}
	if resp.StatusCode >= 400 {
		s.hf.set(map[string]any{
			"phase": "error",
			"error": fmt.Sprintf("Download failed (HTTP %d)", resp.StatusCode),
			"message": fmt.Sprintf("Download failed (HTTP %d)", resp.StatusCode),
		})
		return
	}
	cl := anyInt(resp.Header.Get("Content-Length"), 0)
	total := existing + cl
	if expected > total {
		total = expected
	}
	s.hf.set(map[string]any{"bytes_total": total, "bytes_done": existing})
	f, err := os.OpenFile(partial, mode, 0o600)
	if err != nil {
		s.hf.set(map[string]any{"phase": "error", "error": err.Error(), "message": err.Error()})
		return
	}
	wrote := existing
	buf := make([]byte, 256*1024)
	for {
		select {
		case <-cancel:
			_ = f.Close()
			s.hf.set(map[string]any{"phase": "cancelled", "error": "Cancelled", "message": "Cancelled"})
			return
		default:
		}
		n, readErr := resp.Body.Read(buf)
		if n > 0 {
			if _, werr := f.Write(buf[:n]); werr != nil {
				_ = f.Close()
				s.hf.set(map[string]any{"phase": "error", "error": werr.Error(), "message": werr.Error()})
				return
			}
			wrote += n
			if wrote > maxHFDownloadBytes {
				_ = f.Close()
				s.hf.set(map[string]any{"phase": "error", "error": "exceeded pull cap", "message": "exceeded pull cap"})
				return
			}
			s.hf.set(map[string]any{"bytes_done": wrote})
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			_ = f.Close()
			s.hf.set(map[string]any{"phase": "error", "error": readErr.Error(), "message": readErr.Error()})
			return
		}
	}
	_ = f.Close()
	select {
	case <-cancel:
		s.hf.set(map[string]any{"phase": "cancelled", "error": "Cancelled", "message": "Cancelled"})
		return
	default:
	}
	if wrote < 64 {
		_ = os.Remove(partial)
		s.hf.set(map[string]any{"phase": "error", "error": "Download too small", "message": "Download too small"})
		return
	}
	if err := os.Rename(partial, dest); err != nil {
		s.hf.set(map[string]any{"phase": "error", "error": err.Error(), "message": err.Error()})
		return
	}
	s.hf.set(map[string]any{
		"phase": "done", "bytes_done": wrote, "bytes_total": wrote,
		"path": dest, "message": "Download complete", "error": nil,
	})
}

func (s *Server) handleRmbHfSearch(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Query string `json:"query"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	out, err := resolveQuery(body.Query)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error(), "repos": []any{}, "files": []any{}})
		return
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleRmbHfFiles(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Repo     string `json:"repo"`
		Revision string `json:"revision"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	repo, err := sanitizeHFRepo(body.Repo)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error(), "files": []any{}})
		return
	}
	files, err := listGGUFFiles(repo, body.Revision)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error(), "files": []any{}})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "repo": repo, "files": files})
}

func (s *Server) handleRmbHfPull(w http.ResponseWriter, r *http.Request) {
	var body map[string]any
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	writeJSON(w, http.StatusOK, s.startHFPull(ResolveHomeDir(s.homeDir), body))
}

func (s *Server) handleRmbHfProgress(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "progress": s.hf.snapshot()})
}

func (s *Server) handleRmbHfCancel(w http.ResponseWriter, _ *http.Request) {
	snap := s.hf.snapshot()
	phase := anyString(snap["phase"])
	if phase != "downloading" && phase != "loading" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "No download in progress"})
		return
	}
	s.hf.mu.Lock()
	if s.hf.cancel != nil {
		select {
		case <-s.hf.cancel:
		default:
			close(s.hf.cancel)
		}
	}
	s.hf.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "progress": s.hf.snapshot()})
}
