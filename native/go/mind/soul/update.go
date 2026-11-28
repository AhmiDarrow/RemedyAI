package soul

import (
	"crypto/rand"
	"encoding/hex"
	"math"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

// Heuristic soul-field updates after a turn (update.py). No model call.

var (
	frustratedRE = regexp.MustCompile(`(?i)\b(frustrated|annoying|broken|why (?:is|does|won'?t)|this (?:sucks|is broken)|` +
		`stop (?:doing|saying)|you(?:'re| are) (?:wrong|useless)|wtf|damn it)\b`)
	playfulRE     = regexp.MustCompile(`(?i)\b(lol|lmao|haha|😂|😄|joke|funny|silly|let'?s gooo+)\b`)
	focusedRE     = regexp.MustCompile(`(?i)\b(implement|fix|ship|review|debug|continue|keep going|next step|don'?t stop|finish)\b`)
	exploratoryRE = regexp.MustCompile(`(?i)\b(what if|curious|wonder|explore|brainstorm|ideas?|maybe we)\b`)
	correctionRE  = regexp.MustCompile(`(?i)\b(no[,.]?\s+(?:that'?s|not)|actually\b|wrong\b|don'?t\b|stop\b|` +
		`I (?:said|meant|wanted)|correct(?:ion)?\b|not what I)\b`)
	bluntRE     = regexp.MustCompile(`(?i)\b(just (?:do it|fix it|ship it)|no fluff|be brief|cut the|skip the)\b`)
	gentleRE    = regexp.MustCompile(`(?i)\b(please|when you can|no rush|gently|softly)\b`)
	pairRE      = regexp.MustCompile(`(?i)\b(let'?s|we should|together|pair|with me)\b`)
	doerRE      = regexp.MustCompile(`(?i)\b(just (?:fix|implement|do)|go ahead and|don'?t ask|auto)\b`)
	pledgeRE    = regexp.MustCompile(`(?i)\b(?:promise|always remember|from now on|we(?:'ll| will) always|never forget that)\b(.{8,120})`)
	voiceSnipRE = regexp.MustCompile(`(?i)\b(ship it|make it so|be real|no theater|just works|feels like|no fluff)\b`)
	casualRE    = regexp.MustCompile(`(?i)\b(yeah|yep|nah|gonna|wanna|kinda|idk|lol|hey|feels like)\b|` +
		`\b\w+n't\b|\b(?:it's|i'm|that's|you're)\b`)
	thanksRE   = regexp.MustCompile(`(?i)\b(thanks|thank you|perfect|great|love (?:it|this))\b`)
	revisionRE = regexp.MustCompile(`(?i)\b(actually|never mind|forget that|not anymore|changed my mind)\b`)
	laterRE    = regexp.MustCompile(`(?i)\b(later|tomorrow|remind|don'?t forget|next time)\b`)
	spaceRE    = regexp.MustCompile(`\s+`)
	clauseRE   = regexp.MustCompile(`[.!?\n]`)
	reconRE    = regexp.MustCompile(`[a-zA-Z][a-zA-Z0-9_]{2,}`)
)

var reconStop = map[string]bool{}

func init() {
	for _, w := range strings.Fields("the a an and or but to of in on for with it this that is are was were be i you we he she they me my your our do did does can could would should just now then so if not no yes ok okay let lets get got go going make want need have has had will won about into out up down over what how why when where who which as at by from") {
		reconStop[w] = true
	}
}

func reconTokens(text string) map[string]bool {
	out := map[string]bool{}
	for _, w := range reconRE.FindAllString(strings.ToLower(text), -1) {
		if !reconStop[w] {
			out[w] = true
		}
	}
	return out
}

// reconsolidate hardens up to three episodes this turn is about.
func reconsolidate(f *Field, tokens map[string]bool, now float64) {
	if len(tokens) == 0 {
		return
	}
	type hit struct{ overlap, i int }
	var scored []hit
	for i, ep := range f.Episodes {
		n := 0
		for w := range reconTokens(ep.Arc + " " + ep.OpenThread) {
			if tokens[w] {
				n++
			}
		}
		if n >= 2 {
			scored = append(scored, hit{n, i})
		}
	}
	sort.SliceStable(scored, func(a, b int) bool { return scored[a].overlap > scored[b].overlap })
	for k, h := range scored {
		if k == 3 {
			break
		}
		ep := &f.Episodes[h.i]
		ep.Strength = math.Min(1, ep.Strength+0.12)
		ep.Recalls++
		ep.LastRecallTS = now
	}
}

func inferRegister(t string) string {
	t = strings.TrimSpace(t)
	if t == "" {
		return ""
	}
	casual := casualRE.MatchString(t)
	if utf8.RuneCountInString(t) < 90 {
		if casual {
			return "casual-short"
		}
		return "terse"
	}
	if casual {
		return "casual"
	}
	return ""
}

func stance(t string) string {
	switch {
	case frustratedRE.MatchString(t) || (correctionRE.MatchString(t) && bluntRE.MatchString(t)):
		return "frustrated"
	case playfulRE.MatchString(t):
		return "playful"
	case exploratoryRE.MatchString(t):
		return "exploratory"
	case focusedRE.MatchString(t):
		return "focused"
	}
	return "steady"
}

func valence(t string) float64 {
	v := 0.0
	if frustratedRE.MatchString(t) {
		v -= 0.35
	}
	if playfulRE.MatchString(t) {
		v += 0.25
	}
	if thanksRE.MatchString(t) {
		v += 0.3
	}
	if correctionRE.MatchString(t) {
		v -= 0.1
	}
	return math.Max(-1, math.Min(1, v))
}

// compressArc is _compress_arc without a session brief (the Go runtime has none).
func compressArc(user, assistant string) string {
	u := sf.Cut(spaceRE.ReplaceAllString(strings.TrimSpace(user), " "), 140)
	arc := "continued session"
	if u != "" {
		arc = "user: " + u
	}
	a := spaceRE.ReplaceAllString(strings.TrimSpace(assistant), " ")
	if utf8.RuneCountInString(a) > 40 {
		if a0 := sf.Cut(strings.TrimSpace(clauseRE.Split(a, 2)[0]), 90); a0 != "" {
			arc += " | did: " + a0
		}
	}
	return sf.Cut(arc, 240)
}

func openThread(user string) string {
	u := strings.TrimSpace(user)
	if laterRE.MatchString(u) {
		return sf.Cut(u, 160)
	}
	return ""
}

// joinMuscle is "/".join(x for x in (provider, model) if x).
func joinMuscle(provider, model string) string {
	var parts []string
	for _, p := range []string{strings.TrimSpace(provider), strings.TrimSpace(model)} {
		if p != "" {
			parts = append(parts, p)
		}
	}
	return strings.Join(parts, "/")
}

func newID() string {
	var b [5]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// Turn is what one finished turn tells the soul.
type Turn struct {
	UserText, AssistantText string
	SessionID               string
	Provider, Model         string
	ProjectPath             string
}

func addHabit(f *Field, h string) {
	if !sf.Contains(f.SelfHabits, h) {
		f.SelfHabits = append(f.SelfHabits, h)
	}
}

// UpdateAfterTurn micro-updates the field after a turn and folds the
// rendered reply into the muscle's proprioception profile. Secret-looking
// turns only count the turn. Hive daughters never write the owner's soul.
func UpdateAfterTurn(env Env, t Turn) (*Field, error) {
	if strings.HasPrefix(strings.TrimSpace(t.SessionID), "hive_") {
		return Load(env)
	}
	ut, at := strings.TrimSpace(t.UserText), strings.TrimSpace(t.AssistantText)
	now := sf.Unix(env.Now())
	if LooksLikeSecretSoul(ut) || LooksLikeSecretSoul(at) {
		return Mutate(env, func(f *Field) (bool, error) {
			f.Relational.TurnsTogether++
			f.Relational.LastUserTS = now
			return true, nil
		})
	}
	f, err := Mutate(env, func(f *Field) (bool, error) {
		applyTurn(f, t, ut, at, now)
		return true, nil
	})
	if err != nil {
		return f, err
	}
	if _, err := ObserveRender(env, at, ut, t.Provider, t.Model); err != nil {
		return f, err
	}
	return f, nil
}

func applyTurn(f *Field, t Turn, ut, at string, now float64) {
	rel := &f.Relational
	rel.TurnsTogether++
	rel.LastUserTS = now
	val := valence(ut)
	rel.LastValence = val
	rel.Rapport = 0.92*rel.Rapport + 0.08*(0.55+0.35*val)
	if correctionRE.MatchString(ut) {
		rel.Trust = 0.94*rel.Trust + 0.06*0.45
		switch {
		case bluntRE.MatchString(ut):
			rel.CorrectionStyle = "blunt"
		case gentleRE.MatchString(ut):
			rel.CorrectionStyle = "gentle"
		case rel.CorrectionStyle == "":
			rel.CorrectionStyle = "direct"
		}
	} else {
		rel.Trust = 0.96*rel.Trust + 0.04*(0.55+0.3*math.Max(0, val))
	}
	if bluntRE.MatchString(ut) && rel.HelpMode == "" {
		rel.HelpMode = "silent-doer"
	}
	if pairRE.MatchString(ut) {
		rel.HelpMode = "pair"
	}
	if doerRE.MatchString(ut) {
		rel.HelpMode = "silent-doer"
	}
	if exploratoryRE.MatchString(ut) && rel.HelpMode == "" {
		rel.HelpMode = "sparring"
	}
	for _, m := range voiceSnipRE.FindAllString(ut, -1) {
		phrase := strings.ToLower(strings.TrimSpace(m))
		dup := false
		for _, v := range rel.VoiceMarkers {
			if strings.ToLower(v) == phrase {
				dup = true
			}
		}
		if phrase != "" && !dup {
			rel.VoiceMarkers = append(rel.VoiceMarkers, phrase)
		}
	}
	if reg := inferRegister(ut); reg != "" {
		if !strings.HasPrefix(rel.SpeechRegister, "casual") || strings.HasPrefix(reg, "casual") {
			rel.SpeechRegister = reg
		}
	}
	for _, m := range pledgeRE.FindAllString(ut, -1) {
		body := strings.TrimSpace(m)
		if body != "" && !LooksLikeSecretSoul(body) {
			if key := f.FindPledgeKey(body); !sf.Contains(f.Pledges, key) {
				f.Pledges = append(f.Pledges, key)
			}
			f.TouchPledgeTrace(body, now)
		}
	}
	if revisionRE.MatchString(ut) {
		// Python checked the bare snippet but stored "revision: …", so a
		// repeated line was stored again each time; the stored form is checked.
		if line := "revision: " + sf.Cut(ut, 160); ut != "" && !sf.Contains(rel.Tensions, line) {
			rel.Tensions = append(rel.Tensions, line)
		}
	}
	openT := openThread(ut)
	if openT != "" && !LooksLikeWorkResidue(openT) && !sf.Contains(rel.OpenThreads, openT) {
		rel.OpenThreads = append(rel.OpenThreads, openT)
		if len(rel.OpenThreads) > 10 {
			rel.OpenThreads = rel.OpenThreads[len(rel.OpenThreads)-10:]
		}
	}
	if at != "" && rel.HelpMode == "silent-doer" && utf8.RuneCountInString(at) < 800 {
		addHabit(f, "Do the work they want, and keep them posted in the chat as it goes.")
	}
	if rel.CorrectionStyle == "blunt" {
		addHabit(f, "When corrected, fix fast — no defensive monologue.")
	}
	reconsolidate(f, reconTokens(ut), now)
	muscle := joinMuscle(t.Provider, t.Model)
	ep := Episode{
		ID:           newID(),
		TS:           now,
		Arc:          compressArc(ut, at),
		UserStance:   stance(ut),
		OpenThread:   openT,
		Valence:      val,
		Muscle:       sf.Cut(muscle, 80),
		SessionID:    sf.Cut(t.SessionID, 80),
		ProjectHint:  sf.Tail(strings.ReplaceAll(t.ProjectPath, `\`, "/"), 80),
		Strength:     EncodeStrength(val),
		LastRecallTS: now,
	}
	if strings.TrimSpace(ep.Arc) != "" {
		f.Episodes = append(f.Episodes, ep)
	}
}
