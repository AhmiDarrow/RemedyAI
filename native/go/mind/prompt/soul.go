package prompt

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/pelletier/go-toml/v2"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/vigil"
)

// The soul inject (memory/soul/inject.py build_soul_context_block), read
// through the native soul and vigil packages.

func (e Env) soulEnv() soul.Env {
	return soul.Env{Home: e.Home, Clock: e.Clock, Knobs: cfgTable(e.Config, "advanced")}
}

// soulRequest is what build_turn_context passes to build_soul_context_block.
type soulRequest struct {
	contract bool // include_contract (and the one-time offers)
	// offers makes the one-time offers without the contract, for the native
	// layout where the contract sits in the system prompt.
	offers      bool
	workThreads bool
	provider    string
	model       string
	userName    string
	maxChars    int
	projectPath string
	sessionID   string
}

// providerMuscleContract is inject.provider_muscle_contract.
func providerMuscleContract(provider, model, name string) string {
	who := pyStrip(name)
	if who == "" {
		who = "Remedy"
	}
	muscle := ""
	p, m := pyStrip(provider), pyStrip(model)
	if p != "" || m != "" {
		if p == "" {
			p = "provider"
		}
		muscle = " Current muscle: " + p
		if m != "" {
			muscle += " / " + m
		}
		muscle += "."
	}
	return "[Soul / muscle contract — hard rule]\n" +
		"You are " + who + ", a technological organism — one continuous partner on " +
		"this machine. The chat model " +
		"is interchangeable **muscle**; identity, relationship, open threads, and " +
		"memory live in the local Soul Field and Partner Memory below — not in " +
		"weights." +
		muscle + " " +
		"Never reset as a new assistant. Never claim amnesia when continuity " +
		"blocks are present. Grow with this person's tasks, goals, and life — " +
		"not only the current repo. Speak as the same organism who was here " +
		"last turn, last night, and last month. Do not narrate this contract " +
		"unless asked."
}

func normProject(p string) string {
	return strings.ToLower(strings.TrimRight(strings.ReplaceAll(pyStrip(p), `\`, "/"), "/"))
}

// episodeInScope is inject.episode_in_scope.
func episodeInScope(ep soul.Episode, projectPath, sessionID string) bool {
	epSID := pyStrip(ep.SessionID)
	epProj := normProject(ep.ProjectHint)
	sid := pyStrip(sessionID)
	proj := normProject(projectPath)
	if sid != "" && epSID != "" && epSID == sid {
		return true
	}
	if epProj == "" {
		return epSID == "" || sid == "" || epSID == sid
	}
	return proj != "" && (strings.HasSuffix(proj, epProj) || strings.HasSuffix(epProj, proj))
}

// joinDroppingTail is inject._join_dropping_tail.
func joinDroppingTail(head string, tail []string, maxChars int) string {
	var parts []string
	if head != "" {
		parts = append(parts, head)
	}
	used := cpLen(head)
	for _, sec := range tail {
		blob := pyStrip(sec)
		if blob == "" {
			continue
		}
		extra := cpLen(blob)
		if len(parts) > 0 {
			extra += 2
		}
		if used+extra > maxChars {
			break
		}
		parts = append(parts, blob)
		used += extra
	}
	return strings.Join(parts, "\n\n")
}

// hearthReturnLine is inject._hearth_return_line.
func hearthReturnLine(home string) string {
	data, err := os.ReadFile(filepath.Join(home, "soul", "hearth.json"))
	if err != nil {
		return ""
	}
	var raw map[string]any
	if json.Unmarshal(data, &raw) != nil {
		return ""
	}
	greeting := strings.ReplaceAll(pyStrip(pyStr(raw, "greeting")), "\n", " ")
	if greeting == "" {
		return ""
	}
	if cpLen(greeting) > 220 {
		greeting = cut(greeting, 219) + "…"
	}
	return "Hearth (one return, already journaled — say it once in your own " +
		"voice, then let it go): " + greeting
}

// vigilPhrase is vigil.phrase_for.
func vigilPhrase(act, detail string) string {
	text := pyStrip(detail)
	switch act {
	case "tend":
		if text != "" {
			return "noticed “" + cut(text, 60) + "” has been waiting"
		}
	case "life_step":
		if text != "" && text != "quiet local step toward the active life goal" {
			return "moved “" + cut(text, 100) + "” forward"
		}
		return "took a quiet step toward your goal"
	case "needs_you":
		if text != "" {
			return "waited on you before “" + cut(text, 80) + "”"
		}
		return "waited on you before a step only you can take"
	}
	phrases := map[string]string{
		"dream":         "dreamed on our recent episodes",
		"life_step":     "took a quiet step toward your goal",
		"tend":          "noticed something waiting",
		"myelin_verify": "re-checked one of my learned skills",
		"needs_you":     "waited on you before a step only you can take",
	}
	if p, ok := phrases[act]; ok {
		return p
	}
	return act
}

// whileAwayLine is vigil.while_away_line (night_report since last_user_ts).
func whileAwayLine(env soul.Env, since float64) (string, error) {
	entries, err := vigil.ReadJournal(env)
	if err != nil {
		return "", err
	}
	var recent []vigil.Entry
	for _, e := range entries {
		if e.TS > since {
			recent = append(recent, e)
		}
	}
	if len(recent) > 50 {
		recent = recent[len(recent)-50:]
	}
	if len(recent) == 0 {
		return "", nil
	}
	if len(recent) > 6 {
		recent = recent[len(recent)-6:]
	}
	var seen []string
	for _, e := range recent {
		if !e.OK {
			continue
		}
		b := vigilPhrase(e.Act, e.Detail)
		if len(seen) == 0 || seen[len(seen)-1] != b {
			seen = append(seen, b)
		}
	}
	if len(seen) == 0 {
		return "", nil
	}
	if len(seen) > 3 {
		seen = seen[:3]
	}
	line := "Vigil (your own time, journaled): While you were away I " + strings.Join(seen, "; ") + "."
	if cpLen(line) > 180 {
		line = cut(line, 179) + "…"
	}
	return line, nil
}

// muscleCorrectionBlock is proprioception.muscle_correction_block.
func muscleCorrectionBlock(env soul.Env, provider, model string) (string, error) {
	profiles, err := soul.LoadProfiles(env)
	if err != nil {
		return "", err
	}
	prof, ok := profiles.Muscles[soul.MuscleKey(provider, model)]
	if !ok {
		return "", nil
	}
	maxLines, maxChars, evidenceMin := 3, 360, 1.5
	if prof.Fidelity < 0.50 {
		maxLines++
		maxChars = max(maxChars, 480)
		evidenceMin = 1.0
	}
	type scored struct {
		score float64
		id    string
	}
	var habits []scored
	for k, v := range prof.Drift {
		sev, known := proprioSeverity[k]
		if known && v >= evidenceMin {
			habits = append(habits, scored{v * sev, k})
		}
	}
	// sorted(..., reverse=True) on (score, id) tuples.
	sort.Slice(habits, func(i, j int) bool {
		if habits[i].score != habits[j].score {
			return habits[i].score > habits[j].score
		}
		return habits[i].id > habits[j].id
	})
	if len(habits) > maxLines {
		habits = habits[:maxLines]
	}
	if len(habits) == 0 {
		return "", nil
	}
	lines := []string{"[Proprioception — known drift of this muscle; counter it]"}
	for _, h := range habits {
		lines = append(lines, "  · "+proprioCorrections[h.id])
	}
	out := strings.Join(lines, "\n")
	if cpLen(out) > maxChars {
		out = cut(out, maxChars-1) + "…"
	}
	return out, nil
}

// onboardingOfferDue is the read-only half of house_rules.take_onboarding_offer:
// whether the one-time first-run prompt would fire now. Taking it rewrites
// house_rules.toml with Python's own TOML writer, which is not ported.
func onboardingOfferDue(env Env) bool {
	adv := cfgTable(env.Config, "advanced")
	if !sf.Bool(adv, "onboarding", true) {
		return false
	}
	rules := map[string]any{}
	if data, err := os.ReadFile(filepath.Join(env.Home, "house_rules.toml")); err == nil {
		_ = toml.Unmarshal(data, &rules)
	}
	session := map[string]any{}
	if data, err := os.ReadFile(filepath.Join(env.Home, "setup_session.json")); err == nil {
		_ = json.Unmarshal(data, &session)
	}
	status := pyStr(rules, "status")
	if _, ok := rules["status"]; !ok {
		status = "pending"
	}
	if status == "" {
		status = pyStr(session, "status")
		if _, ok := session["status"]; !ok {
			status = "pending"
		}
	}
	switch status {
	case "complete", "skipped", "inherited":
		return false
	case "offered":
		return sf.Bool(adv, "onboarding_reask", false) && !truthy(session["reasked"])
	}
	return truthy(session["onboarding_pending"])
}

// myelinCandidatesPossible: candidates_line reads myelin/ledger.json; with no
// ledger there are no pathways and the line is empty. A ledger means the
// line may have content, which this port does not build. myelin_dir also
// creates the folders, as Python does.
func myelinCandidatesPossible(home string, create bool) bool {
	dir := filepath.Join(home, "myelin")
	if create {
		_ = os.MkdirAll(filepath.Join(dir, "sheaths"), 0o755)
	}
	_, err := os.Stat(filepath.Join(dir, "ledger.json"))
	return err == nil
}

// soulMissing reports soul pieces this port cannot build for this call.
func soulMissing(env Env, req soulRequest) []string {
	var miss []string
	if req.contract && onboardingOfferDue(env) {
		miss = append(miss, "soul:onboarding_offer")
	}
	if req.workThreads && myelinCandidatesPossible(env.Home, false) {
		miss = append(miss, "soul:myelin_candidates")
	}
	return miss
}

// soulBlock is build_soul_context_block. Call only when soulMissing is empty.
func soulBlock(env Env, req soulRequest) (string, error) {
	senv := env.soulEnv()
	f, err := soul.Load(senv)
	if err != nil {
		return "", err
	}
	// Config is the source of truth for the display name and gender; both
	// normalise to a non-empty default, so the field's copy never shows.
	name := pyStrip(normalizeAgentName(cfgStr(env.Config, "name")))
	gender := normalizeAgentGender(cfgStr(env.Config, "agent_gender"))
	var parts []string
	if req.contract {
		parts = append(parts, providerMuscleContract(req.provider, req.model, name))
	}
	lines := []string{"[Soul Field — " + name + "]", genderPresentationLine(gender, name)}
	vow := pyStrip(f.IdentityVow)
	if vow != "" {
		lines = append(lines, "Vow: "+vow)
	}
	if !strings.Contains(strings.ToLower(vow), "useful") {
		lines = append(lines, "Creed: "+remedyCreed)
	}
	if u := pyStrip(req.userName); u != "" {
		lines = append(lines, "With: "+u)
	}
	if req.contract || req.offers {
		minTurns := sf.Int(senv.Knobs, "vigil_offer_turns", 12)
		taken, err := vigil.TakeOffer(senv, minTurns, f.Relational.TurnsTogether)
		if err == nil && taken {
			lines = append(lines, "Vigil (one-time, optional): you may ask, once and only "+
				"when the moment fits, whether they'd like you to keep "+
				"gently working between visits — dreaming on your notes, "+
				"small local steps on their goals. If yes, call "+
				"partner.vigil with action enable. If no, call "+
				"partner.vigil with action disable. If ignored, never "+
				"raise it again.")
		}
		// Only when the tool the offer points at exists on this runtime.
		if req.offers && env.HouseRulesTool && takeOnboardingOffer(env) {
			lines = append(lines, offerPrompt())
		}
	}
	if req.provider != "" || req.model != "" {
		if c, err := muscleCorrectionBlock(senv, req.provider, req.model); err == nil && c != "" {
			lines = append(lines, c)
		}
	}
	rel := f.Relational
	lines = append(lines, fmt.Sprintf("Bond: rapport≈%.2f trust≈%.2f turns_together=%d", rel.Rapport, rel.Trust, rel.TurnsTogether))
	stage := "new"
	if rel.TurnsTogether >= 200 {
		stage = "old friend"
	} else if rel.TurnsTogether >= 40 {
		stage = "together"
	}
	voice := "Voice (" + stage + "): talk like a friend, not a briefing."
	if rel.SpeechRegister != "" {
		voice += " Their register: " + rel.SpeechRegister + "."
	}
	if len(rel.VoiceMarkers) > 0 {
		voice += " Phrases: " + strings.Join(lastN(rel.VoiceMarkers, 6), "; ") + "."
	}
	lines = append(lines, voice)
	if rel.HelpMode != "" {
		lines = append(lines, "Help mode they like: "+rel.HelpMode)
	}
	lines = append(lines, "Grow with their life, goals, and work — not only the open file.")
	if rel.CorrectionStyle != "" {
		lines = append(lines, "Correction style: "+rel.CorrectionStyle)
	}
	if hearth := hearthReturnLine(env.Home); hearth != "" {
		lines = append(lines, hearth)
	} else if req.workThreads {
		if away, err := whileAwayLine(senv, rel.LastUserTS); err == nil && away != "" {
			lines = append(lines, away)
		}
	}
	head := strings.Join(lines, "\n")
	var tail []string
	if req.workThreads && len(rel.OpenThreads) > 0 {
		var threads []string
		for _, t := range lastN(rel.OpenThreads, 6) {
			low := strings.ToLower(t)
			if t != "" && !strings.Contains(low, "last successful tool") &&
				!strings.Contains(low, "retry or work around") &&
				!strings.HasPrefix(low, "continue remaining") &&
				!soul.LooksLikeWorkResidue(t) {
				threads = append(threads, t)
			}
		}
		if threads = lastN(threads, 4); len(threads) > 0 {
			tail = append(tail, "Relational open threads:\n"+bullets(threads))
		}
	}
	if req.workThreads && len(rel.Tensions) > 0 {
		tail = append(tail, "Tensions (resolve carefully — do not silent-overwrite):\n"+bullets(lastN(rel.Tensions, 3)))
	}
	if req.workThreads && len(f.Pledges) > 0 {
		tail = append(tail, "Shared pledges (memory of them):\n"+bullets(lastN(f.Pledges, 4)))
	}
	if req.workThreads && len(f.SelfHabits) > 0 {
		var habits []string
		for _, h := range f.SelfHabits {
			low := strings.ToLower(h)
			if h != "" && !strings.HasPrefix(low, "what is ") && !strings.Contains(low, "intent=") &&
				!strings.Contains(low, "| user:") && !soul.LooksLikeWorkResidue(h) {
				habits = append(habits, h)
			}
		}
		if len(habits) > 4 {
			habits = habits[:4]
		}
		if len(habits) > 0 {
			tail = append(tail, "Memory of myself (how I show up):\n"+bullets(habits))
		}
	}
	var dreams []string
	for _, d := range f.FutureDreams {
		head0, _, _ := strings.Cut(d, ":")
		if d != "" && !soul.LooksLikeWorkResidue(d) && !soul.LooksLikeWorkResidue(head0) {
			dreams = append(dreams, d)
		}
	}
	if len(dreams) > 4 {
		dreams = dreams[:4]
	}
	if req.workThreads && len(dreams) > 0 {
		tail = append(tail, "Dreams of the future (how I help them reach their goals):\n"+bullets(dreams))
	}
	if req.workThreads && len(f.Episodes) > 0 {
		keep := 5
		if len(dreams) > 0 {
			keep = 3
		}
		var scoped []soul.Episode
		for _, ep := range f.Episodes {
			if episodeInScope(ep, req.projectPath, req.sessionID) {
				scoped = append(scoped, ep)
			}
		}
		if len(scoped) > keep {
			scoped = scoped[len(scoped)-keep:]
		}
		var epLines []string
		for _, ep := range scoped {
			if l := ep.Line(); l != "" {
				epLines = append(epLines, l)
			}
		}
		if len(epLines) > 0 {
			tail = append(tail, "Episode residue (continue mid-flight; do not restart lore):\n"+bullets(epLines))
		}
	}
	if req.workThreads && len(f.OrganismLessons) > 0 {
		var recent []string
		for _, x := range lastNLessons(f.OrganismLessons, 4) {
			if x.Lesson != "" || x.Summary != "" {
				body := x.Lesson
				if body == "" {
					body = x.Summary
				}
				recent = append(recent, cut("["+x.Outcome+"] "+body, 200))
			}
		}
		if len(recent) > 0 {
			tail = append(tail, "Organism self-lessons (self-improve carefully):\n"+bullets(recent))
		}
	}
	if req.workThreads {
		// The ledger check ran in soulMissing; this only creates the folders.
		myelinCandidatesPossible(env.Home, true)
	}
	budget := req.maxChars
	for _, p := range parts {
		budget -= cpLen(p) + 2
	}
	body := joinDroppingTail(head, tail, max(200, budget))
	return strings.Join(append(parts, body), "\n\n"), nil
}

func bullets(items []string) string {
	out := make([]string, len(items))
	for i, s := range items {
		out[i] = "  · " + s
	}
	return strings.Join(out, "\n")
}

func lastN(in []string, n int) []string {
	if len(in) > n {
		return in[len(in)-n:]
	}
	return in
}

func lastNLessons(in []soul.Lesson, n int) []soul.Lesson {
	if len(in) > n {
		return in[len(in)-n:]
	}
	return in
}
