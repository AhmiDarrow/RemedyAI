package httpapi

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

var validSkillStatuses = map[string]struct{}{
	"discovered": {}, "validated": {}, "active": {},
	"disabled": {}, "deprecated": {}, "archived": {},
}

func (s *Server) skillPacksPath() string {
	return filepath.Join(s.skillsHome(), "skill_packs.json")
}

func (s *Server) skillStatsPath() string {
	return filepath.Join(s.skillsHome(), "skill_stats.json")
}

func (s *Server) loadSkillPacks() map[string]any {
	path := s.skillPacksPath()
	var data map[string]any
	if err := readJSONFile(path, &data); err != nil || data == nil {
		return map[string]any{"packs": map[string]any{}, "enabled": []any{}}
	}
	packs, _ := data["packs"].(map[string]any)
	if packs == nil {
		packs = map[string]any{}
	}
	enabled, _ := data["enabled"].([]any)
	if enabled == nil {
		enabled = []any{}
	}
	return map[string]any{"packs": packs, "enabled": enabled}
}

func (s *Server) saveSkillPacks(data map[string]any) (string, error) {
	path := s.skillPacksPath()
	if err := writeJSONAtomic(path, data); err != nil {
		return "", err
	}
	return path, nil
}

func (s *Server) handleGetSkillPacks(w http.ResponseWriter, r *http.Request) {
	data := s.loadSkillPacks()
	budget := 80
	cfg := LoadConfig(s.homeDir)
	if b := cfgInt(cfg, "skills_active_budget", 80); b > 0 {
		budget = b
	}
	activeCount := 0
	for _, rec := range s.discoverSkills() {
		st := strings.ToLower(rec.Status)
		if st == "archived" || st == "disabled" || st == "deprecated" {
			continue
		}
		if rec.Quarantine {
			continue
		}
		activeCount++
	}
	var banner any
	if activeCount >= int(float64(budget)*0.85) {
		banner = fmt.Sprintf("%d / %d in active set — archive or pack to stay sharp", activeCount, budget)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"packs":         data["packs"],
		"enabled":       data["enabled"],
		"active_count":  activeCount,
		"active_budget": budget,
		"budget_banner": banner,
	})
}

func (s *Server) handlePutSkillPacks(w http.ResponseWriter, r *http.Request) {
	var payload map[string]any
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "JSON body required"})
		return
	}
	packs, _ := payload["packs"].(map[string]any)
	if packs == nil {
		packs = map[string]any{}
	}
	enabled := []any{}
	switch e := payload["enabled"].(type) {
	case []any:
		enabled = e
	case []string:
		for _, s := range e {
			enabled = append(enabled, s)
		}
	}
	data := map[string]any{"packs": packs, "enabled": enabled}
	path, err := s.saveSkillPacks(data)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":  "ok",
		"path":    path,
		"packs":   packs,
		"enabled": enabled,
	})
}

func (s *Server) handleSetSkillStatus(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PathValue("name"))
	if name == "" || !isSafeSkillName(name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid skill name"})
		return
	}
	rec, ok := s.findSkill(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found: " + name})
		return
	}
	var payload map[string]any
	_ = json.NewDecoder(r.Body).Decode(&payload)
	statusRaw, _ := payload["status"].(string)
	status := strings.ToLower(strings.TrimSpace(statusRaw))
	if status == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "status required"})
		return
	}
	if _, ok := validSkillStatuses[status]; !ok {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid status: " + status})
		return
	}
	meta := map[string]string{}
	forcePromote := false
	if v, ok := payload["force_promote"]; ok {
		forcePromote = truthyAny(v)
	}
	quarantineSet := false
	quarantineVal := false
	if v, ok := payload["quarantine"]; ok {
		quarantineSet = true
		quarantineVal = truthyAny(v)
	}
	if forcePromote || status == "active" {
		meta["lifecycle"] = "manual-promote"
		meta["lifecycle_last"] = "Manually force-promoted by user"
		meta["manual_override"] = "promote"
		meta["quarantine"] = "false"
		rec.Quarantine = false
	}
	if quarantineSet {
		meta["quarantine"] = strconv.FormatBool(quarantineVal)
		rec.Quarantine = quarantineVal
		if quarantineVal {
			meta["lifecycle"] = "manual-quarantine"
			meta["lifecycle_last"] = "Manually quarantined by user"
			meta["manual_override"] = "quarantine"
			if status == "active" {
				status = "disabled"
			}
		}
	}
	rec.Status = status
	if err := s.persistSkillRecord(rec, meta, ""); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"name":       name,
		"status":     status,
		"quarantine": rec.Quarantine,
		"lifecycle":  meta["lifecycle"],
	})
}

func (s *Server) handleSetSkillQuarantine(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PathValue("name"))
	if name == "" || !isSafeSkillName(name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid skill name"})
		return
	}
	rec, ok := s.findSkill(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found: " + name})
		return
	}
	var payload map[string]any
	_ = json.NewDecoder(r.Body).Decode(&payload)
	on := true
	if v, ok := payload["quarantine"]; ok {
		on = truthyAny(v)
	}
	meta := map[string]string{
		"quarantine": strconv.FormatBool(on),
	}
	if on {
		meta["manual_override"] = "quarantine"
		meta["lifecycle_last"] = "Manually quarantined by user"
		rec.Status = "disabled"
	} else {
		meta["manual_override"] = "clear-quarantine"
		meta["lifecycle_last"] = "Quarantine cleared by user"
	}
	rec.Quarantine = on
	if err := s.persistSkillRecord(rec, meta, ""); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"name":       name,
		"quarantine": on,
		"status":     rec.Status,
	})
}

func (s *Server) handlePutSkillBody(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PathValue("name"))
	if name == "" || !isSafeSkillName(name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid skill name"})
		return
	}
	rec, ok := s.findSkill(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found: " + name})
		return
	}
	var payload map[string]any
	_ = json.NewDecoder(r.Body).Decode(&payload)
	var text string
	if body, ok := payload["body"]; ok && body != nil {
		text = fmt.Sprint(body)
	} else if inst, ok := payload["instructions"]; ok && inst != nil {
		text = fmt.Sprint(inst)
	} else {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "body or instructions required"})
		return
	}
	if strings.HasPrefix(strings.TrimLeft(text, " \t\n"), "---") {
		parts := strings.SplitN(text, "---", 3)
		if len(parts) >= 3 {
			text = strings.TrimLeft(parts[2], "\n")
		}
	}
	meta := map[string]string{
		"manual_edit":    "true",
		"lifecycle_last": "Instructions edited by user",
	}
	rec.Body = text
	if err := s.persistSkillRecord(rec, meta, text); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"name":   name,
		"status": "saved",
		"chars":  len(text),
	})
}

func (s *Server) handleSkillFeedback(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimSpace(r.PathValue("name"))
	if name == "" || !isSafeSkillName(name) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Invalid skill name"})
		return
	}
	rec, ok := s.findSkill(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Skill not found: " + name})
		return
	}
	var payload map[string]any
	_ = json.NewDecoder(r.Body).Decode(&payload)
	success := true
	if v, ok := payload["success"]; ok {
		success = truthyAny(v)
	}
	stats := s.loadSkillStats()
	row := statsEnsure(stats, name)
	row["total_executions"] = asInt(row["total_executions"]) + 1
	if success {
		row["successes"] = asInt(row["successes"]) + 1
		row["consecutive_failures"] = 0
		row["last_success_at"] = time.Now().UTC().Format(time.RFC3339)
	} else {
		row["failures"] = asInt(row["failures"]) + 1
		row["consecutive_failures"] = asInt(row["consecutive_failures"]) + 1
		row["last_failure_at"] = time.Now().UTC().Format(time.RFC3339)
	}
	row["last_executed"] = time.Now().UTC().Format(time.RFC3339)
	_ = s.saveSkillStats(stats)
	writeJSON(w, http.StatusOK, map[string]any{
		"name":        name,
		"success":     success,
		"status":      rec.Status,
		"refined":     false,
		"suggestions": []string{},
		"decision":    nil,
	})
}

func (s *Server) handleSkillsLearningSummary(w http.ResponseWriter, r *http.Request) {
	limit := 12
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	if limit > 50 {
		limit = 50
	}
	if limit < 1 {
		limit = 1
	}
	type scored struct {
		row   map[string]any
		mtime float64
	}
	var learned []scored
	probation := 0
	activeLearned := 0
	for _, rec := range s.discoverSkills() {
		st := strings.ToLower(rec.Status)
		if st == "discovered" || st == "validated" {
			probation++
		}
		if !rec.AutoGenerated {
			continue
		}
		if st == "active" {
			activeLearned++
		}
		mtime := 0.0
		if rec.Path != "" {
			target := filepath.Join(rec.Dir, "SKILL.md")
			if st, err := os.Stat(target); err == nil {
				mtime = float64(st.ModTime().Unix())
			}
		}
		info := skillToPublic(rec)
		info["lifecycle_last"] = rec.Lifecycle
		learned = append(learned, scored{row: info, mtime: mtime})
	}
	learnedCount := len(learned)
	for i := 0; i < len(learned); i++ {
		for j := i + 1; j < len(learned); j++ {
			if learned[j].mtime > learned[i].mtime {
				learned[i], learned[j] = learned[j], learned[i]
			}
		}
	}
	if len(learned) > limit {
		learned = learned[:limit]
	}
	recent := make([]map[string]any, 0, len(learned))
	for _, sc := range learned {
		recent = append(recent, sc.row)
	}
	note := "No auto-learned skills yet — multi-step successful work can create them."
	if learnedCount > 0 {
		note = "Learned skills start on probation and promote only after multi-session success."
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"recent":               recent,
		"probation_count":      probation,
		"learned_count":        learnedCount,
		"active_learned_count": activeLearned,
		"note":                 note,
	})
}

func (s *Server) handleSkillsReuseMetrics(w http.ResponseWriter, r *http.Request) {
	stats := s.loadSkillStats()
	skillsMap, _ := stats["skills"].(map[string]any)
	if skillsMap == nil {
		skillsMap = map[string]any{}
	}
	type row struct {
		name string
		data map[string]any
		act  int
	}
	rows := make([]row, 0, len(skillsMap))
	totalAct := 0
	reused := 0
	multi := 0
	for name, raw := range skillsMap {
		m, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		act := asInt(m["activations"])
		totalAct += act
		if act > 0 {
			reused++
		}
		sessCount := 0
		if sm, ok := m["activation_sessions"].(map[string]any); ok {
			sessCount = len(sm)
			if sessCount >= 2 {
				multi++
			}
		}
		total := asInt(m["total_executions"])
		successes := asInt(m["successes"])
		successRate := 0.0
		if total > 0 {
			successRate = float64(successes) / float64(total)
		}
		reuseRate := 0.0
		if act > 0 {
			reuseRate = float64(act-1) / float64(act)
		}
		rows = append(rows, row{
			name: name,
			act:  act,
			data: map[string]any{
				"name":                name,
				"activations":         act,
				"activation_sessions": sessCount,
				"total_executions":    total,
				"success_rate":        round3(successRate),
				"reuse_rate":          round3(reuseRate),
				"last_activated":      m["last_activated"],
			},
		})
	}
	for i := 0; i < len(rows); i++ {
		for j := i + 1; j < len(rows); j++ {
			if rows[j].act > rows[i].act {
				rows[i], rows[j] = rows[j], rows[i]
			}
		}
	}
	outSkills := make([]map[string]any, 0, len(rows))
	for i, r := range rows {
		if i >= 100 {
			break
		}
		outSkills = append(outSkills, r.data)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"skills_tracked":               len(skillsMap),
		"skills_with_activation":       reused,
		"total_activations":            totalAct,
		"multi_session_reactivations":  multi,
		"skills":                       outSkills,
	})
}

func (s *Server) handleArchiveUnusedSkills(w http.ResponseWriter, r *http.Request) {
	var payload map[string]any
	_ = json.NewDecoder(r.Body).Decode(&payload)
	days := 90
	if v, ok := payload["days"]; ok {
		days = asInt(v)
	}
	if days < 7 {
		days = 7
	}
	if days > 3650 {
		days = 3650
	}
	dryRun := truthyAny(payload["dry_run"])
	includeQ := truthyAny(payload["include_quarantine"])
	cutoff := time.Now().UTC().Add(-time.Duration(days) * 24 * time.Hour)
	stats := s.loadSkillStats()
	skillsMap, _ := stats["skills"].(map[string]any)
	if skillsMap == nil {
		skillsMap = map[string]any{}
	}
	candidates := []map[string]any{}
	archived := []string{}
	for _, rec := range s.discoverSkills() {
		st := strings.ToLower(rec.Status)
		if st == "archived" || st == "deprecated" {
			continue
		}
		if rec.Quarantine && !includeQ {
			continue
		}
		var lastDt *time.Time
		rawStats, _ := skillsMap[rec.Name].(map[string]any)
		never := true
		if rawStats != nil {
			for _, key := range []string{"last_activated", "last_executed", "last_success_at"} {
				if ts := parseISOTime(fmt.Sprint(rawStats[key])); ts != nil {
					never = false
					if lastDt == nil || ts.After(*lastDt) {
						lastDt = ts
					}
				}
			}
			if asInt(rawStats["total_executions"]) > 0 || asInt(rawStats["activations"]) > 0 {
				never = false
			}
		}
		cold := false
		if lastDt != nil {
			cold = lastDt.Before(cutoff)
		}
		isLearned := rec.AutoGenerated
		if !(cold || (never && isLearned)) {
			continue
		}
		if never && !isLearned {
			continue
		}
		var lastActivity any
		if lastDt != nil {
			lastActivity = lastDt.Format(time.RFC3339)
		}
		row := map[string]any{
			"name":           rec.Name,
			"status":         st,
			"last_activity":  lastActivity,
			"auto_generated": isLearned,
		}
		candidates = append(candidates, row)
		if dryRun {
			continue
		}
		meta := map[string]string{
			"lifecycle":      "bulk-archive",
			"lifecycle_last": fmt.Sprintf("Archived: unused >%dd", days),
		}
		rec.Status = "archived"
		if err := s.persistSkillRecord(rec, meta, ""); err != nil {
			continue
		}
		archived = append(archived, rec.Name)
	}
	count := len(archived)
	if dryRun {
		count = len(candidates)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"days":       days,
		"dry_run":    dryRun,
		"candidates": candidates,
		"archived":   archived,
		"count":      count,
	})
}

func (s *Server) handleExportSkillsPack(w http.ResponseWriter, r *http.Request) {
	var payload map[string]any
	_ = json.NewDecoder(r.Body).Decode(&payload)
	var names []string
	if raw, ok := payload["names"].([]any); ok {
		for _, n := range raw {
			names = append(names, strings.TrimSpace(fmt.Sprint(n)))
		}
	}
	var skills []skillRecord
	if len(names) > 0 {
		for _, n := range names {
			if n == "" || n == "<nil>" {
				continue
			}
			if rec, ok := s.findSkill(n); ok {
				skills = append(skills, rec)
			}
		}
	} else {
		skills = s.discoverSkills()
	}
	if len(skills) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "No skills to export"})
		return
	}
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for _, rec := range skills {
		if rec.Dir == "" || !dirExists(rec.Dir) {
			continue
		}
		_ = filepath.WalkDir(rec.Dir, func(path string, d os.DirEntry, err error) error {
			if err != nil || d.IsDir() {
				return nil
			}
			rel, err := filepath.Rel(filepath.Dir(rec.Dir), path)
			if err != nil {
				return nil
			}
			rel = filepath.ToSlash(rel)
			data, err := os.ReadFile(path)
			if err != nil {
				return nil
			}
			w, err := zw.Create(rel)
			if err != nil {
				return nil
			}
			_, _ = w.Write(data)
			return nil
		})
	}
	if err := zw.Close(); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	filename := "remedy-skills-pack.zip"
	w.Header().Set("Content-Type", "application/zip")
	w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(buf.Bytes())
}

func (s *Server) handleImportSkillsPack(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseMultipartForm(int64(maxSkillZipBytes) + 1024); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "file required"})
		return
	}
	file, _, err := r.FormFile("file")
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "file required"})
		return
	}
	defer file.Close()
	data, err := io.ReadAll(io.LimitReader(file, int64(maxSkillZipBytes)+1))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "cannot read upload"})
		return
	}
	if len(data) > maxSkillZipBytes {
		writeJSON(w, http.StatusRequestEntityTooLarge, map[string]string{
			"detail": fmt.Sprintf("Skill pack exceeds %d bytes", maxSkillZipBytes),
		})
		return
	}
	destRoot := filepath.Join(s.skillsHome(), "skills")
	if err := os.MkdirAll(destRoot, 0o700); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	names, err := importSkillPackQuarantine(data, destRoot)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"imported":   len(names),
		"names":      names,
		"quarantine": true,
	})
}

// importSkillPackQuarantine extracts a user-uploaded pack ZIP into destRoot.
func importSkillPackQuarantine(zipData []byte, destRoot string) ([]string, error) {
	zr, err := zip.NewReader(bytes.NewReader(zipData), int64(len(zipData)))
	if err != nil {
		return nil, fmt.Errorf("invalid zip: %w", err)
	}
	tmp, err := os.MkdirTemp("", "remedy-skill-import-*")
	if err != nil {
		return nil, err
	}
	defer os.RemoveAll(tmp)
	extractRoot := filepath.Join(tmp, "extract")
	if err := os.MkdirAll(extractRoot, 0o700); err != nil {
		return nil, err
	}
	if err := safeExtractZip(zr, extractRoot); err != nil {
		return nil, err
	}
	skillDirs := findSkillDirs(extractRoot)
	if len(skillDirs) == 0 {
		return nil, fmt.Errorf("no SKILL.md in zip")
	}
	var names []string
	for _, src := range skillDirs {
		rec, ok := loadSkillDir(src)
		if !ok || !isSafeSkillName(rec.Name) {
			continue
		}
		dest := filepath.Join(destRoot, rec.Name)
		destAbs, err := filepath.Abs(dest)
		if err != nil {
			continue
		}
		rootAbs, err := filepath.Abs(destRoot)
		if err != nil {
			continue
		}
		rel, err := filepath.Rel(rootAbs, destAbs)
		if err != nil || strings.HasPrefix(rel, "..") {
			continue
		}
		if dirExists(dest) {
			_ = os.RemoveAll(dest)
		}
		if err := copyDir(src, dest); err != nil {
			continue
		}
		// Force quarantine metadata on import.
		md := filepath.Join(dest, "SKILL.md")
		rawBytes, err := os.ReadFile(md)
		if err != nil {
			continue
		}
		fm, body, ok := parseSkillFrontmatter(string(rawBytes))
		_ = fm
		metaLines := []string{
			"metadata:",
			"  source: import",
			"  quarantine: true",
			"  trust: pack-import",
			"status: discovered",
		}
		var front string
		if ok {
			cleaned := stripMetadataBlock(string(rawBytes))
			m := frontmatterRE.FindStringSubmatch(cleaned)
			if m != nil {
				block := strings.TrimRight(m[1], "\n")
				// Drop existing status line so imported packs start discovered.
				block = stripTopLevelKey(block, "status")
				front = "---\n" + block + "\n" + strings.Join(metaLines, "\n") + "\n---\n\n" + strings.TrimLeft(body, "\n")
			} else {
				front = "---\n" + strings.Join(metaLines, "\n") + "\n---\n\n" + strings.TrimLeft(string(rawBytes), "\n")
			}
		} else {
			front = "---\nname: " + yamlScalar(rec.Name) + "\n" + strings.Join(metaLines, "\n") +
				"\n---\n\n" + strings.TrimLeft(string(rawBytes), "\n")
		}
		if err := os.WriteFile(md, []byte(front), 0o600); err != nil {
			continue
		}
		names = append(names, rec.Name)
	}
	if len(names) == 0 {
		return nil, fmt.Errorf("import produced no skills (invalid pack?)")
	}
	return names, nil
}

func stripTopLevelKey(block, key string) string {
	lines := splitYAMLLines(block)
	var kept []string
	for _, line := range lines {
		trim := strings.TrimSpace(line)
		if countIndent(line) == 0 {
			k, _, ok := splitKV(trim)
			if ok && strings.EqualFold(k, key) {
				continue
			}
		}
		kept = append(kept, line)
	}
	return strings.Join(kept, "\n")
}

// persistSkillRecord rewrites SKILL.md under the skill dir (user skills only).
func (s *Server) persistSkillRecord(rec skillRecord, meta map[string]string, bodyOverride string) error {
	dir := rec.Dir
	if dir == "" {
		return fmt.Errorf("skill has no path")
	}
	userRoot := filepath.Join(s.skillsHome(), "skills")
	absDir, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	absUser, err := filepath.Abs(userRoot)
	if err != nil {
		return err
	}
	rel, err := filepath.Rel(absUser, absDir)
	underUser := err == nil && !strings.HasPrefix(rel, "..")
	if !underUser {
		// Bundled / seed skills: copy-on-write into user skills.
		dest := filepath.Join(userRoot, rec.Name)
		if err := os.MkdirAll(userRoot, 0o700); err != nil {
			return err
		}
		if !dirExists(dest) {
			if err := copyDir(dir, dest); err != nil {
				return err
			}
		}
		dir = dest
		absDir = dest
	}
	md := filepath.Join(dir, "SKILL.md")
	rawBytes, err := os.ReadFile(md)
	if err != nil {
		return err
	}
	fm, body, ok := parseSkillFrontmatter(string(rawBytes))
	if bodyOverride != "" {
		body = bodyOverride
	}
	status := rec.Status
	if status == "" {
		status = fm.Status
	}
	name := rec.Name
	if name == "" {
		name = fm.Name
	}
	desc := rec.Description
	if desc == "" {
		desc = fm.Description
	}
	ver := rec.Version
	if ver == "" {
		ver = fm.Version
	}
	if !ok {
		ok = true
	}
	_ = ok
	// Merge metadata
	merged := map[string]string{}
	for k, v := range fm.RawMeta {
		merged[k] = v
	}
	for k, v := range meta {
		merged[k] = v
	}
	if rec.Quarantine {
		merged["quarantine"] = "true"
	} else if _, set := meta["quarantine"]; set {
		merged["quarantine"] = meta["quarantine"]
	}
	var lines []string
	lines = append(lines, "name: "+yamlScalar(name))
	if desc != "" {
		lines = append(lines, "description: "+yamlScalar(desc))
	}
	if ver != "" {
		lines = append(lines, "version: "+yamlScalar(ver))
	}
	if status != "" {
		lines = append(lines, "status: "+yamlScalar(status))
	}
	if len(rec.Tags) > 0 {
		lines = append(lines, "tags: ["+strings.Join(quoteYAMLList(rec.Tags), ", ")+"]")
	}
	if len(merged) > 0 {
		lines = append(lines, "metadata:")
		for k, v := range merged {
			lines = append(lines, "  "+k+": "+yamlScalar(v))
		}
	}
	front := "---\n" + strings.Join(lines, "\n") + "\n---\n\n" + strings.TrimLeft(body, "\n")
	return os.WriteFile(md, []byte(front), 0o600)
}

func (s *Server) loadSkillStats() map[string]any {
	path := s.skillStatsPath()
	var data map[string]any
	if err := readJSONFile(path, &data); err != nil || data == nil {
		return map[string]any{"version": 1, "skills": map[string]any{}}
	}
	if _, ok := data["skills"].(map[string]any); !ok {
		data["skills"] = map[string]any{}
	}
	return data
}

func (s *Server) saveSkillStats(data map[string]any) error {
	return writeJSONAtomic(s.skillStatsPath(), data)
}

func statsEnsure(stats map[string]any, name string) map[string]any {
	skills, _ := stats["skills"].(map[string]any)
	if skills == nil {
		skills = map[string]any{}
		stats["skills"] = skills
	}
	row, _ := skills[name].(map[string]any)
	if row == nil {
		row = map[string]any{}
		skills[name] = row
	}
	return row
}

func truthyAny(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		return truthyYAML(t)
	case float64:
		return t != 0
	case int:
		return t != 0
	default:
		return false
	}
}

func asInt(v any) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case string:
		n, _ := strconv.Atoi(strings.TrimSpace(t))
		return n
	case json.Number:
		n, _ := t.Int64()
		return int(n)
	default:
		return 0
	}
}

func round3(f float64) float64 {
	return float64(int(f*1000+0.5)) / 1000
}

func parseISOTime(s string) *time.Time {
	s = strings.TrimSpace(s)
	if s == "" || s == "<nil>" {
		return nil
	}
	for _, layout := range []string{time.RFC3339Nano, time.RFC3339} {
		if t, err := time.Parse(layout, s); err == nil {
			return &t
		}
	}
	return nil
}
