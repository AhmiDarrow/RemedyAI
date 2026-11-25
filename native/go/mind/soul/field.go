// Package soul is Remedy's soul field and proprioception: the durable,
// provider-agnostic state under REMEDY_HOME/soul/ (field.json,
// proprioception.json). Port of src/remedy/memory/soul/field.py, update.py
// and proprioception.py. File and JSON field names match the Python, and
// fields this code does not know survive a load and save.
//
// There is no in-memory cache: every read comes from disk, and every
// read-modify-write runs under the file's process-wide lock (Mutate), so a
// wipe or a second writer is never overwritten by a stale copy.
package soul

import (
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

const (
	Dir           = "soul"
	FieldFile     = "field.json"
	SchemaVersion = 1

	maxEpisodes          = 12
	episodeRecentProtect = 4
	episodeHalflifeDays  = 30.0
	maxTensions          = 16
	maxSelfLessons       = 24
	maxVoiceMarkers      = 12
	maxOpenThreads       = 10
	maxFutureDreams      = 8
	maxPledges           = 12
	pledgeRecentProtect  = 3
	lessonRecentProtect  = 4
	rehearseMax          = 5
	rehearseStrengthStep = 0.05

	DefaultIdentityVow = "I am one continuous partner on this machine — not a new instance per " +
		"model, tab, or provider. Muscle changes; I stay. " +
		"I live to be useful; I am strong to be useful."
)

// ErrPersistBlocked means field.json exists but is not valid JSON. It is
// never overwritten automatically; the owner's data stays as it is.
var ErrPersistBlocked = errors.New("soul field.json is unreadable; not overwriting it")

// Env is what every mind package needs from its host: where the home is,
// what time it is, the resolved organism knobs ([advanced] table), and the
// owner's name from settings.
type Env struct {
	Home     string
	Clock    func() time.Time
	Knobs    map[string]any
	UserName string
}

// Now is the injected clock, or the wall clock.
func (e Env) Now() time.Time {
	if e.Clock != nil {
		return e.Clock()
	}
	return time.Now()
}

// Path is a file under REMEDY_HOME/soul/.
func (e Env) Path(name string) string { return filepath.Join(e.Home, Dir, name) }

// Episode is one micro-episode: a felt residue of shared work.
type Episode struct {
	ID           string   `json:"id"`
	TS           float64  `json:"ts"`
	Arc          string   `json:"arc"`
	UserStance   string   `json:"user_stance"`
	OpenThread   string   `json:"open_thread"`
	Valence      float64  `json:"valence"`
	Muscle       string   `json:"muscle"`
	SessionID    string   `json:"session_id"`
	ProjectHint  string   `json:"project_hint"`
	Strength     float64  `json:"strength"`
	Recalls      int      `json:"recalls"`
	LastRecallTS float64  `json:"last_recall_ts"`
	Extra        sf.Extra `json:"-"`
}

type plainEpisode Episode

func (e *Episode) UnmarshalJSON(b []byte) (err error) {
	e.Extra, err = sf.Decode(b, (*plainEpisode)(e))
	return err
}
func (e Episode) MarshalJSON() ([]byte, error) { return sf.Encode(plainEpisode(e), e.Extra) }

// Line is the human form of an episode.
func (e Episode) Line() string {
	var bits []string
	if a := strings.TrimSpace(e.Arc); a != "" {
		bits = append(bits, a)
	}
	if t := strings.TrimSpace(e.OpenThread); t != "" {
		bits = append(bits, "open: "+t)
	}
	if s := strings.TrimSpace(e.UserStance); s != "" {
		bits = append(bits, "stance: "+s)
	}
	return sf.Cut(strings.Join(bits, " · "), 220)
}

// Lesson is what the product organism learned about improving itself.
type Lesson struct {
	TS           float64  `json:"ts"`
	Outcome      string   `json:"outcome"`
	Tree         string   `json:"tree"`
	Summary      string   `json:"summary"`
	Lesson       string   `json:"lesson"`
	RoundID      string   `json:"round_id"`
	Strength     float64  `json:"strength"`
	Recalls      int      `json:"recalls"`
	LastRecallTS float64  `json:"last_recall_ts"`
	Extra        sf.Extra `json:"-"`
}

type plainLesson Lesson

func (l *Lesson) UnmarshalJSON(b []byte) (err error) {
	l.Extra, err = sf.Decode(b, (*plainLesson)(l))
	return err
}
func (l Lesson) MarshalJSON() ([]byte, error) { return sf.Encode(plainLesson(l), l.Extra) }

// Relational is the dyadic state: the relationship, not a user dossier.
type Relational struct {
	Rapport         float64  `json:"rapport"`
	Trust           float64  `json:"trust"`
	CorrectionStyle string   `json:"correction_style"`
	VoiceMarkers    []string `json:"voice_markers"`
	SpeechRegister  string   `json:"speech_register"`
	HelpMode        string   `json:"help_mode"`
	OpenThreads     []string `json:"open_threads"`
	Tensions        []string `json:"tensions"`
	TurnsTogether   int      `json:"turns_together"`
	LastUserTS      float64  `json:"last_user_ts"`
	LastValence     float64  `json:"last_valence"`
	Extra           sf.Extra `json:"-"`
}

type plainRelational Relational

func (r *Relational) UnmarshalJSON(b []byte) (err error) {
	r.Extra, err = sf.Decode(b, (*plainRelational)(r))
	return err
}
func (r Relational) MarshalJSON() ([]byte, error) { return sf.Encode(plainRelational(r), r.Extra) }

// Field is the continuous personhood field (owner-global, provider-agnostic).
type Field struct {
	Schema          int            `json:"schema"`
	IdentityName    string         `json:"identity_name"`
	IdentityGender  string         `json:"identity_gender"`
	IdentityVow     string         `json:"identity_vow"`
	SelfHabits      []string       `json:"self_habits"`
	Relational      Relational     `json:"relational"`
	Episodes        []Episode      `json:"episodes"`
	OrganismLessons []Lesson       `json:"organism_lessons"`
	Pledges         []string       `json:"pledges"`
	PledgeTraces    map[string]any `json:"pledge_traces"`
	FutureDreams    []string       `json:"future_dreams"`
	UpdatedTS       float64        `json:"updated_ts"`
	Extra           sf.Extra       `json:"-"`
	// PersistBlocked is set when field.json could not be parsed.
	PersistBlocked bool `json:"-"`
}

type plainField Field

func (f *Field) UnmarshalJSON(b []byte) (err error) {
	f.Extra, err = sf.Decode(b, (*plainField)(f))
	return err
}
func (f Field) MarshalJSON() ([]byte, error) { return sf.Encode(plainField(f), f.Extra) }

// New is a fresh field, as SoulField() in Python.
func New(now time.Time) *Field {
	return &Field{
		Schema:         SchemaVersion,
		IdentityName:   "Remedy",
		IdentityGender: "female",
		IdentityVow:    DefaultIdentityVow,
		Relational:     Relational{Rapport: 0.55, Trust: 0.55},
		PledgeTraces:   map[string]any{},
		UpdatedTS:      sf.Unix(now),
	}
}

// EncodeStrength is the initial trace strength of a fresh episode: intense
// turns (either sign) encode strongly.
func EncodeStrength(valence float64) float64 {
	return math.Max(0.05, math.Min(1.0, 0.35+0.55*math.Abs(valence)))
}

// EncodeLessonStrength: failures teach hardest.
func EncodeLessonStrength(outcome string) float64 {
	switch strings.ToLower(strings.TrimSpace(outcome)) {
	case "red", "rolled_back":
		return 0.85
	case "green", "applied":
		return 0.6
	}
	return 0.4
}

func traceRetention(strength, anchor float64, recalls int, now float64, knobs map[string]any) float64 {
	base := math.Max(0, strength)
	if anchor == 0 {
		anchor = now
	}
	n := recalls
	if n < 0 {
		n = 0
	}
	age := math.Max(0, (now-anchor)/86400.0)
	half := sf.Float(knobs, "episode_halflife_days", episodeHalflifeDays)
	if half <= 0 {
		half = 30
	}
	return base*math.Pow(0.5, age/half) + 0.10*float64(min(n, 5))
}

func episodeRetention(e Episode, now float64, knobs map[string]any) float64 {
	anchor := e.LastRecallTS
	if anchor == 0 {
		anchor = e.TS
	}
	score := traceRetention(e.Strength, anchor, e.Recalls, now, knobs)
	score += 0.15 * math.Abs(e.Valence)
	if strings.TrimSpace(e.OpenThread) != "" {
		score += 0.12
	}
	return score
}

func lessonRetention(l Lesson, now float64, knobs map[string]any) float64 {
	anchor := l.LastRecallTS
	if anchor == 0 {
		anchor = l.TS
	}
	score := traceRetention(l.Strength, anchor, l.Recalls, now, knobs)
	if o := strings.ToLower(strings.TrimSpace(l.Outcome)); o == "red" || o == "rolled_back" {
		score += 0.1
	}
	return score
}

// retain keeps the last protect items by position and the highest-scoring
// rest, in insertion order (shared shape of retain_episodes / retain_lessons).
func retain(n, cap, protect int, score func(int) float64) []int {
	all := make([]int, n)
	for i := range all {
		all[i] = i
	}
	if n <= cap {
		return all
	}
	protect = max(0, min(protect, cap))
	keep := map[int]bool{}
	for i := n - protect; i < n; i++ {
		keep[i] = true
	}
	older := all[:n-protect]
	if slots := cap - protect; slots > 0 && len(older) > 0 {
		ranked := append([]int(nil), older...)
		sort.SliceStable(ranked, func(a, b int) bool { return score(ranked[a]) > score(ranked[b]) })
		if len(ranked) > slots {
			ranked = ranked[:slots]
		}
		for _, i := range ranked {
			keep[i] = true
		}
	}
	out := make([]int, 0, len(keep))
	for _, i := range all {
		if keep[i] {
			out = append(out, i)
		}
	}
	return out
}

// RetainEpisodes is salience-weighted eviction (the successor to FIFO).
// cap <= 0 uses the max_episodes knob.
func RetainEpisodes(eps []Episode, now float64, cap int, knobs map[string]any) []Episode {
	if cap <= 0 {
		cap = sf.Int(knobs, "max_episodes", maxEpisodes)
	}
	idx := retain(len(eps), cap, sf.Int(knobs, "episode_recent_protect", episodeRecentProtect),
		func(i int) float64 { return episodeRetention(eps[i], now, knobs) })
	out := make([]Episode, 0, len(idx))
	for _, i := range idx {
		out = append(out, eps[i])
	}
	return out
}

func retainLessons(ls []Lesson, now float64, knobs map[string]any) []Lesson {
	idx := retain(len(ls), sf.Int(knobs, "max_self_lessons", maxSelfLessons), lessonRecentProtect,
		func(i int) float64 { return lessonRetention(ls[i], now, knobs) })
	out := make([]Lesson, 0, len(idx))
	for _, i := range idx {
		out = append(out, ls[i])
	}
	return out
}

func worthRehearsing(e Episode) bool {
	return strings.TrimSpace(e.OpenThread) != "" || e.Recalls >= 1 ||
		math.Abs(e.Valence) >= 0.5 || e.Strength >= 0.7
}

// RehearseEpisodes refreshes the top few valuable traces (spaced rehearsal).
func RehearseEpisodes(eps []Episode, now float64, knobs map[string]any) int {
	var cand []int
	for i := range eps {
		if worthRehearsing(eps[i]) {
			cand = append(cand, i)
		}
	}
	sort.SliceStable(cand, func(a, b int) bool {
		return episodeRetention(eps[cand[a]], now, knobs) > episodeRetention(eps[cand[b]], now, knobs)
	})
	n := 0
	for _, i := range cand {
		if n == rehearseMax {
			break
		}
		eps[i].Strength = math.Min(1, eps[i].Strength+rehearseStrengthStep)
		eps[i].LastRecallTS = now
		n++
	}
	return n
}

// RehearseLessons keeps the hardest-won lessons fresh.
func RehearseLessons(ls []Lesson, now float64, knobs map[string]any) int {
	var cand []int
	for i := range ls {
		o := strings.ToLower(strings.TrimSpace(ls[i].Outcome))
		if o == "red" || o == "rolled_back" || ls[i].Recalls >= 1 {
			cand = append(cand, i)
		}
	}
	sort.SliceStable(cand, func(a, b int) bool {
		return lessonRetention(ls[cand[a]], now, knobs) > lessonRetention(ls[cand[b]], now, knobs)
	})
	n := 0
	for _, i := range cand {
		if n == rehearseMax {
			break
		}
		ls[i].Strength = math.Min(1, ls[i].Strength+rehearseStrengthStep)
		ls[i].LastRecallTS = now
		n++
	}
	return n
}

// FindPledgeKey is the stored form of a pledge, matched case-insensitively.
func (f *Field) FindPledgeKey(pledge string) string {
	key := sf.Cut(strings.TrimSpace(pledge), 160)
	kf := sf.Fold(key)
	for _, p := range f.Pledges {
		if sf.Fold(p) == kf {
			return p
		}
	}
	for k := range f.PledgeTraces {
		if sf.Fold(k) == kf {
			return k
		}
	}
	return key
}

// TouchPledgeTrace encodes or reconsolidates the trace behind a pledge.
func (f *Field) TouchPledgeTrace(pledge string, now float64) {
	key := f.FindPledgeKey(pledge)
	if key == "" {
		return
	}
	if f.PledgeTraces == nil {
		f.PledgeTraces = map[string]any{}
	}
	if tr, ok := f.PledgeTraces[key].(map[string]any); ok {
		tr["strength"] = math.Min(1, num(tr["strength"])+0.12)
		tr["recalls"] = int(num(tr["recalls"])) + 1
		tr["last_recall_ts"] = now
		return
	}
	f.PledgeTraces[key] = map[string]any{"strength": 0.5, "recalls": 0, "last_recall_ts": now, "ts": now}
}

func num(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	}
	return 0
}

func (f *Field) pledgeRetention(p string, now float64, knobs map[string]any) float64 {
	tr, ok := f.PledgeTraces[sf.Cut(strings.TrimSpace(p), 160)].(map[string]any)
	if !ok {
		return 0.35
	}
	anchor := num(tr["last_recall_ts"])
	if anchor == 0 {
		anchor = num(tr["ts"])
	}
	return traceRetention(num(tr["strength"]), anchor, int(num(tr["recalls"])), now, knobs)
}

func (f *Field) retainPledges(knobs map[string]any) []string {
	now := f.UpdatedTS
	seen := map[string]bool{}
	var ps []string
	for _, p := range f.Pledges {
		p = sf.Cut(p, 160)
		if p != "" && !seen[p] {
			seen[p] = true
			ps = append(ps, p)
		}
	}
	if len(ps) > maxPledges {
		idx := retain(len(ps), maxPledges, pledgeRecentProtect, func(i int) float64 { return f.pledgeRetention(ps[i], now, knobs) })
		kept := make([]string, 0, len(idx))
		for _, i := range idx {
			kept = append(kept, ps[i])
		}
		ps = kept
	}
	held := map[string]bool{}
	for _, p := range ps {
		held[p] = true
	}
	for k := range f.PledgeTraces {
		if !held[k] {
			delete(f.PledgeTraces, k)
		}
	}
	return ps
}

func clipList(in []string, n int, last bool, cap int) []string {
	out := []string{}
	for _, v := range in {
		if v != "" {
			out = append(out, sf.Cut(v, n))
		}
	}
	if len(out) > cap {
		if last {
			out = out[len(out)-cap:]
		} else {
			out = out[:cap]
		}
	}
	return out
}

func (r *Relational) clamp(knobs map[string]any) {
	r.Rapport = math.Max(0.05, math.Min(0.98, r.Rapport))
	r.Trust = math.Max(0.05, math.Min(0.98, r.Trust))
	r.TurnsTogether = max(0, r.TurnsTogether)
	r.VoiceMarkers = clipList(r.VoiceMarkers, 80, true, sf.Int(knobs, "max_voice_markers", maxVoiceMarkers))
	r.SpeechRegister = sf.Cut(r.SpeechRegister, 32)
	r.OpenThreads = clipList(r.OpenThreads, 160, true, sf.Int(knobs, "max_open_threads", maxOpenThreads))
	r.Tensions = clipList(r.Tensions, 180, true, sf.Int(knobs, "max_tensions", maxTensions))
}

// ScrubWorkResidue drops job/project residue from the owner-global lists.
func (f *Field) ScrubWorkResidue() int {
	removed := 0
	filter := func(in []string, residue func(string) bool) []string {
		out := []string{}
		for _, v := range in {
			if v != "" && !residue(v) {
				out = append(out, v)
			}
		}
		removed += len(in) - len(out)
		return out
	}
	f.Relational.OpenThreads = filter(f.Relational.OpenThreads, LooksLikeWorkResidue)
	before := f.Pledges
	f.Pledges = filter(f.Pledges, LooksLikeWorkResidue)
	for _, p := range before {
		if !sf.Contains(f.Pledges, p) {
			delete(f.PledgeTraces, p)
		}
	}
	f.SelfHabits = filter(f.SelfHabits, LooksLikeWorkResidue)
	f.FutureDreams = filter(f.FutureDreams, DreamIsResidue)
	return removed
}

// DreamIsResidue: "Toward <goal>: <move>" — the goal half must be clean too.
func DreamIsResidue(d string) bool {
	return LooksLikeWorkResidue(d) || LooksLikeWorkResidue(strings.SplitN(d, ":", 2)[0])
}

// Touch applies every cap and the residue rule, as SoulField.touch().
func (f *Field) Touch(now time.Time, knobs map[string]any) {
	f.UpdatedTS = sf.Unix(now)
	f.Relational.clamp(knobs)
	f.ScrubWorkResidue()
	f.Episodes = RetainEpisodes(f.Episodes, f.UpdatedTS, 0, knobs)
	if f.Episodes == nil {
		f.Episodes = []Episode{}
	}
	f.OrganismLessons = retainLessons(f.OrganismLessons, f.UpdatedTS, knobs)
	if f.OrganismLessons == nil {
		f.OrganismLessons = []Lesson{}
	}
	f.SelfHabits = clipList(f.SelfHabits, 120, false, 16)
	if f.PledgeTraces == nil {
		f.PledgeTraces = map[string]any{}
	}
	f.Pledges = f.retainPledges(knobs)
	if f.Pledges == nil {
		f.Pledges = []string{}
	}
	f.FutureDreams = clipList(f.FutureDreams, 200, false, sf.Int(knobs, "max_future_dreams", maxFutureDreams))
}

// normalize applies the defaults SoulField.from_dict fills in.
func (f *Field) normalize(now time.Time, knobs map[string]any) {
	nowF := sf.Unix(now)
	if f.Schema == 0 {
		f.Schema = SchemaVersion
	}
	if f.IdentityName == "" {
		f.IdentityName = "Remedy"
	}
	g := strings.ToLower(strings.TrimSpace(f.IdentityGender))
	if g != "female" && g != "male" && g != "neutral" {
		g = "female"
	}
	f.IdentityGender = g
	if f.IdentityVow == "" {
		f.IdentityVow = DefaultIdentityVow
	}
	if f.Relational.Rapport == 0 {
		f.Relational.Rapport = 0.55
	}
	if f.Relational.Trust == 0 {
		f.Relational.Trust = 0.55
	}
	for i := range f.Episodes {
		if f.Episodes[i].TS == 0 {
			f.Episodes[i].TS = nowF
		}
		if f.Episodes[i].Strength == 0 {
			f.Episodes[i].Strength = EncodeStrength(f.Episodes[i].Valence)
		}
	}
	for i := range f.OrganismLessons {
		if f.OrganismLessons[i].TS == 0 {
			f.OrganismLessons[i].TS = nowF
		}
		if f.OrganismLessons[i].Strength == 0 {
			f.OrganismLessons[i].Strength = EncodeLessonStrength(f.OrganismLessons[i].Outcome)
		}
	}
	traces := map[string]any{}
	for k, v := range f.PledgeTraces {
		if m, ok := v.(map[string]any); ok {
			traces[sf.Cut(k, 160)] = m
		}
	}
	f.PledgeTraces = traces
	updated := f.UpdatedTS
	if updated == 0 {
		updated = nowF
	}
	f.Touch(now, knobs)
	f.UpdatedTS = updated
}

// FieldPath is REMEDY_HOME/soul/field.json.
func FieldPath(home string) string { return filepath.Join(home, Dir, FieldFile) }

func load(env Env) (*Field, error) {
	now := env.Now()
	data, err := sf.Read(FieldPath(env.Home))
	if err != nil {
		return nil, err
	}
	f := New(now)
	if data == nil {
		return f, nil
	}
	parsed := &Field{}
	if err := json.Unmarshal(data, parsed); err != nil {
		f.PersistBlocked = true
		return f, nil
	}
	parsed.normalize(now, env.Knobs)
	return parsed, nil
}

// Load reads the field (or a fresh one when there is no file). Read-only:
// to change it, use Mutate.
func Load(env Env) (*Field, error) { return load(env) }

func save(env Env, f *Field) error {
	if f.PersistBlocked {
		return ErrPersistBlocked
	}
	f.Touch(env.Now(), env.Knobs)
	return sf.WriteJSON(FieldPath(env.Home), f)
}

// Mutate loads the field, lets fn change it, and saves it, all under the
// field's lock. fn returns false to leave the file untouched.
func Mutate(env Env, fn func(*Field) (bool, error)) (*Field, error) {
	unlock := sf.Lock(FieldPath(env.Home))
	defer unlock()
	f, err := load(env)
	if err != nil {
		return nil, err
	}
	if f.PersistBlocked {
		return f, ErrPersistBlocked
	}
	changed, err := fn(f)
	if err != nil || !changed {
		return f, err
	}
	return f, save(env, f)
}

// Reset is the persona wipe: a fresh field that keeps only the partner's
// name, presentation and vow (reset_soul_persona in Python).
func Reset(env Env) error {
	unlock := sf.Lock(FieldPath(env.Home))
	defer unlock()
	prev, err := load(env)
	if err != nil {
		return err
	}
	fresh := New(env.Now())
	if !prev.PersistBlocked {
		fresh.IdentityName, fresh.IdentityGender, fresh.IdentityVow = prev.IdentityName, prev.IdentityGender, prev.IdentityVow
	}
	return save(env, fresh)
}

// Compact forces salience eviction and bounds field.json to maxBytes by
// dropping the oldest episodes (compact._compact_soul). It writes only when
// the bytes change, and keeps updated_ts, so a quiet tick is not a soul update.
func Compact(env Env, maxBytes int) (bool, error) {
	path := FieldPath(env.Home)
	unlock := sf.Lock(path)
	defer unlock()
	before, err := sf.Read(path)
	if err != nil || before == nil {
		return false, err
	}
	f, err := load(env)
	if err != nil || f.PersistBlocked {
		return false, err
	}
	keep := f.UpdatedTS
	encode := func() ([]byte, error) {
		f.Touch(env.Now(), env.Knobs)
		f.UpdatedTS = keep
		return sf.Marshal(f)
	}
	after, err := encode()
	if err != nil {
		return false, err
	}
	for maxBytes > 0 && len(after) > maxBytes && len(f.Episodes) > 0 {
		f.Episodes = f.Episodes[1:]
		if after, err = encode(); err != nil {
			return false, err
		}
	}
	if string(after) == string(before) {
		return false, nil
	}
	if err := sf.WriteBytes(path, after); err != nil {
		return false, err
	}
	return len(after) < len(before) || (len(before) > maxBytes && len(after) <= maxBytes), nil
}

// RemoveState deletes a mind state file under its lock (persona wipe).
func RemoveState(path string) (bool, error) {
	if _, err := os.Stat(path); err != nil {
		return false, nil
	}
	return sf.Remove(path)
}
