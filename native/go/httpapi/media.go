package httpapi

import (
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

const maxMediaBytes = 25 * 1024 * 1024

var mediaTypes = map[string]string{
	".png":  "image/png",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".gif":  "image/gif",
	".webp": "image/webp",
	".bmp":  "image/bmp",
	".ico":  "image/x-icon",
}

func normalizeMediaPathQuery(raw string) string {
	s := strings.TrimSpace(raw)
	s = strings.Trim(s, `"'<>`)
	lower := strings.ToLower(s)
	if strings.HasPrefix(lower, "file:") {
		s = s[5:]
		s = strings.TrimLeft(s, `/\`)
		// file:///C:/Users/... → C:/Users/...
		if len(s) >= 2 && s[1] == ':' {
			// already drive-form
		} else if strings.HasPrefix(s, "/") && len(s) >= 3 && s[2] == ':' {
			s = s[1:]
		}
	}
	return strings.TrimSpace(s)
}

func (s *Server) mediaAllowedRoots() []string {
	roots := make([]string, 0, 6)
	seen := map[string]struct{}{}
	add := func(p string) {
		p = strings.TrimSpace(p)
		if p == "" {
			return
		}
		abs, err := resolveAbsPath(p)
		if err != nil || abs == "" {
			return
		}
		key := strings.ToLower(abs)
		if _, ok := seen[key]; ok {
			return
		}
		seen[key] = struct{}{}
		roots = append(roots, abs)
	}

	base, _ := s.resolveFilesBase("")
	add(base)
	add(s.remedyHomeDir())
	add(ResolveHomeDir(s.homeDir))
	if uh, err := os.UserHomeDir(); err == nil && uh != "" {
		add(filepath.Join(uh, ".remedy"))
	}
	if raw := s.configProjectRaw(); !isUnsetProjectPath(raw) {
		add(raw)
	}
	return roots
}

func (s *Server) resolveMediaCandidate(raw string) (string, error) {
	raw = normalizeMediaPathQuery(raw)
	if raw == "" {
		return "", errPathOutside
	}
	isAbs := filepath.IsAbs(raw) || (len(raw) >= 2 && raw[1] == ':' && isDriveLetter(raw[0]))
	if isAbs {
		return resolveAbsPath(raw)
	}

	base, _ := s.resolveFilesBase("")
	cand := filepath.Join(base, raw)
	if abs, err := resolveAbsPath(cand); err == nil {
		if st, err := os.Stat(abs); err == nil && !st.IsDir() {
			return abs, nil
		}
	}
	home := s.remedyHomeDir()
	if home != "" {
		homeCand := filepath.Join(home, raw)
		if abs, err := resolveAbsPath(homeCand); err == nil {
			if st, err := os.Stat(abs); err == nil && !st.IsDir() {
				return abs, nil
			}
		}
	}

	// Bare image filename → newest match under attachments/
	slashFree := !strings.Contains(raw, "/") && !strings.Contains(raw, `\`)
	lower := strings.ToLower(raw)
	if slashFree && hasImageSuffix(lower) && home != "" {
		att := filepath.Join(home, "attachments")
		var best string
		var bestMod int64
		_ = filepath.WalkDir(att, func(path string, d os.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return nil
			}
			if !strings.EqualFold(d.Name(), filepath.Base(raw)) {
				return nil
			}
			info, err := d.Info()
			if err != nil {
				return nil
			}
			mod := info.ModTime().UnixNano()
			if best == "" || mod > bestMod {
				if abs, err := resolveAbsPath(path); err == nil {
					best = abs
					bestMod = mod
				}
			}
			return nil
		})
		if best != "" {
			return best, nil
		}
	}
	return resolveAbsPath(filepath.Join(base, raw))
}

func hasImageSuffix(lowerName string) bool {
	for ext := range mediaTypes {
		if strings.HasSuffix(lowerName, ext) {
			return true
		}
	}
	return false
}

func mediaUnderRoots(candidate string, roots []string) bool {
	for _, root := range roots {
		if pathUnder(candidate, root) {
			return true
		}
	}
	return false
}

func (s *Server) handleServeMedia(w http.ResponseWriter, r *http.Request) {
	raw := r.URL.Query().Get("path")
	if strings.TrimSpace(raw) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "path required"})
		return
	}
	candidate, err := s.resolveMediaCandidate(raw)
	if err != nil || candidate == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid path"})
		return
	}
	if isProtectedSecretPath(candidate, s.remedyHomeDir()) {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"detail": "path is a protected Remedy secrets location",
		})
		return
	}
	st, err := os.Stat(candidate)
	if err != nil || st.IsDir() {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "media not found"})
		return
	}
	if !mediaUnderRoots(candidate, s.mediaAllowedRoots()) {
		writeJSON(w, http.StatusForbidden, map[string]string{"detail": "path outside allowed roots"})
		return
	}
	suffix := strings.ToLower(filepath.Ext(candidate))
	if suffix == ".svg" {
		writeJSON(w, http.StatusUnsupportedMediaType, map[string]string{
			"detail": "SVG is not served as chat media (scriptable)",
		})
		return
	}
	if st.Size() > maxMediaBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{
			"detail": "media too large (25 MB max)",
		})
		return
	}
	ct, ok := mediaTypes[suffix]
	if !ok {
		// No Pillow port — refuse unknown types fail-closed (Python converts via Pillow).
		writeJSON(w, http.StatusUnsupportedMediaType, map[string]string{
			"detail": "unsupported media type: " + suffix,
		})
		return
	}

	f, err := os.Open(candidate)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "cannot read"})
		return
	}
	defer f.Close()

	w.Header().Set("Content-Type", ct)
	w.Header().Set("Cache-Control", "private, max-age=120")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Disposition", `inline; filename="`+filepath.Base(candidate)+`"`)
	w.WriteHeader(http.StatusOK)
	_, _ = io.Copy(w, f)
}
