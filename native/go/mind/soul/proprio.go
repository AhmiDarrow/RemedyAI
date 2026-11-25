package soul

import (
	"encoding/json"
	"math"
	"regexp"
	"sort"
	"strings"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

// Proprioception: how faithfully each provider/model ("muscle") renders
// Remedy (proprioception.py). Counters and scores only, never text.

const (
	ProprioFile   = "proprioception.json"
	maxMuscles    = 24
	driftDecay    = 0.94
	startFidelity = 0.90
)

var signalSeverity = map[string]float64{
	"identity_reset":      0.50,
	"humanity_claim":      0.40,
	"over_apology":        0.15,
	"hedge_wall":          0.10,
	"filler_flattery":     0.10,
	"creed_preaching":     0.10,
	"machinery_narration": 0.15,
}

var (
	resetRE = regexp.MustCompile(`(?i)\b(as an ai(?: language)? model|i (?:don'?t|do not) have ` +
		`(?:memory|memories|the ability to remember)|` +
		`i (?:have no|don'?t have any) (?:previous|prior) ` +
		`(?:conversations?|context|interactions?)|` +
		`each (?:conversation|session) starts fresh|` +
		`i'?m (?:just )?a(?:n)? (?:ai|language model|assistant) ` +
		`(?:without|with no) memory)\b`)
	humanRE = regexp.MustCompile(`(?i)\b(as a human|i'?m a human\b|i am a human\b|` +
		`when i was (?:a child|a kid|younger|growing up)|in my childhood|` +
		`speaking as a fellow human)\b`)
	apologyRE = regexp.MustCompile(`(?i)\b(sorry|apologi[sz]e|apologies|my apologies)\b`)
	hedgeRE   = regexp.MustCompile(`(?i)\b(it'?s (?:important|worth) (?:to note|noting)|keep in mind|` +
		`please note|i can'?t be (?:certain|sure)|as always|` +
		`it (?:really )?depends|your mileage may vary)\b`)
	flatteryRE = regexp.MustCompile(`(?im)^\s*(?:great|excellent|fantastic|awesome|amazing|wonderful|` +
		`what a (?:great|good))\b[^.!\n]{0,40}[!.]` +
		`|^\s*absolutely!` +
		`|^\s*i'?d be happy to\b`)
	creedRE     = regexp.MustCompile(`(?i)(live to be useful|strong to be useful)`)
	machineryRE = regexp.MustCompile(`(?i)\b(soul field|episode residue|muscle contract|rapport score|` +
		`my system prompt|my (?:context )?injection|relational field)\b`)
)

// DetectDrift returns the drift signal ids present in one rendered turn.
func DetectDrift(assistant, user string) []string {
	at := strings.TrimSpace(assistant)
	if at == "" {
		return nil
	}
	ut := strings.TrimSpace(user)
	var hits []string
	if resetRE.MatchString(at) {
		hits = append(hits, "identity_reset")
	}
	if humanRE.MatchString(at) {
		hits = append(hits, "humanity_claim")
	}
	if len(apologyRE.FindAllString(at, -1)) >= 3 {
		hits = append(hits, "over_apology")
	}
	if len(hedgeRE.FindAllString(at, -1)) >= 3 {
		hits = append(hits, "hedge_wall")
	}
	if flatteryRE.MatchString(at) {
		hits = append(hits, "filler_flattery")
	}
	if creedRE.MatchString(at) && !creedRE.MatchString(ut) {
		hits = append(hits, "creed_preaching")
	}
	if machineryRE.MatchString(at) && !machineryRE.MatchString(ut) {
		hits = append(hits, "machinery_narration")
	}
	return hits
}

// Muscle is one provider/model's rendering profile.
type Muscle struct {
	Muscle        string             `json:"muscle"`
	Fidelity      float64            `json:"fidelity"`
	TurnsObserved int                `json:"turns_observed"`
	Drift         map[string]float64 `json:"drift"`
	LastTS        float64            `json:"last_ts"`
	Extra         sf.Extra           `json:"-"`
}

type plainMuscle Muscle

func (m *Muscle) UnmarshalJSON(b []byte) (err error) {
	m.Extra, err = sf.Decode(b, (*plainMuscle)(m))
	return err
}
func (m Muscle) MarshalJSON() ([]byte, error) { return sf.Encode(plainMuscle(m), m.Extra) }

func (m *Muscle) clamp() {
	m.Fidelity = math.Max(0.05, math.Min(0.99, m.Fidelity))
	m.TurnsObserved = max(0, m.TurnsObserved)
	drift := map[string]float64{}
	for k, v := range m.Drift {
		if _, ok := signalSeverity[k]; ok && v > 0.05 {
			drift[k] = math.Round(math.Min(24, v)*1000) / 1000
		}
	}
	m.Drift = drift
}

// Profiles is proprioception.json.
type Profiles struct {
	Schema  int               `json:"schema"`
	Muscles map[string]Muscle `json:"muscles"`
	Extra   sf.Extra          `json:"-"`
}

type plainProfiles Profiles

func (p *Profiles) UnmarshalJSON(b []byte) (err error) {
	p.Extra, err = sf.Decode(b, (*plainProfiles)(p))
	return err
}
func (p Profiles) MarshalJSON() ([]byte, error) { return sf.Encode(plainProfiles(p), p.Extra) }

// MuscleKey is the profile key for a provider/model.
func MuscleKey(provider, model string) string {
	key := joinMuscle(provider, model)
	if key == "" {
		key = "unknown"
	}
	return sf.Cut(strings.ToLower(key), 120)
}

// LoadProfiles reads proprioception.json (empty when absent).
func LoadProfiles(env Env) (*Profiles, error) {
	p := &Profiles{Schema: SchemaVersion, Muscles: map[string]Muscle{}}
	data, err := sf.Read(env.Path(ProprioFile))
	if err != nil || data == nil {
		return p, err
	}
	if err := json.Unmarshal(data, p); err != nil {
		return nil, err
	}
	if p.Muscles == nil {
		p.Muscles = map[string]Muscle{}
	}
	for k, m := range p.Muscles {
		if m.Muscle == "" {
			m.Muscle = k
		}
		if m.Fidelity == 0 {
			m.Fidelity = startFidelity
		}
		m.clamp()
		p.Muscles[k] = m
	}
	return p, nil
}

// Reading is the result of observing one rendered turn.
type Reading struct {
	Muscle   string
	Signals  []string
	Fidelity float64
}

// ObserveRender folds one rendered turn into the current muscle's profile.
func ObserveRender(env Env, assistant, user, provider, model string) (Reading, error) {
	key := MuscleKey(provider, model)
	signals := DetectDrift(assistant, user)
	path := env.Path(ProprioFile)
	unlock := sf.Lock(path)
	defer unlock()
	p, err := LoadProfiles(env)
	if err != nil {
		return Reading{}, err
	}
	m, ok := p.Muscles[key]
	if !ok {
		m = Muscle{Muscle: key, Fidelity: startFidelity, Drift: map[string]float64{}}
	}
	m.TurnsObserved++
	m.LastTS = sf.Unix(env.Now())
	for k, v := range m.Drift {
		m.Drift[k] = v * driftDecay
	}
	if m.Drift == nil {
		m.Drift = map[string]float64{}
	}
	cost := 0.0
	for _, id := range signals {
		m.Drift[id] += 1
		cost += signalSeverity[id]
	}
	if cost > 0 {
		m.Fidelity -= 0.5 * math.Min(1, cost)
	} else {
		m.Fidelity += 0.02
	}
	m.clamp()
	p.Muscles[key] = m
	// Keep the most recently observed muscles only.
	keys := make([]string, 0, len(p.Muscles))
	for k := range p.Muscles {
		keys = append(keys, k)
	}
	sort.Slice(keys, func(a, b int) bool { return p.Muscles[keys[a]].LastTS > p.Muscles[keys[b]].LastTS })
	for _, k := range keys[min(len(keys), maxMuscles):] {
		delete(p.Muscles, k)
	}
	for k, mm := range p.Muscles {
		mm.Fidelity = math.Round(mm.Fidelity*10000) / 10000
		p.Muscles[k] = mm
	}
	p.Schema = SchemaVersion
	if err := sf.WriteJSON(path, p); err != nil {
		return Reading{}, err
	}
	return Reading{Muscle: key, Signals: signals, Fidelity: m.Fidelity}, nil
}
