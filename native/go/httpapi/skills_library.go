package httpapi

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
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
	catalogPublicKeyB64 = "Zecma7eUlNtKbQSnTeBJC+v9nQEmJ5OT6YTYq27GsLc="
	libraryReleaseTag   = "v1.0.0"
	libraryRepo         = "AhmiDarrow/remedy-skills"
	catalogCacheMaxAge  = 24 * time.Hour
	maxCatalogBytes     = 8 * 1024 * 1024
	maxSkillZipBytes    = 50 * 1024 * 1024
	librarySuggestMinQ  = 8
	librarySuggestMinScore = 0.35
)

var (
	skillNameRE = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$`)
	chattyRE    = regexp.MustCompile(`(?i)^\s*(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure|cool|lol)\s*[.!]?\s*$`)

	suggestMu       sync.Mutex
	suggestSuppress = map[string]map[string]struct{}{}
)

func defaultCatalogURL() string {
	return fmt.Sprintf(
		"https://github.com/%s/releases/download/%s/catalog.json",
		libraryRepo, libraryReleaseTag,
	)
}

func defaultCatalogSigURL() string {
	return defaultCatalogURL() + ".sig"
}

type librarySkillEntry struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	Description    string   `json:"description"`
	Version        string   `json:"version"`
	Author         string   `json:"author"`
	AuthorURL      *string  `json:"author_url,omitempty"`
	Tags           []string `json:"tags"`
	DownloadURL    string   `json:"download_url"`
	SizeBytes      int      `json:"size_bytes"`
	Checksum       string   `json:"checksum"`
	Requires       []string `json:"requires"`
	Tools          []string `json:"tools"`
	Rating         float64  `json:"rating"`
	Installs       int      `json:"installs"`
	ReviewsCount   int      `json:"reviews_count"`
	UpdatedAt      string   `json:"updated_at"`
	PublishedAt    string   `json:"published_at"`
	Compatible     []string `json:"compatible_remedy"`
	SecurityFlags  []string `json:"security_flags"`
	Status         string   `json:"status"`
}

type skillsCatalog struct {
	Version     string              `json:"version"`
	GeneratedAt string              `json:"generated_at"`
	Repository  string              `json:"repository"`
	Skills      []librarySkillEntry `json:"skills"`
	Source      string              `json:"source"`
}

func skillsDevMode() bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_SKILLS_DEV")))
	switch v {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func catalogCacheDir(home string) string {
	return filepath.Join(home, "cache", "skills")
}

func verifyCatalogSignature(data []byte, sigB64, pubB64 string) error {
	pubB64 = strings.TrimSpace(pubB64)
	if pubB64 == "" {
		pubB64 = catalogPublicKeyB64
	}
	pub, err := base64.StdEncoding.DecodeString(pubB64)
	if err != nil {
		return fmt.Errorf("invalid catalog public key: %w", err)
	}
	if len(pub) != ed25519.PublicKeySize {
		return errors.New("invalid catalog public key length")
	}
	sig, err := base64.StdEncoding.DecodeString(strings.TrimSpace(sigB64))
	if err != nil {
		return fmt.Errorf("invalid catalog signature encoding: %w", err)
	}
	if !ed25519.Verify(ed25519.PublicKey(pub), data, sig) {
		return errors.New("catalog signature verification failed")
	}
	return nil
}

func isAllowedCatalogURL(raw string) bool {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme != "https" {
		return false
	}
	host := strings.ToLower(u.Hostname())
	if host != "github.com" && host != "www.github.com" {
		return false
	}
	prefix := "/" + libraryRepo + "/releases/download/"
	path := u.Path
	if !strings.HasPrefix(path, prefix) {
		return false
	}
	rest := strings.TrimPrefix(path, prefix)
	parts := strings.Split(strings.Trim(rest, "/"), "/")
	return len(parts) == 2 && parts[0] != "" && parts[1] != "" &&
		!strings.Contains(parts[0], "..") && !strings.Contains(parts[1], "..")
}

func isAllowedDownloadURL(raw string, allowCDN bool) bool {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return false
	}
	if strings.HasPrefix(raw, "local:") {
		return isSafeSkillName(strings.TrimSpace(raw[6:]))
	}
	u, err := url.Parse(raw)
	if err != nil || !strings.EqualFold(u.Scheme, "https") {
		return false
	}
	host := strings.ToLower(u.Hostname())
	switch host {
	case "github.com", "www.github.com":
		return isAllowedCatalogURL(raw)
	case "objects.githubusercontent.com", "release-assets.githubusercontent.com":
		if !allowCDN {
			return false
		}
		p := u.Path
		if strings.Contains(p, "..") || p == "" {
			return false
		}
		cleaned := strings.Trim(p, "/")
		return cleaned != "" && len(cleaned) <= 500 &&
			(strings.Contains(cleaned, "github-production-release-asset") || strings.Contains(cleaned, "release"))
	default:
		return false
	}
}

func isSafeSkillName(name string) bool {
	n := strings.TrimSpace(name)
	if n == "" || strings.ContainsAny(n, `/\`) || strings.Contains(n, "..") {
		return false
	}
	return skillNameRE.MatchString(n)
}

func httpGetBytes(rawURL string, maxBytes int, timeout time.Duration) ([]byte, string, error) {
	client := &http.Client{Timeout: timeout}
	resp, err := client.Get(rawURL)
	if err != nil {
		return nil, "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, "", fmt.Errorf("HTTP %d for %s", resp.StatusCode, rawURL)
	}
	final := rawURL
	if resp.Request != nil && resp.Request.URL != nil {
		final = resp.Request.URL.String()
	}
	limited := io.LimitReader(resp.Body, int64(maxBytes)+1)
	data, err := io.ReadAll(limited)
	if err != nil {
		return nil, "", err
	}
	if len(data) > maxBytes {
		return nil, "", fmt.Errorf("response exceeds size limit (%d bytes)", maxBytes)
	}
	return data, final, nil
}

func loadCatalogFromBytes(data []byte, source string) (skillsCatalog, error) {
	var cat skillsCatalog
	if err := json.Unmarshal(data, &cat); err != nil {
		return skillsCatalog{}, err
	}
	cat.Source = source
	if cat.Skills == nil {
		cat.Skills = []librarySkillEntry{}
	}
	return cat, nil
}

func (s *Server) getSkillsCatalog(refresh bool) (skillsCatalog, error) {
	home := s.skillsHome()
	cacheDir := catalogCacheDir(home)
	cacheFile := filepath.Join(cacheDir, "catalog.json")
	cacheSig := filepath.Join(cacheDir, "catalog.json.sig")

	pub := catalogPublicKeyB64
	if env := strings.TrimSpace(os.Getenv("REMEDY_SKILLS_CATALOG_PUBKEY")); env != "" {
		if env != catalogPublicKeyB64 && !skillsDevMode() {
			pub = catalogPublicKeyB64
		} else {
			pub = env
		}
	}

	if !refresh && fileExists(cacheFile) && fileExists(cacheSig) {
		st, err := os.Stat(cacheFile)
		if err == nil && time.Since(st.ModTime()) < catalogCacheMaxAge {
			data, err := os.ReadFile(cacheFile)
			if err == nil {
				sig, err := os.ReadFile(cacheSig)
				if err == nil && verifyCatalogSignature(data, string(sig), pub) == nil {
					if cat, err := loadCatalogFromBytes(data, "cache"); err == nil {
						return cat, nil
					}
				}
			}
		}
	}

	catalogURL := strings.TrimSpace(os.Getenv("REMEDY_SKILLS_CATALOG_URL"))
	if catalogURL == "" {
		catalogURL = defaultCatalogURL()
	}
	sigURL := strings.TrimSpace(os.Getenv("REMEDY_SKILLS_CATALOG_SIG_URL"))
	if sigURL == "" {
		sigURL = defaultCatalogSigURL()
	}

	dev := skillsDevMode()
	if !dev && !(isAllowedCatalogURL(catalogURL) && isAllowedCatalogURL(sigURL)) {
		return skillsCatalog{}, errors.New("skills catalog URL not allowlisted; set REMEDY_SKILLS_DEV=1 for dogfood")
	}

	data, finalURL, err := httpGetBytes(catalogURL, maxCatalogBytes, 15*time.Second)
	if err == nil {
		if !dev && !isAllowedCatalogURL(finalURL) && !isAllowedDownloadURL(finalURL, true) {
			err = fmt.Errorf("catalog final URL not allowlisted: %s", trimURL(finalURL))
		}
	}
	if err == nil {
		sigBytes, _, sigErr := httpGetBytes(sigURL, 4096, 15*time.Second)
		if sigErr != nil {
			err = sigErr
		} else if vErr := verifyCatalogSignature(data, string(sigBytes), pub); vErr != nil {
			err = vErr
		} else {
			_ = os.MkdirAll(cacheDir, 0o700)
			tmpC := cacheFile + ".tmp"
			tmpS := cacheSig + ".tmp"
			if wErr := os.WriteFile(tmpC, data, 0o600); wErr == nil {
				if wErr = os.WriteFile(tmpS, append([]byte(strings.TrimSpace(string(sigBytes))), '\n'), 0o600); wErr == nil {
					_ = os.Rename(tmpC, cacheFile)
					_ = os.Rename(tmpS, cacheSig)
				}
			}
			return loadCatalogFromBytes(data, "remote")
		}
	}

	// Fall back to cache even if stale when remote fails.
	if fileExists(cacheFile) && fileExists(cacheSig) {
		data, rErr := os.ReadFile(cacheFile)
		sig, sErr := os.ReadFile(cacheSig)
		if rErr == nil && sErr == nil && verifyCatalogSignature(data, string(sig), pub) == nil {
			if cat, cErr := loadCatalogFromBytes(data, "cache"); cErr == nil {
				return cat, nil
			}
		}
	}
	if err != nil {
		return skillsCatalog{}, fmt.Errorf("could not load Skills Library catalog: %w", err)
	}
	return skillsCatalog{}, errors.New("could not load Skills Library catalog")
}

func trimURL(u string) string {
	if len(u) > 160 {
		return u[:160]
	}
	return u
}

func (s *Server) getSkillsCatalogCached() (skillsCatalog, bool) {
	home := s.skillsHome()
	cacheFile := filepath.Join(catalogCacheDir(home), "catalog.json")
	cacheSig := filepath.Join(catalogCacheDir(home), "catalog.json.sig")
	pub := catalogPublicKeyB64
	if env := strings.TrimSpace(os.Getenv("REMEDY_SKILLS_CATALOG_PUBKEY")); env != "" && (env == catalogPublicKeyB64 || skillsDevMode()) {
		pub = env
	}
	needs := true
	if fileExists(cacheFile) && fileExists(cacheSig) {
		data, err := os.ReadFile(cacheFile)
		sig, sErr := os.ReadFile(cacheSig)
		if err == nil && sErr == nil && verifyCatalogSignature(data, string(sig), pub) == nil {
			if cat, cErr := loadCatalogFromBytes(data, "cache"); cErr == nil {
				st, stErr := os.Stat(cacheFile)
				if stErr == nil && time.Since(st.ModTime()) < catalogCacheMaxAge {
					needs = false
				}
				return cat, needs
			}
		}
	}
	return skillsCatalog{Skills: []librarySkillEntry{}, Source: "none"}, true
}

func searchLibraryCatalog(cat skillsCatalog, q, tagsCSV, author, sortBy string) []librarySkillEntry {
	results := append([]librarySkillEntry(nil), cat.Skills...)
	if q = strings.TrimSpace(q); q != "" {
		ql := strings.ToLower(q)
		filtered := results[:0]
		for _, s := range results {
			if strings.Contains(strings.ToLower(s.Name), ql) ||
				strings.Contains(strings.ToLower(s.Description), ql) {
				filtered = append(filtered, s)
				continue
			}
			for _, t := range s.Tags {
				if strings.Contains(strings.ToLower(t), ql) {
					filtered = append(filtered, s)
					break
				}
			}
		}
		results = filtered
	}
	if tagsCSV = strings.TrimSpace(tagsCSV); tagsCSV != "" {
		want := map[string]struct{}{}
		for _, t := range strings.Split(tagsCSV, ",") {
			t = strings.ToLower(strings.TrimSpace(t))
			if t != "" {
				want[t] = struct{}{}
			}
		}
		if len(want) > 0 {
			filtered := results[:0]
			for _, s := range results {
				for _, t := range s.Tags {
					if _, ok := want[strings.ToLower(t)]; ok {
						filtered = append(filtered, s)
						break
					}
				}
			}
			results = filtered
		}
	}
	if author = strings.TrimSpace(author); author != "" {
		al := strings.ToLower(author)
		filtered := results[:0]
		for _, s := range results {
			if strings.ToLower(s.Author) == al {
				filtered = append(filtered, s)
			}
		}
		results = filtered
	}
	switch sortBy {
	case "rating":
		sort.SliceStable(results, func(i, j int) bool { return results[i].Rating > results[j].Rating })
	case "updated_at":
		sort.SliceStable(results, func(i, j int) bool { return results[i].UpdatedAt > results[j].UpdatedAt })
	case "installs":
		sort.SliceStable(results, func(i, j int) bool { return results[i].Installs > results[j].Installs })
	default:
		sort.SliceStable(results, func(i, j int) bool {
			return strings.ToLower(results[i].Name) < strings.ToLower(results[j].Name)
		})
	}
	return results
}

func (s *Server) handleLibraryCatalog(w http.ResponseWriter, r *http.Request) {
	refresh := strings.EqualFold(r.URL.Query().Get("refresh"), "true") || r.URL.Query().Get("refresh") == "1"
	cat, err := s.getSkillsCatalog(refresh)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, cat)
}

func (s *Server) handleLibrarySearch(w http.ResponseWriter, r *http.Request) {
	cat, err := s.getSkillsCatalog(false)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"detail": err.Error()})
		return
	}
	q := r.URL.Query().Get("q")
	results := searchLibraryCatalog(cat, q, r.URL.Query().Get("tags"), r.URL.Query().Get("author"), r.URL.Query().Get("sort_by"))
	writeJSON(w, http.StatusOK, map[string]any{
		"query":   q,
		"total":   len(results),
		"results": results,
		"source":  cat.Source,
	})
}

type libraryHit struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Score       float64  `json:"score"`
	Version     string   `json:"version"`
	Tags        []string `json:"tags"`
	Reason      string   `json:"reason"`
}

func suppressLibrarySuggest(sessionID, skillID string) {
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = "_default"
	}
	kid := strings.TrimSpace(skillID)
	if kid == "" {
		return
	}
	suggestMu.Lock()
	defer suggestMu.Unlock()
	if suggestSuppress[sid] == nil {
		suggestSuppress[sid] = map[string]struct{}{}
	}
	suggestSuppress[sid][kid] = struct{}{}
}

func isLibrarySuggestSuppressed(sessionID, skillID string) bool {
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = "_default"
	}
	suggestMu.Lock()
	defer suggestMu.Unlock()
	_, ok := suggestSuppress[sid][skillID]
	return ok
}

func rankLibrarySkills(cat skillsCatalog, query string, installed map[string]struct{}, sessionID string, limit int) []libraryHit {
	q := strings.TrimSpace(query)
	if len(q) < librarySuggestMinQ || chattyRE.MatchString(q) {
		return nil
	}
	qTokens := tokenizeSkillText(q)
	if len(qTokens) == 0 {
		return nil
	}
	ql := strings.ToLower(q)
	var scored []libraryHit
	for _, e := range cat.Skills {
		st := strings.ToLower(strings.TrimSpace(e.Status))
		if st != "" && st != "published" && st != "active" {
			continue
		}
		name := strings.TrimSpace(e.Name)
		if name == "" {
			name = strings.TrimSpace(e.ID)
		}
		if name == "" {
			continue
		}
		if _, ok := installed[strings.ToLower(name)]; ok {
			continue
		}
		if _, ok := installed[strings.ToLower(e.ID)]; ok {
			continue
		}
		if sessionID != "" && (isLibrarySuggestSuppressed(sessionID, e.ID) || isLibrarySuggestSuppressed(sessionID, name)) {
			continue
		}
		nameTokens := tokenizeSkillText(strings.ReplaceAll(strings.ReplaceAll(name, "-", " "), "_", " "))
		descTokens := tokenizeSkillText(e.Description)
		tagTokens := tokenizeSkillText(strings.Join(e.Tags, " "))
		toolTokens := tokenizeSkillText(strings.Join(e.Tools, " "))
		all := map[string]struct{}{}
		for _, src := range []map[string]struct{}{nameTokens, descTokens, tagTokens, toolTokens} {
			for t := range src {
				all[t] = struct{}{}
			}
		}
		overlap, nameHit, tagHit := 0, 0, 0
		for t := range qTokens {
			if _, ok := all[t]; ok {
				overlap++
			}
			if _, ok := nameTokens[t]; ok {
				nameHit++
			}
			if _, ok := tagTokens[t]; ok {
				tagHit++
			}
			if _, ok := toolTokens[t]; ok {
				tagHit++
			}
		}
		if nameHit == 0 && tagHit == 0 && overlap < 2 {
			compact := strings.ReplaceAll(strings.ToLower(name), "-", "")
			okSub := strings.Contains(strings.ToLower(name), ql)
			if !okSub {
				for t := range qTokens {
					if len(t) >= 4 && strings.Contains(compact, t) {
						okSub = true
						break
					}
				}
			}
			if !okSub {
				continue
			}
		}
		substr := 0.0
		if ql != "" && strings.Contains(strings.ToLower(name), ql) {
			substr += 0.55
		}
		if ql != "" && strings.Contains(strings.ToLower(e.Description), ql) {
			substr += 0.2
		}
		for t := range qTokens {
			if len(t) >= 4 && strings.Contains(strings.ReplaceAll(strings.ToLower(name), "-", ""), t) {
				substr += 0.08
				break
			}
		}
		textScore := 0.40*minFloat(1, float64(overlap)/float64(maxInt(1, len(qTokens)))) +
			0.35*minFloat(1, float64(nameHit)/float64(maxInt(1, minInt(3, len(qTokens))))) +
			0.15*minFloat(1, float64(tagHit)/float64(maxInt(1, minInt(3, len(qTokens))))) +
			0.10*minFloat(1, substr)
		if textScore < librarySuggestMinScore {
			continue
		}
		reasons := []string{}
		if nameHit > 0 {
			reasons = append(reasons, "name match")
		}
		if tagHit > 0 {
			reasons = append(reasons, "tags")
		}
		if overlap >= 2 {
			reasons = append(reasons, "description overlap")
		}
		reason := strings.Join(reasons, ", ")
		if reason == "" {
			reason = "relevant"
		}
		tags := e.Tags
		if len(tags) > 8 {
			tags = tags[:8]
		}
		desc := e.Description
		if len(desc) > 200 {
			desc = desc[:200]
		}
		scored = append(scored, libraryHit{
			ID:          e.ID,
			Name:        name,
			Description: desc,
			Score:       float64(int(textScore*10000+0.5)) / 10000,
			Version:     e.Version,
			Tags:        tags,
			Reason:      reason,
		})
	}
	sort.SliceStable(scored, func(i, j int) bool {
		if scored[i].Score != scored[j].Score {
			return scored[i].Score > scored[j].Score
		}
		return strings.ToLower(scored[i].Name) < strings.ToLower(scored[j].Name)
	})
	if limit < 1 {
		limit = 5
	}
	if limit > 20 {
		limit = 20
	}
	if len(scored) > limit {
		scored = scored[:limit]
	}
	return scored
}

func minFloat(a, b float64) float64 {
	if a < b {
		return a
	}
	return b
}
func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func (s *Server) installedSkillNames() map[string]struct{} {
	out := map[string]struct{}{}
	for _, rec := range s.discoverSkills() {
		out[strings.ToLower(rec.Name)] = struct{}{}
	}
	return out
}

func (s *Server) handleLibrarySuggest(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	sessionID := r.URL.Query().Get("session_id")
	mark := r.URL.Query().Get("mark") == "1" || strings.EqualFold(r.URL.Query().Get("mark"), "true")

	cat, needs := s.getSkillsCatalogCached()
	installed := s.installedSkillNames()
	idxSize := len(cat.Skills)

	if mark {
		hits := rankLibrarySkills(cat, q, installed, sessionID, 1)
		var suggestion any
		if len(hits) > 0 {
			suggestion = hits[0]
			if sessionID != "" {
				suppressLibrarySuggest(sessionID, hits[0].ID)
			}
		} else {
			suggestion = nil
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"query":         q,
			"suggestion":    suggestion,
			"source":        cat.Source,
			"index_size":    idxSize,
			"needs_refresh": needs || cat.Source == "none",
		})
		return
	}

	hits := rankLibrarySkills(cat, q, installed, sessionID, 5)
	var suggestion any
	if len(hits) > 0 {
		suggestion = hits[0]
	} else {
		suggestion = nil
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"query":         q,
		"suggestion":    suggestion,
		"results":       hits,
		"source":        cat.Source,
		"index_size":    idxSize,
		"needs_refresh": needs || cat.Source == "none",
	})
}

func (s *Server) handleLibrarySuggestDismiss(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SkillID   string `json:"skill_id"`
		SessionID string `json:"session_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	suppressLibrarySuggest(body.SessionID, body.SkillID)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "skill_id": body.SkillID})
}

func compareVersions(a, b string) int {
	pa := versionParts(a)
	pb := versionParts(b)
	n := len(pa)
	if len(pb) > n {
		n = len(pb)
	}
	for i := 0; i < n; i++ {
		var x, y int
		if i < len(pa) {
			x = pa[i]
		}
		if i < len(pb) {
			y = pb[i]
		}
		if x < y {
			return -1
		}
		if x > y {
			return 1
		}
	}
	return 0
}

func versionParts(v string) []int {
	var out []int
	cur := ""
	for _, r := range v {
		if r >= '0' && r <= '9' {
			cur += string(r)
			continue
		}
		if cur != "" {
			n := 0
			fmt.Sscanf(cur, "%d", &n)
			out = append(out, n)
			cur = ""
		}
	}
	if cur != "" {
		n := 0
		fmt.Sscanf(cur, "%d", &n)
		out = append(out, n)
	}
	if len(out) == 0 {
		return []int{0}
	}
	return out
}

func (s *Server) handleLibraryUpdates(w http.ResponseWriter, _ *http.Request) {
	cat, err := s.getSkillsCatalog(false)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"detail": err.Error()})
		return
	}
	byID := map[string]librarySkillEntry{}
	for _, e := range cat.Skills {
		byID[e.ID] = e
		byID[e.Name] = e
	}
	var updates []map[string]string
	for _, rec := range s.discoverSkills() {
		if rec.Source != "library" && rec.LibraryID == "" {
			continue
		}
		lid := rec.LibraryID
		if lid == "" {
			lid = rec.Name
		}
		remote, ok := byID[lid]
		if !ok {
			remote, ok = byID[rec.Name]
		}
		if !ok {
			continue
		}
		if compareVersions(remote.Version, rec.Version) > 0 {
			updates = append(updates, map[string]string{
				"skill_id":          remote.ID,
				"name":              rec.Name,
				"current_version":   rec.Version,
				"available_version": remote.Version,
			})
		}
	}
	if updates == nil {
		updates = []map[string]string{}
	}
	writeJSON(w, http.StatusOK, map[string]any{"updates": updates})
}

func (s *Server) handleLibraryInstall(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SkillID string  `json:"skill_id"`
		Version *string `json:"version"`
		Force   bool    `json:"force"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	skillID := strings.TrimSpace(body.SkillID)
	if skillID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "skill_id required"})
		return
	}
	cat, err := s.getSkillsCatalog(false)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"detail": err.Error()})
		return
	}
	var entry *librarySkillEntry
	for i := range cat.Skills {
		e := &cat.Skills[i]
		if e.ID == skillID || e.Name == skillID {
			entry = e
			break
		}
	}
	if entry == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found in catalog: " + skillID})
		return
	}
	if body.Version != nil && strings.TrimSpace(*body.Version) != "" && *body.Version != entry.Version {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"detail": fmt.Sprintf("Version %s not available (catalog has %s)", *body.Version, entry.Version),
		})
		return
	}
	if !isSafeSkillName(entry.Name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Catalog skill has unsafe name"})
		return
	}
	for _, root := range skillSeedRoots(s.skillsHome()) {
		if dirExists(filepath.Join(root, entry.Name)) {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"detail": fmt.Sprintf("Skill name '%s' conflicts with a bundled skill and cannot be installed from the library.", entry.Name),
			})
			return
		}
	}

	destRoot := filepath.Join(s.skillsHome(), "skills")
	if err := os.MkdirAll(destRoot, 0o700); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	dest := filepath.Join(destRoot, entry.Name)
	if dirExists(dest) && !body.Force {
		writeJSON(w, http.StatusConflict, map[string]string{
			"detail": fmt.Sprintf("Skill '%s' already installed. Use force/update to replace.", entry.Name),
		})
		return
	}

	zipData, err := downloadLibrarySkillZip(*entry)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{"detail": err.Error()})
		return
	}
	names, err := importSkillZipQuarantine(zipData, destRoot, entry, body.Force)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	msg := "Installed in quarantine. Trust the skill in Skills → Installed to activate."
	if body.Force {
		msg = "Updated and re-quarantined. Trust again after reviewing changes."
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":         "installed",
		"skill_id":       entry.ID,
		"names":          names,
		"version":        entry.Version,
		"quarantine":     true,
		"security_flags": entry.SecurityFlags,
		"replaced":       body.Force,
		"message":        msg,
	})
}

func downloadLibrarySkillZip(entry librarySkillEntry) ([]byte, error) {
	url := strings.TrimSpace(entry.DownloadURL)
	if !isAllowedDownloadURL(url, false) {
		return nil, fmt.Errorf("download URL not allowed: %s", url)
	}
	if strings.HasPrefix(url, "local:") {
		return nil, errors.New("local: catalog entries require monorepo community pack (not available)")
	}
	capBytes := maxSkillZipBytes
	if entry.SizeBytes > 0 {
		slack := entry.SizeBytes * 2
		if entry.SizeBytes+1024 > slack {
			slack = entry.SizeBytes + 1024
		}
		if slack < capBytes {
			capBytes = slack
		}
	}
	data, final, err := httpGetBytes(url, capBytes, 60*time.Second)
	if err != nil {
		return nil, err
	}
	if !isAllowedDownloadURL(final, true) {
		return nil, fmt.Errorf("download final URL not allowed: %s", trimURL(final))
	}
	if err := verifySHA256Checksum(data, entry.Checksum); err != nil {
		return nil, err
	}
	return data, nil
}

func verifySHA256Checksum(data []byte, checksum string) error {
	checksum = strings.TrimSpace(checksum)
	algo, expected, ok := strings.Cut(checksum, ":")
	if !ok || expected == "" {
		return errors.New("missing checksum")
	}
	if !strings.EqualFold(algo, "sha256") {
		return fmt.Errorf("unsupported checksum algorithm: %s", algo)
	}
	sum := sha256.Sum256(data)
	actual := hex.EncodeToString(sum[:])
	if !strings.EqualFold(actual, expected) {
		return errors.New("checksum mismatch — download corrupted or tampered")
	}
	return nil
}
