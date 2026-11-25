// Package dream is the dream cycle: idle consolidation of episode residue
// into durable soul tissue (src/remedy/memory/soul/dream.py and
// partner_dream.py). Local heuristics only; it never calls a model.
//
// The cooldown lives in REMEDY_HOME/soul/dream.json, not in memory, so a
// restart does not dream again at once, and the check-and-claim runs under
// that file's lock so two callers cannot both dream.
package dream

import (
	"encoding/json"
	"regexp"
	"strings"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

const (
	StateFile       = "dream.json"
	defaultCooldown = 120.0
)

// State is dream.json.
type State struct {
	Schema      int      `json:"schema"`
	LastDreamTS float64  `json:"last_dream_ts"`
	Extra       sf.Extra `json:"-"`
}

type plainState State

func (s *State) UnmarshalJSON(b []byte) (err error) {
	s.Extra, err = sf.Decode(b, (*plainState)(s))
	return err
}
func (s State) MarshalJSON() ([]byte, error) { return sf.Encode(plainState(s), s.Extra) }

func loadState(env soul.Env) (State, error) {
	st := State{Schema: 1}
	data, err := sf.Read(env.Path(StateFile))
	if err != nil || data == nil {
		return st, err
	}
	err = json.Unmarshal(data, &st)
	return st, err
}

func cooldown(env soul.Env) float64 { return sf.Float(env.Knobs, "dream_cooldown_s", defaultCooldown) }

// ShouldDream reports whether the cooldown has passed.
func ShouldDream(env soul.Env) (bool, error) {
	st, err := loadState(env)
	if err != nil {
		return false, err
	}
	return sf.Unix(env.Now())-st.LastDreamTS >= cooldown(env), nil
}

// claim stamps the cooldown if it has passed. false means skip.
func claim(env soul.Env, force bool) (bool, error) {
	path := env.Path(StateFile)
	unlock := sf.Lock(path)
	defer unlock()
	st, err := loadState(env)
	if err != nil {
		return false, err
	}
	now := sf.Unix(env.Now())
	if !force && now-st.LastDreamTS < cooldown(env) {
		return false, nil
	}
	st.LastDreamTS = now
	st.Schema = 1
	return true, sf.WriteJSON(path, st)
}

// Result is what one dream concluded.
type Result struct {
	Skipped         bool     `json:"skipped"`
	Reason          string   `json:"reason,omitempty"`
	EpisodesBefore  int      `json:"episodes_before"`
	EpisodesAfter   int      `json:"episodes_after"`
	Rehearsed       int      `json:"rehearsed"`
	DominantStance  string   `json:"dominant_stance,omitempty"`
	PromotedThreads int      `json:"promoted_threads"`
	PledgesAdded    int      `json:"pledges_added"`
	HabitsAdded     int      `json:"habits_added"`
	FutureDreams    int      `json:"future_dreams"`
	Dreams          []string `json:"dreams,omitempty"`
	Rapport         float64  `json:"rapport"`
	Trust           float64  `json:"trust"`
	// Field is the saved field after the dream (nil when skipped).
	Field *soul.Field `json:"-"`
}

// Top is the dream line the cycle put first, or "".
func (r Result) Top() string {
	if len(r.Dreams) == 0 {
		return ""
	}
	return r.Dreams[0]
}

var (
	spaceRE = regexp.MustCompile(`\s+`)
	habitRE = regexp.MustCompile(`(?i)\b(always|never|prefer|from now on)\b`)
)

// counter keeps insertion order for ties, like collections.Counter.most_common.
type counter struct {
	keys []string
	n    map[string]int
}

func (c *counter) add(k string) {
	if c.n == nil {
		c.n = map[string]int{}
	}
	if c.n[k] == 0 {
		c.keys = append(c.keys, k)
	}
	c.n[k]++
}

func (c *counter) mostCommon(limit int) []string {
	out := append([]string(nil), c.keys...)
	// stable insertion sort by count desc
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && c.n[out[j]] > c.n[out[j-1]]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	if limit > 0 && len(out) > limit {
		out = out[:limit]
	}
	return out
}

func lastN(eps []soul.Episode, n int) []soul.Episode {
	if len(eps) > n {
		return eps[len(eps)-n:]
	}
	return eps
}

func addHabit(f *soul.Field, h string) {
	if !sf.Contains(f.SelfHabits, h) {
		f.SelfHabits = append(f.SelfHabits, h)
	}
}

// Cycle runs one dream. A cooldown or too-few-episodes skip returns
// Skipped=true and changes nothing in the field.
func Cycle(env soul.Env, force bool) (Result, error) {
	ok, err := claim(env, force)
	if err != nil {
		return Result{}, err
	}
	if !ok {
		return Result{Skipped: true, Reason: "cooldown"}, nil
	}
	var res Result
	f, err := soul.Mutate(env, func(f *soul.Field) (bool, error) {
		res.EpisodesBefore = len(f.Episodes)
		hasGoals := len(f.Pledges) > 0 || len(f.FutureDreams) > 0 || len(f.Relational.OpenThreads) > 0
		if res.EpisodesBefore < 2 && !force && !hasGoals {
			res.Skipped, res.Reason = true, "too_few_episodes"
			return false, nil
		}
		consolidate(env, f, &res)
		return true, nil
	})
	if err != nil {
		return Result{}, err
	}
	if res.Skipped {
		return res, nil
	}
	res.Field = f
	res.EpisodesAfter = len(f.Episodes)
	if len(f.FutureDreams) > 5 {
		res.Dreams = append([]string(nil), f.FutureDreams[:5]...)
	} else {
		res.Dreams = append([]string(nil), f.FutureDreams...)
	}
	res.Rapport, res.Trust = f.Relational.Rapport, f.Relational.Trust
	return res, nil
}

func consolidate(env soul.Env, f *soul.Field, res *Result) {
	recent := lastN(f.Episodes, 12)
	var stances counter
	for _, e := range recent {
		if e.UserStance != "" {
			stances.add(e.UserStance)
		}
	}
	res.DominantStance = "steady"
	if top := stances.mostCommon(1); len(top) > 0 {
		res.DominantStance = top[0]
	}

	var threads counter
	for _, e := range recent {
		t := spaceRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(e.OpenThread)), " ")
		if len([]rune(t)) >= 8 && !soul.LooksLikeSecretSoul(t) {
			threads.add(sf.Cut(t, 160))
		}
	}
	now := sf.Unix(env.Now())
	for _, thr := range threads.mostCommon(5) {
		n := threads.n[thr]
		if n < 2 {
			continue
		}
		pretty := sf.UpperFirst(thr)
		if soul.LooksLikeWorkResidue(pretty) {
			continue
		}
		if !sf.Contains(f.Relational.OpenThreads, pretty) {
			f.Relational.OpenThreads = append(f.Relational.OpenThreads, sf.Cut(pretty, 160))
			res.PromotedThreads++
		}
		if n >= 3 {
			stay := "Stay with: " + sf.Cut(pretty, 140)
			if soul.LooksLikeWorkResidue(stay) {
				continue
			}
			if !sf.Contains(f.Pledges, stay) {
				f.Pledges = append(f.Pledges, stay)
				res.PledgesAdded++
			}
			f.TouchPledgeTrace(stay, now)
		}
	}

	for _, e := range recent {
		if !habitRE.MatchString(e.Arc) {
			continue
		}
		line := sf.Cut(spaceRE.ReplaceAllString(e.Arc, " "), 120)
		if soul.LooksLikeWorkResidue(line) {
			continue
		}
		if line != "" && !sf.Contains(f.SelfHabits, line) && !soul.LooksLikeSecretSoul(line) {
			f.SelfHabits = append(f.SelfHabits, line)
			res.HabitsAdded++
		}
	}

	switch res.DominantStance {
	case "frustrated":
		f.Relational.Rapport = max(0.2, f.Relational.Rapport-0.02)
		addHabit(f, "When they are frustrated: fix first, explain second.")
	case "playful":
		f.Relational.Rapport = min(0.95, f.Relational.Rapport+0.03)
	case "focused":
		addHabit(f, "Stay in builder mode: tools over narration until green.")
	}

	res.Rehearsed = soul.RehearseEpisodes(f.Episodes, now, env.Knobs)
	res.Rehearsed += soul.RehearseLessons(f.OrganismLessons, now, env.Knobs)
	if len(f.Episodes) > 10 {
		f.Episodes = soul.RetainEpisodes(f.Episodes, now, 8, env.Knobs)
	}

	res.FutureDreams = ApplyPartnerDreams(f, ComposePartnerDreams(f, 5))
	for i, d := range f.FutureDreams {
		if i == 3 {
			break
		}
		if soul.DreamIsResidue(d) {
			continue
		}
		if !sf.Contains(f.SelfHabits, d) && strings.HasPrefix(d, "Toward ") {
			parts := strings.SplitN(d, ":", 2)
			move := strings.TrimSpace(parts[len(parts)-1])
			if move != "" && !sf.Contains(f.SelfHabits, move) {
				f.SelfHabits = append(f.SelfHabits, sf.Cut(move, 120))
				break
			}
		}
	}
}
