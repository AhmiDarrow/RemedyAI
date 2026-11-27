package httpapi

import (
	"math"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"
)

// ── consolidation (remedy.memory.consolidator.MemoryConsolidator) ─────────

// Consolidate is MemoryConsolidator.consolidate_session: it folds a session's
// entries into one "session" entry, demotes the sources, and quietly distills
// partner-memory facts from them. It returns nil when there are fewer than
// three non-system entries.
func (s *CLIStore) Consolidate(sessionID string, maxEntries int) (*CLIMemoryEntry, error) {
	entries, err := s.listBySession(sessionID, maxEntries)
	if err != nil {
		return nil, err
	}
	var src []*CLIMemoryEntry
	for _, e := range entries {
		if e.EntryType != "system" {
			src = append(src, e)
		}
	}
	if len(src) < 3 {
		return nil, nil
	}
	topics := cliStoreTopics(src)
	highlights := cliStoreHighlights(src)
	actions := cliStoreActionItems(src)
	top5 := func(xs []string) []string {
		if len(xs) > 5 {
			return xs[:5]
		}
		return xs
	}
	bullets := func(xs []string) string {
		parts := make([]string, len(xs))
		for i, x := range xs {
			parts[i] = "- " + x
		}
		return strings.Join(parts, "\n")
	}
	// Python's conditional expression binds the whole concatenation, so a
	// session with no action items consolidates to empty content. Kept as is
	// so both stores write the same row.
	summary := ""
	if len(actions) > 0 {
		summary = "Session " + sessionID + " consolidated summary.\n\n" +
			"**Topics**: " + strings.Join(top5(topics), ", ") + "\n\n" +
			"**Highlights**:\n" + bullets(top5(highlights)) + "\n\n" +
			"**Action items**:\n" + bullets(top5(actions))
	}
	ids := make([]any, len(src))
	for i, e := range src {
		ids[i] = e.ID
	}
	topicTags := append([]string{}, top5(topics)...)
	c := NewCLIMemoryEntry("session", "Consolidation: "+sessionID, summary, topicTags, 0.75)
	sid := sessionID
	c.SessionID = &sid
	c.meta.set("consolidated_from", ids)
	c.meta.set("entry_count", len(src))
	c.meta.set("topics", append([]string{}, topicTags...))
	if err := s.Upsert(c); err != nil {
		return nil, err
	}
	for _, e := range src {
		imp := e.Importance
		if imp == 0 {
			imp = 0.5
		}
		e.Importance = math.Min(imp, 0.25)
		e.meta = e.meta.copy()
		e.meta.set("consolidated_into", c.ID)
		e.meta.set("demoted", true)
		_ = s.Upsert(e) // per-entry failures are skipped, as Python does
	}
	n := len(src)
	if n > 40 {
		n = 40
	}
	lines := make([]string, n)
	for i := 0; i < n; i++ {
		lines[i] = src[i].Title + ": " + src[i].Content
	}
	_ = s.distill(strings.Join(lines, "\n"), sessionID) // quiet, never fails the consolidate
	return c, nil
}

// cliStoreTopics is Counter(tags).most_common(5): count desc, first seen first.
func cliStoreTopics(entries []*CLIMemoryEntry) []string {
	counts := map[string]int{}
	var order []string
	for _, e := range entries {
		for _, t := range e.Tags {
			if counts[t] == 0 {
				order = append(order, t)
			}
			counts[t]++
		}
	}
	sort.SliceStable(order, func(i, j int) bool { return counts[order[i]] > counts[order[j]] })
	if len(order) > 5 {
		order = order[:5]
	}
	return order
}

func cliStoreHighlights(entries []*CLIMemoryEntry) []string {
	sorted := append([]*CLIMemoryEntry{}, entries...)
	sort.SliceStable(sorted, func(i, j int) bool { return sorted[i].Importance > sorted[j].Importance })
	if len(sorted) > 5 {
		sorted = sorted[:5]
	}
	out := make([]string, len(sorted))
	for i, e := range sorted {
		preview := strings.ReplaceAll(truncateRunes(e.Content, 100), "\n", " ")
		out[i] = "[" + e.EntryType + "] " + e.Title + ": " + preview + "..."
	}
	return out
}

func cliStoreActionItems(entries []*CLIMemoryEntry) []string {
	seen := map[string]bool{}
	var out []string
	for _, e := range entries {
		v, _ := e.meta.get("action_items")
		arr, ok := v.([]any)
		if !ok {
			continue
		}
		for _, a := range arr {
			s := cliStorePyStr(a)
			if !seen[s] {
				seen[s] = true
				out = append(out, s)
			}
		}
	}
	if len(out) > 10 {
		out = out[:10]
	}
	return out
}

// ── partner-memory distillation (remedy.memory.partner_memory) ────────────

type cliStorePattern struct {
	re         *regexp.Regexp
	category   string
	confidence float64
}

// cliStorePrefPatterns is partner_memory._PREF_PATTERNS.
var cliStorePrefPatterns = []cliStorePattern{
	{regexp.MustCompile(`(?i)(?:my name is|i(?:'m| am) called|call me)\s+([A-Za-z][A-Za-z0-9_.\- ]{1,40})`), "identity", 0.92},
	{regexp.MustCompile(`(?i)i (?:prefer|always (?:use|prefer|want)|usually (?:use|prefer))\s+(.+?)(?:[.!?\n]|$)`), "preference", 0.88},
	{regexp.MustCompile(`(?i)(?:please )?(?:always|never)\s+(?:use|prefer|do|run|deploy|commit|push|force-?push|merge|delete|share|store|save|open|install)\s+(.+?)(?:[.!?\n]|$)`), "constraint", 0.9},
	{regexp.MustCompile(`(?i)(?:don'?t|do not|never)\s+(?:ever\s+)?(use|prefer|run|deploy|commit|push|force-?push|merge|delete|share|store|save)\s+(.+?)(?:[.!?\n]|$)`), "constraint", 0.86},
	{regexp.MustCompile(`(?i)(?:please\s+)?remember(?:\s+(?:this|that|the))?(?:\s+fact)?[:\s]+(.+?)(?:[.!?\n]|$)`), "general", 0.97},
	{regexp.MustCompile(`(?i)(?:note that|for (?:future|later) reference|don'?t forget)[:\s]+(.+?)(?:[.!?\n]|$)`), "general", 0.93},
	{regexp.MustCompile(`(?i)(?:store|save|keep)\s+(?:this\s+)?(?:in\s+)?(?:memory|partner memory)[:\s]+(.+?)(?:[.!?\n]|$)`), "general", 0.95},
	{regexp.MustCompile(`(?i)i (?:work (?:in|with|on)|use|write in)\s+(.+?)(?:[.!?\n]|$)`), "preference", 0.8},
	{regexp.MustCompile(`(?i)i (?:hate|love|can'?t stand|enjoy)\s+(.+?)(?:[.!?\n]|$)`), "preference", 0.86},
	{regexp.MustCompile(`(?i)(?:i(?:'m| am)|we(?:'re| are))\s+(?:a |an )?([^.!?\n]{4,80}(?:engineer|developer|founder|designer|student|parent)[^.!?\n]{0,40})`), "identity", 0.88},
	{regexp.MustCompile(`(?i)(?:my (?:wife|husband|partner|kids?|dog|cat|team|company|boss))\s+([^.!?\n]{3,80})`), "personal", 0.84},
	{regexp.MustCompile(`(?i)(?:i live in|i'?m (?:based|from)|timezone is)\s+([A-Za-z0-9_, /\-]{2,60})`), "identity", 0.87},
	{regexp.MustCompile(`(?i)(?:when you (?:talk|reply|answer|help)|please (?:be|stay))\s+(.+?)(?:[.!?\n]|$)`), "preference", 0.89},
	{regexp.MustCompile(`(?i)(?:we always|from now on we|our rule is)\s+(.+?)(?:[.!?\n]|$)`), "constraint", 0.9},
}

// cliStoreLivingPatterns is living._LIVING_PATTERNS.
var cliStoreLivingPatterns = []cliStorePattern{
	{regexp.MustCompile(`(?i)\b(?:i have|we've got|we have)\s+(?:a |an |two |three |\d+ )?(kids?|children|son|daughter|dog|cat|wife|husband|partner)\b([^.!?\n]{0,60})`), "life", 0.9},
	{regexp.MustCompile(`(?i)\b(?:my (?:wife|husband|partner|kids?|son|daughter|dog|cat|family))\s+([^.!?\n]{3,80})`), "life", 0.86},
	{regexp.MustCompile(`(?i)\b(?:i live in|i'?m (?:based|from)|timezone is|i work nights|i work mornings|i'?m a night owl|i'?m an early bird)\s+([^.!?\n]{0,60})`), "life", 0.88},
	{regexp.MustCompile(`(?i)\b(?:don'?t|do not|never)\s+(?:mention|bring up|talk about|ask about)\s+([^.!?\n]{3,80})`), "life", 0.91},
	{regexp.MustCompile(`(?i)\b(?:my |our )?(?:life )?goal(?:s)? (?:is|are|this (?:week|month|year) is)\s+([^.!?\n]{4,100})`), "goal", 0.9},
	{regexp.MustCompile(`(?i)\b(?:i(?:'m| am) (?:trying to|working toward|saving (?:up )?for))\s+([^.!?\n]{4,100})`), "goal", 0.87},
	{regexp.MustCompile(`(?i)\b(?:priority this (?:week|month)|this week i need to|the most important thing is)\s+([^.!?\n]{4,100})`), "goal", 0.88},
	{regexp.MustCompile(`(?i)\b(?:be (?:blunt|brief|direct|quiet|warm|patient)|no fluff|no theater|just works|don'?t lecture|don'?t ask(?: me)?(?: to)? confirm|just (?:do it|ship it)|work alone)\b([^.!?\n]{0,40})`), "preference", 0.9},
	{regexp.MustCompile(`(?i)\b(?:stop|don'?t) (?:being|sounding) so\s+(verbose|cheerful|corporate|salesy|robotic|generic)\b`), "correction", 0.9},
	{regexp.MustCompile(`(?i)\b(?:too )(generic|ai[- ]looking|verbose|cheerful|corporate|salesy|sloppy)\b`), "correction", 0.88},
	{regexp.MustCompile(`(?i)\b(?:i already (?:told|asked) you|as i(?: already)? said|like i said)\s+([^.!?\n]{6,100})`), "correction", 0.86},
	{regexp.MustCompile(`(?i)\b(?:don'?t|do not|never)\s+(?:add|write|leave)\s+(?:comments|docstrings|type:?ignore|todos?)\b([^.!?\n]{0,40})`), "craft", 0.9},
	{regexp.MustCompile(`(?i)\b(?:always|prefer|use)\s+(type hints?|ruff|pytest|uv |pnpm|yarn|strict types|small functions|early returns)\b([^.!?\n]{0,40})`), "craft", 0.88},
	{regexp.MustCompile(`(?i)\b(?:we use|this (?:repo|project|app) uses|stack is)\s+([^.!?\n]{3,80})`), "stack", 0.86},
	{regexp.MustCompile(`(?i)\b(?:no|never|don'?t use)\s+(rounded corners|drop shadows|gradients?|inter font|comic sans|purple gradients?|glassmorphism)\b`), "design", 0.9},
	{regexp.MustCompile(`(?i)\b(?:keep it|make it|prefer)\s+(brutalist|minimal|dense|quiet|sharp|flat|high contrast)\b([^.!?\n]{0,40})`), "design", 0.88},
	{regexp.MustCompile(`(?i)\b(?:8px|4px|12px|16px)\s+(?:spacing|grid|rhythm)\b|prefer (?:inter|geist|sf pro|jetbrains)`), "design", 0.86},
}

var (
	cliStoreLivingEphemeralRE = regexp.MustCompile(`(?i)\b(today|tonight|right now|this (?:morning|afternoon)|for now|real quick)\b`)
	cliStoreFactPrefixRE      = regexp.MustCompile(`(?i)^(?:this\s+)?fact\s+`)
	cliStoreNameRE            = regexp.MustCompile(`(?i)(?:my name is|call me|i(?:'m| am) called)\s+([A-Za-z][A-Za-z0-9_.\- ]{1,40})`)
)

type cliStoreFact struct {
	text, category, source string
	confidence             float64
}

const (
	cliStoreMinInjectConfidence = 0.55
	cliStoreAutoAccept          = 0.85
	cliStoreMaxFactsPerPass     = 6
)

func cliStoreWS(s string) string { return memWhitespaceRE.ReplaceAllString(strings.TrimSpace(s), " ") }

// cliStoreExtractHeuristicFacts is partner_memory.extract_heuristic_facts.
func cliStoreExtractHeuristicFacts(userText string) []cliStoreFact {
	text := strings.TrimSpace(userText)
	if text == "" || looksLikeSecret(text) {
		return nil
	}
	explicit := isExplicitRememberIntent(text)
	seen := map[string]bool{}
	var found []cliStoreFact
	for _, p := range cliStorePrefPatterns {
		for _, m := range p.re.FindAllStringSubmatchIndex(text, -1) {
			full := text[m[0]:m[1]]
			groups := len(m)/2 - 1
			raw := strings.TrimSpace(full)
			if groups >= 1 && m[2] >= 0 {
				raw = strings.TrimSpace(text[m[2]:m[3]])
			}
			if groups >= 2 && m[4] >= 0 {
				raw = cliStoreWS(full)
			}
			raw = strings.Trim(memWhitespaceRE.ReplaceAllString(raw, " "), " \t\"'")
			raw = strings.TrimSpace(cliStoreFactPrefixRE.ReplaceAllString(raw, ""))
			isExp := explicit || p.confidence >= 0.95
			var factText string
			if !isStableFactText(raw, isExp) {
				f := cliStoreWS(full)
				if !isStableFactText(f, isExp) {
					continue
				}
				factText = truncateRunes(f, 280)
			} else if utf8.RuneCountInString(raw) < 20 && full != "" && p.confidence < 0.95 {
				factText = truncateRunes(cliStoreWS(full), 280)
			} else {
				factText = truncateRunes(raw, 280)
			}
			if !isStableFactText(factText, isExp) {
				continue
			}
			key := normalizeFactKey(factText)
			if seen[key] {
				continue
			}
			seen[key] = true
			src := "heuristic"
			if isExp {
				src = "explicit"
			}
			found = append(found, cliStoreFact{text: factText, category: p.category, confidence: p.confidence, source: src})
			if len(found) >= cliStoreMaxFactsPerPass {
				return found
			}
		}
	}
	for _, extra := range cliStoreExtractLivingFacts(text) {
		key := normalizeFactKey(extra.text)
		if seen[key] {
			continue
		}
		seen[key] = true
		found = append(found, extra)
		if len(found) >= cliStoreMaxFactsPerPass {
			return found
		}
	}
	return found
}

// cliStoreExtractLivingFacts is living.extract_living_facts.
func cliStoreExtractLivingFacts(userText string) []cliStoreFact {
	text := strings.TrimSpace(userText)
	if text == "" || looksLikeSecret(text) {
		return nil
	}
	seen := map[string]bool{}
	var found []cliStoreFact
	for _, p := range cliStoreLivingPatterns {
		for _, m := range p.re.FindAllStringIndex(text, -1) {
			raw := cliStoreWS(text[m[0]:m[1]])
			if cliStoreLivingEphemeralRE.MatchString(raw) && (p.category == "life" || p.category == "goal") {
				continue
			}
			if !isStableFactText(raw, p.confidence >= 0.9) {
				continue
			}
			key := normalizeFactKey(raw)
			if seen[key] {
				continue
			}
			seen[key] = true
			found = append(found, cliStoreFact{text: truncateRunes(raw, 280), category: p.category, confidence: p.confidence, source: "living"})
			if len(found) >= 6 {
				return found
			}
		}
	}
	return found
}

func cliStoreShouldAutoAccept(f cliStoreFact) bool {
	return f.confidence >= cliStoreAutoAccept && isStableFactText(f.text, false) && !looksLikeSecret(f.text)
}

// cliStoreInferAuthority / cliStoreIsInferredSource are remedy.memory.authority's.
func cliStoreInferAuthority(source, sessionID string) string {
	if isHiveSessionID(sessionID) {
		return "hive"
	}
	switch strings.ToLower(strings.TrimSpace(source)) {
	case "explicit", "user", "owner", "chat":
		return "owner"
	case "tool", "web", "browser", "http":
		return "tool"
	}
	return "agent"
}

func cliStoreIsInferredSource(source string) bool {
	switch strings.ToLower(strings.TrimSpace(source)) {
	case "explicit", "user", "owner":
		return false
	}
	return true
}

// cliStoreGentleDecay is partner_memory.apply_gentle_decay.
func cliStoreGentleDecay(facts []map[string]any, now time.Time) int {
	n := 0
	for _, f := range facts {
		if f["pinned"] == true || asString(f["source"]) == "explicit" {
			continue
		}
		c := cliStoreFloat(f["confidence"], 0.7)
		if c < cliStoreMinInjectConfidence {
			continue
		}
		t, err := cliStoreParseISO(cliStoreStr(f["last_referenced"], ""))
		if err != nil {
			continue
		}
		if now.Sub(t.T).Hours()/24 < 90 {
			continue
		}
		if cliStoreInt(f["reference_count"], 1) <= 2 {
			if nc := math.Max(0.35, c-0.08); nc < c {
				f["confidence"] = nc
				n++
			}
		}
	}
	return n
}

// distill is partner_memory.distill_user_text with no brief and no project:
// heuristic facts into the owner profile, explicit ones also as a note. Hive
// sessions never write the parent's memory.
func (s *CLIStore) distill(userText, sessionID string) error {
	if isHiveSessionID(sessionID) {
		return nil
	}
	candidates := cliStoreExtractHeuristicFacts(userText)
	p, err := s.loadOrCreateProfile("default")
	if err != nil {
		return err
	}
	work := map[string]any{"facts": func() []any {
		fl := cliStoreFactList(p)
		out := make([]any, len(fl))
		for i, f := range fl {
			out[i] = f
		}
		return out
	}()}
	added, reinforced := 0, 0
	nameChanged := false
	now := time.Now()
	for _, cand := range candidates {
		if cand.category == "identity" {
			if m := cliStoreNameRE.FindStringSubmatch(cand.text); m != nil {
				name := strings.TrimSpace(m[1])
				if name != "" && !looksLikeSecret(name) {
					name = truncateRunes(name, 60)
					if cur, _ := p.vals["display_name"].(string); cur != name {
						p.set("display_name", name)
						nameChanged = true
					}
				}
			}
		}
		conf := cand.confidence
		force := cand.source == "explicit" || cand.confidence >= 0.95
		if !force && !cliStoreShouldAutoAccept(cand) {
			conf = math.Min(cand.confidence, cliStoreMinInjectConfidence-0.01)
		}
		// Python passes inferred=None and authority=None: a reinforced fact
		// keeps both, a new one gets is_inferred_source / infer_authority.
		inferred := cliStoreIsInferredSource(cand.source)
		authority := cliStoreInferAuthority(cand.source, sessionID)
		facts, _ := work["facts"].([]any)
		for _, item := range facts {
			if f, ok := item.(map[string]any); ok && factNearDuplicate(asString(f["fact"]), cliStoreWS(cand.text)) {
				inferred, authority = true, ""
				break
			}
		}
		action := applyProfileFactUpsert(work, profileFactWrite{
			Text: cand.text, Category: cand.category, Source: cand.source,
			Authority: authority, SessionID: sessionID,
			Confidence: conf, Force: force, Inferred: inferred,
		}, now)
		switch action {
		case "skipped":
			continue
		case "added":
			added++
			if force || cand.source == "explicit" {
				title := truncateRunes(cand.text, 80)
				if title == "" {
					title = "Remembered"
				}
				_ = s.Upsert(NewCLIMemoryEntry("note", title, cand.text, nil, 0.85))
			}
		default:
			reinforced++
		}
	}
	facts, _ := work["facts"].([]any)
	maps := make([]map[string]any, 0, len(facts))
	for _, item := range facts {
		if f, ok := item.(map[string]any); ok {
			maps = append(maps, f)
		}
	}
	decayed := cliStoreGentleDecay(maps, now)
	p.set("facts", facts)
	if added > 0 || reinforced > 0 || nameChanged || decayed > 0 {
		return s.saveProfile(p, "default")
	}
	return nil
}
