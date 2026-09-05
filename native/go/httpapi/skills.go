package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func (s *Server) handleListSkills(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query().Get("q")
	limit := 200
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	skills := filterSkills(s.discoverSkills(), q, limit)
	out := make([]map[string]any, 0, len(skills))
	for _, rec := range skills {
		out = append(out, skillToPublic(rec))
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleGetSkill(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PathValue("name"))
	if name == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "skill name required"})
		return
	}
	// Reserved static segments registered as separate routes; belt-and-suspenders.
	switch strings.ToLower(name) {
	case "packs", "library", "export", "import", "archive-unused", "metrics", "learning":
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "not found"})
		return
	}
	rec, ok := s.findSkill(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found: " + name})
		return
	}
	body := skillToPublic(rec)
	preview := rec.Body
	if len(preview) > 2000 {
		preview = preview[:2000]
	}
	body["instructions_preview"] = preview
	body["body"] = rec.Body
	body["scripts"] = listSkillSubfiles(rec.Dir, "scripts")
	body["references"] = listSkillSubfiles(rec.Dir, "references")
	writeJSON(w, http.StatusOK, body)
}

// handleDeleteSkill removes a user-installed skill under ~/.remedy/skills/.
// Bundled / seed skills cannot be deleted this way (Python parity).
func (s *Server) handleDeleteSkill(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PathValue("name"))
	if name == "" || !isSafeSkillName(name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid skill name"})
		return
	}
	switch strings.ToLower(name) {
	case "packs", "library", "export", "import", "archive-unused", "metrics", "learning":
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "not found"})
		return
	}
	rec, ok := s.findSkill(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found: " + name})
		return
	}

	userRoot, err := filepath.Abs(filepath.Join(s.skillsHome(), "skills"))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	canonical := filepath.Join(userRoot, name)
	target := rec.Dir
	if target == "" {
		target = canonical
	}
	absTarget, err := filepath.Abs(target)
	if err != nil {
		absTarget = canonical
	}
	if !pathUnder(absTarget, userRoot) || filepath.Base(absTarget) != name || absTarget == userRoot {
		// Metadata path outside user tree — only allow canonical user dir.
		if !dirExists(canonical) {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"detail": "Cannot delete bundled or non-user skills. " +
					"Archive or quarantine them instead. " +
					"Only skills under ~/.remedy/skills/ can be removed.",
			})
			return
		}
		absTarget = canonical
	}

	purge := true
	if raw := strings.TrimSpace(r.URL.Query().Get("purge")); raw != "" {
		purge = truthyYAML(raw)
	}
	removedFiles := false
	if purge && dirExists(absTarget) {
		if err := os.RemoveAll(absTarget); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
			return
		}
		removedFiles = !dirExists(absTarget)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"name":          name,
		"status":        "deleted",
		"removed_files": removedFiles,
		"path":          absTarget,
	})
}

func listSkillSubfiles(skillDir, sub string) []string {
	if skillDir == "" {
		return []string{}
	}
	root := filepath.Join(skillDir, sub)
	if !dirExists(root) {
		return []string{}
	}
	var out []string
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() {
			return nil
		}
		rel, err := filepath.Rel(skillDir, path)
		if err != nil {
			return nil
		}
		out = append(out, filepath.ToSlash(rel))
		return nil
	})
	return out
}
