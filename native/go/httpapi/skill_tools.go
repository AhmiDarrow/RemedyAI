package httpapi

import (
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode/utf8"
)

// skill.search and skill.activate: SkillRegistry.match_skills / skill_body in
// remedy/skills/registry.py, ported. filterSkills stays the HTTP catalog
// filter; the model's ranking is this one.

// skillActivations is SkillRegistry._activation_counts: in-process, per name,
// a small rank boost for skills already used this run.
var skillActivations = struct {
	sync.Mutex
	n map[string]int
}{n: map[string]int{}}

func skillActivationCount(name string) int {
	skillActivations.Lock()
	defer skillActivations.Unlock()
	return skillActivations.n[name]
}

func markSkillActivated(name string) {
	skillActivations.Lock()
	skillActivations.n[name]++
	skillActivations.Unlock()
}

var skillStatusScore = map[string]float64{
	"active": 1.0, "validated": 0.7, "discovered": 0.4, "disabled": 0.05, "deprecated": 0.0,
}

// skillToolChainSegments is _TOOL_CHAIN_SEGMENTS, hyphenated, longest first.
var skillToolChainSegments = func() []string {
	raw := []string{"bash_exec", "file_read", "file_write", "file_edit", "file_edit_batch", "list_dir",
		"file_glob", "todo_write", "build_drive", "repo_search", "spread_run", "job_run", "mission_start",
		"mission_update", "mission_status", "plan_show", "plan_save", "plan_step_status", "goal_list",
		"goal_add", "goal_complete", "checkpoint_show", "get_settings", "update_settings", "memory_search",
		"memory_save", "web_fetch", "web_search", "vision_decode", "computer_navigate", "computer_act",
		"computer_snapshot", "computer_screenshot", "computer_windows", "computer_app", "computer_key",
		"computer_wait", "computer_find", "computer_page_text", "comfyui", "skill_activate", "skill_search",
		"local_discover", "mail_list", "mail_send", "calendar_list_events", "calendar_create_event",
		"budget_set", "budget_status", "budget_tx_add", "bill_list", "bill_upsert", "debt_upsert",
		"assistant_accounts", "compress_context", "read_dir"}
	out := make([]string, len(raw))
	for i, t := range raw {
		out[i] = strings.ReplaceAll(t, "_", "-")
	}
	// Python sorts a set by length only; ties never matter (no segment is a
	// prefix of another of equal length).
	sort.SliceStable(out, func(i, j int) bool { return len(out[i]) > len(out[j]) })
	return out
}()

// looksLikeToolChainSkillName is looks_like_tool_chain_skill_name.
func looksLikeToolChainSkillName(name string) bool {
	raw := strings.Trim(strings.ReplaceAll(strings.ToLower(strings.TrimSpace(name)), "_", "-"), "-")
	if raw == "" || !strings.Contains(raw, "-") {
		return false
	}
	remaining, found := raw, 0
	for remaining != "" {
		remaining = strings.TrimLeft(remaining, "-")
		if remaining == "" {
			break
		}
		matched := false
		for _, t := range skillToolChainSegments {
			if remaining == t || strings.HasPrefix(remaining, t+"-") {
				found++
				remaining = remaining[len(t):]
				matched = true
				break
			}
		}
		if !matched {
			if _, rest, ok := strings.Cut(remaining, "-"); ok {
				remaining = rest
			} else {
				remaining = ""
			}
		}
	}
	segs := 0
	for _, p := range strings.Split(raw, "-") {
		if p != "" {
			segs++
		}
	}
	return found >= 2 && found >= max(2, segs/2)
}

func skillMetaFloat(rec skillRecord, keys ...string) float64 {
	for _, k := range keys {
		if f, err := strconv.ParseFloat(strings.TrimSpace(rec.Meta[k]), 64); err == nil && f != 0 {
			return f
		}
	}
	return 0
}

// skillTriggerHit reports whether any trigger regex matches text (case-insensitive).
// Patterns RE2 cannot compile are skipped, as Python skips re.error.
func skillTriggerHit(triggers []string, text string) bool {
	q := strings.TrimSpace(text)
	if q == "" {
		return false
	}
	for _, pat := range triggers {
		if re, err := regexp.Compile("(?i)" + pat); err == nil && re.MatchString(q) {
			return true
		}
	}
	return false
}

type rankedSkill struct {
	rec   skillRecord
	score float64
}

func tokenOverlapCount(a, b map[string]struct{}) int {
	n := 0
	for t := range a {
		if _, ok := b[t]; ok {
			n++
		}
	}
	return n
}

// rankSkills is SkillRegistry.match_skills (include_disabled=False, no project_path).
func rankSkills(skills []skillRecord, query string, limit int, workspaceHint string) []rankedSkill {
	qTokens := tokenizeSkillText(query)
	wTokens := tokenizeSkillText(workspaceHint)
	ql := strings.ToLower(query)
	var scored []rankedSkill
	for _, rec := range skills {
		switch rec.Status {
		case "disabled", "deprecated", "archived":
			continue
		}
		nameL := strings.ToLower(rec.Name)
		// Quarantine skills only match explicit name queries.
		if rec.Quarantine && (len(qTokens) == 0 || !strings.Contains(ql, nameL)) {
			continue
		}
		statusS, ok := skillStatusScore[rec.Status]
		if !ok {
			statusS = 0.3
		}
		effort := rec.EffortWeight
		rate := skillMetaFloat(rec, "success_rate", "estimated_success_rate")
		if rate == 0 {
			rate = 0.5
		}
		nameTok := tokenizeSkillText(strings.ReplaceAll(rec.Name, "-", " "))
		descTok := tokenizeSkillText(rec.Description)
		tagTok := tokenizeSkillText(strings.Join(rec.Tags, " "))
		all := map[string]struct{}{}
		for _, m := range []map[string]struct{}{nameTok, descTok, tagTok} {
			for t := range m {
				all[t] = struct{}{}
			}
		}
		textScore := 0.35 // neutral when browsing
		if len(qTokens) > 0 {
			nq := float64(len(qTokens))
			substr := 0.0
			if ql != "" && strings.Contains(nameL, ql) {
				substr += 0.5
			}
			if ql != "" && strings.Contains(strings.ToLower(rec.Description), ql) {
				substr += 0.25
			}
			textScore = 0.45*math.Min(1, float64(tokenOverlapCount(qTokens, all))/nq) +
				0.30*math.Min(1, float64(tokenOverlapCount(qTokens, nameTok))/nq) +
				0.15*math.Min(1, float64(tokenOverlapCount(qTokens, tagTok))/nq) +
				0.10*math.Min(1, substr)
		}
		wsBoost := 0.0
		if len(wTokens) > 0 {
			wsBoost = 0.1 * math.Min(1, float64(tokenOverlapCount(wTokens, all))/float64(len(wTokens)))
		}
		actBoost := 0.05 * math.Min(1, float64(skillActivationCount(rec.Name))/5)
		costBoost := 0.0
		if dur := skillMetaFloat(rec, "avg_duration_ms", "last_duration_ms", "duration_ms"); dur > 0 {
			costBoost = math.Max(-0.06, 0.05-(dur/60000)*0.08)
		}
		score := 0.30*statusS + 0.36*textScore + 0.14*math.Min(1, effort) + 0.10*math.Min(1, rate) +
			wsBoost + actBoost + costBoost
		if effort >= 0.62 {
			score += 0.03
		}
		if rec.AutoGenerated && rec.Status != "active" {
			nameExact := nameL != "" && ql != "" && (strings.Contains(ql, nameL) || strings.Contains(nameL, ql))
			chain := looksLikeToolChainSkillName(rec.Name) ||
				strings.HasPrefix(strings.ToLower(rec.Description), "use when the user needs help with:")
			if !nameExact {
				if chain {
					score -= 0.22
				} else if effort < 0.62 {
					score -= 0.14
				}
			}
		}
		if len(rec.Triggers) > 0 {
			if skillTriggerHit(rec.Triggers, query) {
				score += 0.25
			} else {
				score -= 0.30
			}
		}
		scored = append(scored, rankedSkill{rec, score})
	}
	sort.SliceStable(scored, func(i, j int) bool {
		if scored[i].score != scored[j].score {
			return scored[i].score > scored[j].score
		}
		return strings.ToLower(scored[i].rec.Name) < strings.ToLower(scored[j].rec.Name)
	})
	if len(scored) > limit {
		scored = scored[:limit]
	}
	return scored
}

func toolSkillSearch(s *Server, in memorySkillInput) (any, error) {
	query := strings.TrimSpace(in.Query)
	ranked := rankSkills(s.discoverSkills(), query, clampToolLimit(in.Limit), strings.TrimSpace(in.ProjectPath))
	skills := make([]map[string]any, 0, len(ranked))
	for _, r := range ranked {
		skills = append(skills, map[string]any{
			"name":        r.rec.Name,
			"score":       r.score,
			"status":      r.rec.Status,
			"description": truncateRunes(r.rec.Description, 200),
		})
	}
	return map[string]any{"query": query, "skills": skills, "total": len(skills)}, nil
}

// skillContextCaps are SKILL_BODY_CHAR_CAP, SKILL_REF_INLINE_MAX and
// SKILL_REF_CHAR_CAP from remedy.core.react_policy.
func skillContextCaps() (body, refMax, refCap int) {
	switch strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_FULL_CONTEXT"))) {
	case "0", "false", "no", "off":
		return 48000, 5, 4000
	}
	return 120000, 8, 6000
}

// skillReferenceFiles lists references/ recursively, relative, sorted (Python's
// _discover_bundled_resources).
func skillReferenceFiles(dir string) []string {
	root := filepath.Join(dir, "references")
	var refs []string
	_ = filepath.WalkDir(root, func(p string, d os.DirEntry, err error) error {
		if err == nil && !d.IsDir() {
			if rel, rerr := filepath.Rel(dir, p); rerr == nil {
				refs = append(refs, rel)
			}
		}
		return nil
	})
	sort.Strings(refs)
	return refs
}

func skillReferencesBlock(dir string, refs []string, refMax, refCap int) string {
	refs = append([]string(nil), refs...)
	rank := func(r string) int {
		if strings.EqualFold(filepath.Base(r), "INDEX.md") {
			return 0
		}
		return 1
	}
	sort.SliceStable(refs, func(i, j int) bool {
		if rank(refs[i]) != rank(refs[j]) {
			return rank(refs[i]) < rank(refs[j])
		}
		return strings.ToLower(refs[i]) < strings.ToLower(refs[j])
	})
	lines := []string{"\n\n## References (on disk)"}
	for _, rel := range refs {
		var size int64
		if st, err := os.Stat(filepath.Join(dir, rel)); err == nil && !st.IsDir() {
			size = st.Size()
		}
		lines = append(lines, fmt.Sprintf("- %s (%d bytes)", filepath.ToSlash(rel), size))
	}
	inlined := 0
	for _, rel := range refs {
		if inlined >= refMax {
			break
		}
		raw, err := os.ReadFile(filepath.Join(dir, rel))
		if err != nil {
			continue
		}
		text := string(raw)
		if utf8.RuneCountInString(text) > refCap {
			text = truncateRunes(text, refCap) + fmt.Sprintf("\n…[truncated at %d chars — file_read %s for the rest]", refCap, rel)
		}
		lines = append(lines, fmt.Sprintf("\n\n## Reference: %s\n\n%s", filepath.ToSlash(rel), text))
		inlined++
	}
	if inlined < len(refs) {
		lines = append(lines, fmt.Sprintf("\n\n_%d more reference(s) not inlined — file_read them from the skill path above._", len(refs)-inlined))
	}
	return strings.Join(lines, "\n")
}

// skillBody is SkillRegistry.skill_body.
func skillBody(rec skillRecord, includeReferences bool) string {
	header := []string{
		"# Skill: " + rec.Name,
		"**Status:** " + rec.Status,
		"**Version:** " + rec.Version,
		"**Description:** " + rec.Description,
	}
	if rec.Dir != "" {
		header = append(header, "**Path:** "+rec.Dir)
	}
	if len(rec.Tags) > 0 {
		header = append(header, "**Tags:** "+strings.Join(rec.Tags, ", "))
	}
	if len(rec.Tools) > 0 {
		header = append(header, "**Tools:** "+strings.Join(rec.Tools, ", "))
	}
	if effort, ok := rec.Meta["effort_weight"]; ok {
		header = append(header, fmt.Sprintf("**Effort weight:** %s (%s)", effort, rec.Meta["effort_band"]))
	}
	if p := rec.Meta["parent_skill"]; p != "" {
		header = append(header, "**Parent / merged from:** "+p)
	}
	chain := rec.Meta["requires_chain"]
	if chain == "" {
		chain = rec.Meta["composes_with"]
	}
	if chain != "" {
		header = append(header, "**Composes with:** "+chain)
	}
	header = append(header, "")
	bodyCap, refMax, refCap := skillContextCaps()
	body := rec.Body
	if utf8.RuneCountInString(body) > bodyCap {
		body = truncateRunes(body, bodyCap) +
			fmt.Sprintf("\n\n…[skill body truncated at %d chars — open SKILL.md on disk for full text]", bodyCap)
	}
	header = append(header, body)
	if includeReferences && rec.Dir != "" {
		if refs := skillReferenceFiles(rec.Dir); len(refs) > 0 {
			header = append(header, skillReferencesBlock(rec.Dir, refs, refMax, refCap))
		}
	}
	return strings.Join(header, "\n")
}

// relatedSkills is SkillRegistry.related_skills: shared tags count 2, tools 1.
func relatedSkills(skills []skillRecord, rec skillRecord, limit int) []string {
	set := func(xs []string) map[string]struct{} {
		m := make(map[string]struct{}, len(xs))
		for _, x := range xs {
			m[x] = struct{}{}
		}
		return m
	}
	tags, tools := set(rec.Tags), set(rec.Tools)
	var scored []rankedSkill
	for _, o := range skills {
		if o.Name == rec.Name || o.Status == "disabled" || o.Status == "deprecated" {
			continue
		}
		if score := tokenOverlapCount(tags, set(o.Tags))*2 + tokenOverlapCount(tools, set(o.Tools)); score > 0 {
			scored = append(scored, rankedSkill{o, float64(score)})
		}
	}
	sort.SliceStable(scored, func(i, j int) bool { return scored[i].score > scored[j].score })
	out := []string{}
	for i := 0; i < len(scored) && i < limit; i++ {
		out = append(out, scored[i].rec.Name)
	}
	return out
}

var skillBulkNames = map[string]bool{"all": true, "*": true, "every": true, "everything": true, "reload": true,
	"reload all": true, "rescan": true, "refresh": true, "all skills": true, "every skill": true}

func toolSkillActivate(s *Server, in memorySkillInput) (any, error) {
	name := strings.TrimSpace(in.Name)
	if name == "" {
		name = strings.TrimSpace(in.Skill)
	}
	bulk := strings.NewReplacer("_", " ", "-", " ").Replace(strings.ToLower(name))
	if skillBulkNames[bulk] || strings.HasPrefix(bulk, "all ") {
		return nil, errors.New("refusing bulk skill.activate; load one pack per task " +
			"(use skill.search then skill.activate with an exact name)")
	}
	if name == "" {
		return nil, errors.New("name is required")
	}
	skills := s.discoverSkills()
	var rec skillRecord
	found := false
	for _, r := range skills {
		if r.Name == name { // registry lookup is by exact name
			rec, found = r, true
			break
		}
	}
	if !found {
		names := []string{}
		for _, h := range rankSkills(skills, name, 5, "") {
			names = append(names, h.rec.Name)
		}
		hint := strings.Join(names, ", ")
		if hint == "" {
			hint = "none"
		}
		return nil, fmt.Errorf("skill not found: %s; closest: %s", name, hint)
	}
	if rec.Quarantine {
		return nil, fmt.Errorf("skill '%s' is quarantined; Trust it in the Skills panel first", name)
	}
	switch rec.Status {
	case "disabled", "archived", "deprecated":
		return nil, fmt.Errorf("skill '%s' is %s (not active)", name, rec.Status)
	}
	body := skillBody(rec, in.IncludeReferences)
	markSkillActivated(name)
	return map[string]any{
		"name":    name,
		"body":    body,
		"related": relatedSkills(skills, rec, 5),
		"chars":   utf8.RuneCountInString(body),
	}, nil
}
