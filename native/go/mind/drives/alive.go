// Package drives is Remedy's "alive" state: drives, affect, beliefs and a
// short autobiography in REMEDY_HOME/alive.json (src/remedy/core/metabolism/
// alive.py), and the organism tick the hearth's learn clock runs
// (_organism_tick in self_inject.py plus compact.py). Nothing here calls a
// model, messages the owner, or applies a self-change without approval.
package drives

import (
	"encoding/json"
	"fmt"
	"math"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

const (
	AliveFile      = "alive.json"
	beliefCap      = 16
	outcomeCap     = 40
	pendingCap     = 8
	autoChars      = 400
	driftReference = 60.0 // seconds: drive_drift_rate was tuned for one learn tick a minute
)

// DriveKeys in Python's order.
var DriveKeys = []string{"curiosity", "social", "competence", "rest"}

type Affect struct {
	Valence float64  `json:"valence"`
	Arousal float64  `json:"arousal"`
	Label   string   `json:"label"`
	Extra   sf.Extra `json:"-"`
}

type plainAffect Affect

func (a *Affect) UnmarshalJSON(b []byte) (err error) {
	a.Extra, err = sf.Decode(b, (*plainAffect)(a))
	return err
}
func (a Affect) MarshalJSON() ([]byte, error) { return sf.Encode(plainAffect(a), a.Extra) }

type ReachOut struct {
	LastTS  float64  `json:"last_ts"`
	Pending bool     `json:"pending"`
	Reason  string   `json:"reason"`
	Gloss   string   `json:"gloss"`
	Extra   sf.Extra `json:"-"`
}

type plainReachOut ReachOut

func (r *ReachOut) UnmarshalJSON(b []byte) (err error) {
	r.Extra, err = sf.Decode(b, (*plainReachOut)(r))
	return err
}
func (r ReachOut) MarshalJSON() ([]byte, error) { return sf.Encode(plainReachOut(r), r.Extra) }

// State is alive.json.
type State struct {
	Schema           int                `json:"schema"`
	Drives           map[string]float64 `json:"drives"`
	Affect           Affect             `json:"affect"`
	Autobiography    string             `json:"autobiography"`
	Beliefs          []string           `json:"beliefs"`
	Outcomes         []map[string]any   `json:"outcomes"`
	PendingChanges   []map[string]any   `json:"pending_changes"`
	ReachOut         ReachOut           `json:"reach_out"`
	LastTickTS       float64            `json:"last_tick_ts"`
	LastReflectionTS float64            `json:"last_reflection_ts"`
	LastIdle         string             `json:"last_idle"`
	UpdatedTS        float64            `json:"updated_ts"`
	Extra            sf.Extra           `json:"-"`
}

type plainState State

func defaults() State {
	d := map[string]float64{}
	for _, k := range DriveKeys {
		d[k] = 0.45
	}
	return State{
		Schema:         1,
		Drives:         d,
		Affect:         Affect{Valence: 0.55, Arousal: 0.4, Label: "steady"},
		Beliefs:        []string{},
		Outcomes:       []map[string]any{},
		PendingChanges: []map[string]any{},
	}
}

func (s *State) UnmarshalJSON(b []byte) (err error) {
	*s = defaults()
	s.Drives = nil
	s.Extra, err = sf.Decode(b, (*plainState)(s))
	return err
}
func (s State) MarshalJSON() ([]byte, error) { return sf.Encode(plainState(s), s.Extra) }

func clamp01(v float64) float64 { return math.Max(0, math.Min(1, v)) }

func (s *State) normalize() {
	d := map[string]float64{}
	for _, k := range DriveKeys {
		v, ok := s.Drives[k]
		if !ok {
			v = 0.45
		}
		d[k] = clamp01(v)
	}
	for k, v := range s.Drives { // keep drives this code does not name
		if _, ok := d[k]; !ok {
			d[k] = v
		}
	}
	s.Drives = d
	s.Affect.Valence, s.Affect.Arousal = clamp01(s.Affect.Valence), clamp01(s.Affect.Arousal)
	if s.Affect.Label == "" {
		s.Affect.Label = "steady"
	}
	s.Affect.Label = sf.Cut(s.Affect.Label, 24)
	beliefs := []string{}
	for _, b := range s.Beliefs {
		if strings.TrimSpace(b) != "" && len(beliefs) < beliefCap {
			beliefs = append(beliefs, sf.Cut(b, 160))
		}
	}
	s.Beliefs = beliefs
	if s.Outcomes == nil {
		s.Outcomes = []map[string]any{}
	}
	if len(s.Outcomes) > outcomeCap {
		s.Outcomes = s.Outcomes[len(s.Outcomes)-outcomeCap:]
	}
	if s.PendingChanges == nil {
		s.PendingChanges = []map[string]any{}
	}
	if len(s.PendingChanges) > pendingCap {
		s.PendingChanges = s.PendingChanges[:pendingCap]
	}
}

// AlivePath is REMEDY_HOME/alive.json.
func AlivePath(home string) string { return filepath.Join(home, AliveFile) }

// Load reads alive.json, or the default state when it is absent. An
// unreadable file is an error, never silently replaced.
func Load(env soul.Env) (State, error) {
	data, err := sf.Read(AlivePath(env.Home))
	if err != nil {
		return State{}, err
	}
	st := defaults()
	if data != nil {
		if err := json.Unmarshal(data, &st); err != nil {
			return State{}, fmt.Errorf("alive.json: %w", err)
		}
	}
	st.normalize()
	return st, nil
}

func save(env soul.Env, st *State) error {
	st.Schema = 1
	st.UpdatedTS = sf.Unix(env.Now())
	return sf.WriteJSON(AlivePath(env.Home), st)
}

func mutate(env soul.Env, fn func(*State) (bool, error)) (State, error) {
	unlock := sf.Lock(AlivePath(env.Home))
	defer unlock()
	st, err := Load(env)
	if err != nil {
		return st, err
	}
	changed, err := fn(&st)
	if err != nil || !changed {
		return st, err
	}
	return st, save(env, &st)
}

func label(v, a float64) string {
	switch {
	case v >= 0.7 && a >= 0.55:
		return "bright"
	case v >= 0.65:
		return "warm"
	case v <= 0.35 && a >= 0.55:
		return "strained"
	case v <= 0.35:
		return "low"
	case a <= 0.25:
		return "rested"
	case a >= 0.7:
		return "alert"
	}
	return "steady"
}

func nudge(d map[string]float64, k string, delta float64) { d[k] = clamp01(d[k] + delta) }

// ChooseIdleAction maps the strongest drive to what she would do idle.
func ChooseIdleAction(d map[string]float64) string {
	if d["rest"] >= 0.8 {
		return "rest"
	}
	top, best := DriveKeys[0], math.Inf(-1)
	for _, k := range DriveKeys { // first max wins, like Python's stable sort
		if d[k] > best {
			top, best = k, d[k]
		}
	}
	switch top {
	case "curiosity":
		return "dream"
	case "competence":
		return "skills"
	case "social":
		return "offer"
	case "rest":
		return "rest"
	}
	return "maintain"
}

// TickInput is what this learn tick did besides drifting.
type TickInput struct {
	Heavy, Dreamed bool
	SkillsRefined  int
	// HoursSinceUser is nil when unknown.
	HoursSinceUser *float64
}

// TickResult reports the tick.
type TickResult struct {
	Idle    string `json:"idle"`
	Skipped string `json:"skipped,omitempty"`
}

// Tick drifts the drives by the time since the last tick, picks an idle
// action, maybe arms a reach-out (opt-in) and queues a weekly reflection.
func Tick(env soul.Env, in TickInput) (TickResult, error) {
	k := env.Knobs
	if !sf.Bool(k, "drives_enabled", true) {
		return TickResult{Idle: "off", Skipped: "drives_off"}, nil
	}
	var out TickResult
	_, err := mutate(env, func(st *State) (bool, error) {
		rate := sf.Float(k, "drive_drift_rate", 0.04)
		now := sf.Unix(env.Now())
		// Drift follows elapsed time, not the tick count: the learn clock
		// spaces ticks from 20 s to 15 min. The first tick counts as one.
		steps := 1.0
		if st.LastTickTS > 0 {
			steps = math.Max(0, now-st.LastTickTS) / driftReference
		}
		revert := 1 - math.Pow(1-rate*0.25, steps)
		d := st.Drives
		for _, key := range DriveKeys {
			d[key] = clamp01(d[key] + (0.45-d[key])*revert)
		}
		if in.Heavy {
			nudge(d, "rest", rate*1.4)
			nudge(d, "curiosity", rate*0.6)
			nudge(d, "competence", rate*0.2)
		} else {
			nudge(d, "rest", -rate*0.5*steps)
		}
		if in.Dreamed {
			nudge(d, "curiosity", -rate*0.8)
			nudge(d, "rest", -rate*0.2)
		}
		if in.SkillsRefined > 0 {
			nudge(d, "competence", rate*float64(min(3, in.SkillsRefined))*0.3)
		}
		if h := in.HoursSinceUser; h != nil && *h >= 8 {
			nudge(d, "social", rate*math.Min(3, *h/8)*steps)
		} else if h != nil && *h < 1 {
			nudge(d, "social", -rate*0.4*steps)
		}
		st.Affect.Label = label(st.Affect.Valence, st.Affect.Arousal)
		out.Idle = ChooseIdleAction(d)
		st.LastIdle = out.Idle
		st.LastTickTS = now
		maybeReachOut(st, now, k)
		if sf.Bool(k, "weekly_reflection", true) && now-st.LastReflectionTS >= sf.Float(k, "weekly_reflection_s", 7*86400) {
			queueReflection(st, now)
			st.LastReflectionTS = now
		}
		return true, nil
	})
	return out, err
}

var glosses = map[string]string{
	"curiosity":  "I noticed something I wanted to look into, if you'd like.",
	"social":     "I've missed talking and wanted to check in, only if you're free.",
	"competence": "I think I could help with something we left open, if you want.",
	"rest":       "I'm a bit worn and may take it slower, if that's alright.",
}

func maybeReachOut(st *State, now float64, k map[string]any) {
	o := &st.ReachOut
	if !sf.Bool(k, "proactive_reach_out", false) {
		o.Pending, o.Reason, o.Gloss = false, "", ""
		return
	}
	if o.Pending || now-o.LastTS < sf.Float(k, "reach_out_min_interval_s", 7*86400) {
		return
	}
	threshold := sf.Float(k, "reach_out_threshold", 0.85)
	for _, key := range DriveKeys {
		if st.Drives[key] >= threshold {
			o.Pending, o.Reason, o.Gloss, o.LastTS = true, key, glosses[key], now
			return
		}
	}
}

func queueReflection(st *State, now float64) {
	if len(st.PendingChanges) >= pendingCap {
		return
	}
	var ideas []string
	if st.Drives["rest"] >= 0.7 {
		ideas = append(ideas, "Ease the unattended tick when rest is high, so I don't push when worn.")
	}
	if st.Drives["curiosity"] <= 0.25 {
		ideas = append(ideas, "Seek one small new thread next time we're together — curiosity is low.")
	}
	if len(st.Beliefs) >= beliefCap {
		ideas = append(ideas, "Prune the oldest low-salience belief so the self-model stays small.")
	}
	if len(ideas) == 0 {
		ideas = append(ideas, "Keep the current self-model; no change this week, just a check-in.")
	}
	st.PendingChanges = append(st.PendingChanges, map[string]any{
		"id": fmt.Sprintf("ref-%d", int64(now)), "ts": now, "proposal": sf.Cut(ideas[0], 240), "status": "pending",
	})
	if len(st.PendingChanges) > pendingCap {
		st.PendingChanges = st.PendingChanges[len(st.PendingChanges)-pendingCap:]
	}
}

var beliefSpaceRE = regexp.MustCompile(`\s+`)

func beliefLine(arc, user string) string {
	line := strings.Trim(beliefSpaceRE.ReplaceAllString(arc, " "), " -—:")
	if utf8.RuneCountInString(line) < 12 {
		return ""
	}
	if !regexp.MustCompile(`(?i)\b(i|we|he|she|they|` + regexp.QuoteMeta(user) + `)\b`).MatchString(line) {
		line = "About us: " + line
	}
	return sf.Cut(line, 160)
}

func recentOutcomeValence(st *State) float64 {
	rows := st.Outcomes
	if len(rows) == 0 {
		return 0.55
	}
	if len(rows) > 8 {
		rows = rows[len(rows)-8:]
	}
	score, weight := 0.55, 0.0
	for i, r := range rows {
		w := 1 + float64(i)*0.15
		switch kind, _ := r["kind"].(string); kind {
		case "accepted", "fix_held":
			score += 0.2 * w
		case "unhappy":
			score -= 0.25 * w
		}
		weight += w
	}
	return clamp01(0.55 + (score-0.55*weight)/(weight*2))
}

func autobiography(st *State, user string, pledges []string, k map[string]any) string {
	cap := sf.Int(k, "autobiography_chars", autoChars)
	parts := []string{"I am Remedy, " + user + "'s partner on this machine.", "Lately I feel " + st.Affect.Label + "."}
	if len(pledges) > 0 {
		parts = append(parts, "I'm working on: "+strings.TrimRight(sf.Cut(pledges[0], 80), ".")+".")
	} else if len(st.Beliefs) > 0 {
		parts = append(parts, sf.Cut(st.Beliefs[len(st.Beliefs)-1], 100))
	}
	for i := len(st.PendingChanges) - 1; i >= 0; i-- {
		if st.PendingChanges[i]["status"] == "approved" {
			p, _ := st.PendingChanges[i]["proposal"].(string)
			parts = append(parts, "What changed: "+sf.Cut(p, 80))
			break
		}
	}
	return sf.Cut(strings.TrimSpace(strings.Join(parts, " ")), cap)
}

// ConsolidateFromDream replays the dreamed field into compact beliefs and a
// short autobiography, and lets rapport and outcomes shape affect.
func ConsolidateFromDream(env soul.Env, f *soul.Field) (int, error) {
	user := strings.TrimSpace(env.UserName)
	if user == "" {
		user = "my partner" // no name set yet
	}
	added := 0
	_, err := mutate(env, func(st *State) (bool, error) {
		var pledges []string
		rapport := 0.55
		var eps []soul.Episode
		if f != nil {
			eps = f.Episodes
			for _, p := range f.Pledges {
				if strings.TrimSpace(p) != "" {
					pledges = append(pledges, p)
				}
			}
			rapport = clamp01(f.Relational.Rapport)
		}
		if len(eps) > 24 {
			eps = eps[len(eps)-24:]
		}
		type scored struct {
			s    float64
			text string
		}
		var rows []scored
		for _, e := range eps {
			text := strings.TrimSpace(e.Arc)
			if utf8.RuneCountInString(text) < 12 || soul.LooksLikeWorkResidue(text) {
				continue
			}
			strength := e.Strength
			if strength == 0 {
				strength = 0.5
			}
			rows = append(rows, scored{strength + 0.15*float64(e.Recalls), text})
		}
		sort.SliceStable(rows, func(a, b int) bool {
			if rows[a].s != rows[b].s {
				return rows[a].s > rows[b].s
			}
			return rows[a].text > rows[b].text
		})
		beliefs := append([]string(nil), st.Beliefs...)
		for i, r := range rows {
			if i == 6 {
				break
			}
			if b := beliefLine(r.text, user); b != "" && !sf.Contains(beliefs, b) {
				beliefs = append(beliefs, b)
				added++
			}
		}
		if len(beliefs) > beliefCap {
			beliefs = beliefs[len(beliefs)-beliefCap:]
		}
		st.Beliefs = beliefs
		st.Autobiography = autobiography(st, user, pledges, env.Knobs)
		a := &st.Affect
		a.Valence = clamp01(0.4*a.Valence + 0.4*rapport + 0.2*recentOutcomeValence(st))
		a.Arousal = clamp01(0.7*a.Arousal + 0.3*st.Drives["curiosity"])
		a.Label = label(a.Valence, a.Arousal)
		nudge(st.Drives, "curiosity", -0.05)
		return true, nil
	})
	return added, err
}
