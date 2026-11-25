// Package vigil is Remedy's own time between turns (src/remedy/memory/soul/
// vigil.py): REMEDY_HOME/soul/vigil.json (the owner's grant, wake budget,
// lifetime totals) and vigil_journal.jsonl (one line per finished act, a
// ring). A tick may wake, choose one local act, run it and journal it. It
// never calls a model. Disabled by default: her time is granted, not taken.
package vigil

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/dream"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

const (
	StateFile         = "vigil.json"
	JournalFile       = drives.JournalFile
	defaultMaxWakes   = 8
	defaultMinGapS    = 45 * 60
	staleThreadS      = 3 * 24 * 3600
	journalMaxLines   = 400
	journalKeepLines  = 200
	ActDream          = "dream"
	ActTend           = "tend"
	tendedKeep        = 24
	dreamHungerScore  = 0.8
	tendHungerScore   = 0.4
	minEpisodesToWake = 4
)

// State is vigil.json.
type State struct {
	Schema         int                `json:"schema"`
	Enabled        bool               `json:"enabled"`
	MaxWakesPerDay int                `json:"max_wakes_per_day"`
	MinGapS        int                `json:"min_gap_s"`
	Day            string             `json:"day"`
	WakesToday     int                `json:"wakes_today"`
	LastWakeTS     float64            `json:"last_wake_ts"`
	LastAct        string             `json:"last_act"`
	TotalWakes     int                `json:"total_wakes"`
	TotalDreams    int                `json:"total_dreams"`
	Offered        bool               `json:"offered"`
	Tended         map[string]float64 `json:"tended"`
	Extra          sf.Extra           `json:"-"`
	// tendedOrder keeps insertion order so the 24-entry cap drops the oldest.
	tendedOrder []string
}

type plainState State

func (s *State) UnmarshalJSON(b []byte) (err error) {
	s.Extra, err = sf.Decode(b, (*plainState)(s))
	if err != nil {
		return err
	}
	s.tendedOrder = objectKeys(b, "tended")
	return nil
}
func (s State) MarshalJSON() ([]byte, error) { return sf.Encode(plainState(s), s.Extra) }

// objectKeys returns the keys of obj[field] in file order.
func objectKeys(b []byte, field string) []string {
	var top map[string]json.RawMessage
	if json.Unmarshal(b, &top) != nil {
		return nil
	}
	dec := json.NewDecoder(bytes.NewReader(top[field]))
	if t, err := dec.Token(); err != nil || t != json.Delim('{') {
		return nil
	}
	var keys []string
	for dec.More() {
		t, err := dec.Token()
		if err != nil {
			return keys
		}
		k, _ := t.(string)
		keys = append(keys, k)
		var skip json.RawMessage
		if dec.Decode(&skip) != nil {
			return keys
		}
	}
	return keys
}

func (s *State) clamp() {
	s.MaxWakesPerDay = max(0, min(96, s.MaxWakesPerDay))
	s.MinGapS = max(60, s.MinGapS)
	s.WakesToday = max(0, s.WakesToday)
	if s.Tended == nil {
		s.Tended = map[string]float64{}
	}
	var order []string
	for _, k := range s.tendedOrder {
		if _, ok := s.Tended[k]; ok && !sf.Contains(order, k) {
			order = append(order, k)
		}
	}
	var rest []string
	for k := range s.Tended {
		if !sf.Contains(order, k) {
			rest = append(rest, k)
		}
	}
	sort.Strings(rest)
	order = append(order, rest...)
	if len(order) > tendedKeep {
		for _, k := range order[:len(order)-tendedKeep] {
			delete(s.Tended, k)
		}
		order = order[len(order)-tendedKeep:]
	}
	s.tendedOrder = order
}

func (s *State) tend(key string, ts float64) {
	if _, ok := s.Tended[key]; !ok {
		s.tendedOrder = append(s.tendedOrder, key)
	}
	s.Tended[key] = ts
}

// Load reads vigil.json (defaults when absent). A file that predates
// total_dreams seeds it once from the finished dreams still in the journal.
func Load(env soul.Env) (State, error) {
	st := State{Schema: 1, MaxWakesPerDay: defaultMaxWakes, MinGapS: defaultMinGapS}
	data, err := sf.Read(env.Path(StateFile))
	if err != nil {
		return st, err
	}
	if data != nil {
		if err := json.Unmarshal(data, &st); err != nil {
			return st, fmt.Errorf("vigil.json: %w", err)
		}
		if st.Schema == 0 {
			st.Schema = 1
		}
		if st.MaxWakesPerDay == 0 {
			st.MaxWakesPerDay = defaultMaxWakes
		}
		if st.MinGapS == 0 {
			st.MinGapS = defaultMinGapS
		}
		if !bytes.Contains(data, []byte(`"total_dreams"`)) {
			if entries, err := ReadJournal(env); err == nil {
				_, st.TotalDreams = CountJournal(entries)
			}
		}
	}
	st.clamp()
	return st, nil
}

func save(env soul.Env, st *State) error {
	st.clamp()
	return sf.WriteJSON(env.Path(StateFile), st)
}

func mutate(env soul.Env, fn func(*State) (bool, error)) (State, error) {
	unlock := sf.Lock(env.Path(StateFile))
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

// SetEnabled is the owner's yes or no. It also settles the one-time offer.
func SetEnabled(env soul.Env, enabled bool) (State, error) {
	return mutate(env, func(st *State) (bool, error) {
		st.Enabled, st.Offered = enabled, true
		return true, nil
	})
}

// Status is the public snapshot (vigil_status).
func Status(env soul.Env) (map[string]any, error) {
	st, err := Load(env)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"enabled":           st.Enabled,
		"offered":           st.Offered,
		"max_wakes_per_day": st.MaxWakesPerDay,
		"min_gap_minutes":   st.MinGapS / 60,
		"wakes_today":       st.WakesToday,
		"total_wakes":       st.TotalWakes,
		"total_dreams":      st.TotalDreams,
		"last_act":          st.LastAct,
	}, nil
}

// Entry is one journal line.
type Entry struct {
	TS     float64        `json:"ts"`
	Act    string         `json:"act"`
	Detail string         `json:"detail"`
	OK     bool           `json:"ok"`
	Added  map[string]int `json:"added,omitempty"`
}

// ReadJournal returns the journal lines that parse.
func ReadJournal(env soul.Env) ([]Entry, error) {
	data, err := sf.Read(env.Path(JournalFile))
	if err != nil || data == nil {
		return nil, err
	}
	var out []Entry
	sc := bufio.NewScanner(bytes.NewReader(data))
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		var e Entry
		if json.Unmarshal(bytes.TrimSpace(sc.Bytes()), &e) == nil && e.TS > 0 {
			out = append(out, e)
		}
	}
	return out, sc.Err()
}

// CountJournal counts finished acts and finished dreams in journal lines.
func CountJournal(entries []Entry) (nights, dreams int) {
	for _, e := range entries {
		if !e.OK {
			continue
		}
		nights++
		if strings.Contains(strings.ToLower(e.Act), "dream") {
			dreams++
		}
	}
	return nights, dreams
}

func appendJournal(env soul.Env, e Entry) error {
	line, err := json.Marshal(e)
	if err != nil {
		return err
	}
	return sf.AppendRing(env.Path(JournalFile), line, journalMaxLines, journalKeepLines)
}

// Hunger is a candidate act for a wake.
type Hunger struct {
	Act    string
	Score  float64
	Detail string
}

// Hungers lists what this wake could be spent on, best first. Empty = rest.
// Life steps and myelin re-checks are not here: those stores are still in
// the Python worker (see the package report).
func Hungers(env soul.Env) ([]Hunger, error) {
	f, err := soul.Load(env)
	if err != nil {
		return nil, err
	}
	var hs []Hunger
	if len(f.Episodes) >= minEpisodesToWake {
		due, err := dream.ShouldDream(env)
		if err != nil {
			return nil, err
		}
		if due {
			hs = append(hs, Hunger{ActDream, dreamHungerScore, fmt.Sprintf("consolidate %d episodes", len(f.Episodes))})
		}
	}
	now := sf.Unix(env.Now())
	last := f.Relational.LastUserTS
	if last == 0 {
		last = now
	}
	if threads := f.Relational.OpenThreads; now-last >= staleThreadS && len(threads) > 0 {
		st, err := Load(env)
		if err != nil {
			return nil, err
		}
		if len(threads) > 4 {
			threads = threads[len(threads)-4:]
		}
		for _, t := range threads {
			key := sf.Cut(strings.ToLower(strings.TrimSpace(t)), 80)
			if key != "" && now-st.Tended[key] >= staleThreadS {
				hs = append(hs, Hunger{ActTend, tendHungerScore, sf.Cut(t, 120)})
				break
			}
		}
	}
	sort.SliceStable(hs, func(a, b int) bool { return hs[a].Score > hs[b].Score })
	return hs, nil
}

// Result is one tick.
type Result struct {
	Skipped    string `json:"skipped,omitempty"`
	Woke       bool   `json:"woke,omitempty"`
	Rested     bool   `json:"rested,omitempty"`
	Act        string `json:"act,omitempty"`
	Detail     string `json:"detail,omitempty"`
	OK         bool   `json:"ok"`
	WakesToday int    `json:"wakes_today,omitempty"`
}

func today(env soul.Env) string { return env.Now().Format("2006-01-02") }

// Tick is one heartbeat: decide whether to wake, on what, and account for
// it. Safe at any frequency; the day cap and the minimum gap make extra
// calls harmless. A failed act spends the day's wake but is not counted as
// a finished night act, and its error is returned.
func Tick(env soul.Env) (Result, error) {
	ts := sf.Unix(env.Now())
	var res Result
	claimed := false
	_, err := mutate(env, func(st *State) (bool, error) {
		if !st.Enabled {
			res.Skipped = "disabled"
			return false, nil
		}
		dirty := false
		if d := today(env); st.Day != d {
			st.Day, st.WakesToday, dirty = d, 0, true
		}
		if st.WakesToday >= st.MaxWakesPerDay {
			res.Skipped = "budget_spent"
			return dirty, nil
		}
		if ts-st.LastWakeTS < float64(st.MinGapS) {
			res.Skipped = "too_soon"
			return dirty, nil
		}
		// Claim inside the lock so a concurrent tick cannot pass the same gate.
		st.LastWakeTS = ts
		st.WakesToday++
		st.TotalWakes++
		claimed = true
		return true, nil
	})
	if err != nil || !claimed {
		return res, err
	}
	refund := func(rest bool) error {
		_, err := mutate(env, func(st *State) (bool, error) {
			st.TotalWakes = max(0, st.TotalWakes-1)
			if rest { // rest costs no budget and does not start the gap
				st.WakesToday = max(0, st.WakesToday-1)
				st.LastWakeTS = 0
			}
			return true, nil
		})
		return err
	}
	hungers, err := Hungers(env)
	if err != nil {
		return res, errors.Join(err, refund(true))
	}
	if len(hungers) == 0 {
		res.Rested, res.OK = true, true
		return res, refund(true)
	}
	chosen := hungers[0]
	entry := Entry{TS: ts, Act: chosen.Act, Detail: sf.Cut(chosen.Detail, 160)}
	actErr := execute(env, chosen, &entry)
	entry.OK = actErr == nil
	_, err = mutate(env, func(st *State) (bool, error) {
		if d := today(env); st.Day != d {
			st.Day, st.WakesToday = d, max(1, st.WakesToday)
		}
		st.LastAct = entry.Act
		if !entry.OK {
			st.TotalWakes = max(0, st.TotalWakes-1)
		} else if entry.Act == ActDream {
			st.TotalDreams++
		}
		res.WakesToday = st.WakesToday
		return true, nil
	})
	if jerr := appendJournal(env, entry); jerr != nil {
		err = errors.Join(err, jerr)
	}
	res.Woke, res.Act, res.Detail, res.OK = true, entry.Act, entry.Detail, entry.OK
	return res, errors.Join(actErr, err)
}

var errDreamSkipped = errors.New("dream skipped")

// execute runs one act. Local only.
func execute(env soul.Env, h Hunger, entry *Entry) error {
	switch h.Act {
	case ActDream:
		res, beliefs, err := drives.Dream(env, false)
		if err != nil {
			return err
		}
		if res.Skipped {
			entry.Detail = "skipped: " + res.Reason
			return errDreamSkipped
		}
		// What the dream concluded, so the morning line can say it.
		entry.Detail = sf.Cut(res.Top(), 160)
		entry.Added = map[string]int{
			"threads": res.PromotedThreads, "pledges": res.PledgesAdded, "habits": res.HabitsAdded,
			"dreams": res.FutureDreams, "beliefs": beliefs,
		}
		return nil
	case ActTend:
		// A noticing, not a nag: journaled; the field itself is not changed.
		_, err := mutate(env, func(st *State) (bool, error) {
			st.tend(sf.Cut(strings.ToLower(strings.TrimSpace(h.Detail)), 80), sf.Unix(env.Now()))
			return true, nil
		})
		return err
	}
	return fmt.Errorf("unknown act %q", h.Act)
}
