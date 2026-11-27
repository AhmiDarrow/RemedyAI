package httpapi

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/learn"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/vigil"
)

// Hearth is Remedy's always-on presence. The runtime stays up in the tray;
// this organ keeps her oriented without calling a model and without watching
// the screen. She speaks first only from a night she already finished
// (the vigil journal). Money, send, and pay stay on their own checkpoints.
//
// The market's always-on agents rent a cloud computer, fire a calendar, or
// record the display. The hearth does none of those. The muscle sleeps until
// a person arrives or a granted, muscle-free night tick is due.

const (
	hearthSchema   = 1
	hearthIdleAway = 20 * time.Minute

	// Adaptive unattended-learn clock. Defaults stay at one tick a minute;
	// the loop backs off when nothing changed or the machine is busy, and
	// speeds up after recent turn-end / mid-turn learning (heavy use).
	hearthLearnDefault = 60 * time.Second
	hearthLearnMin     = 20 * time.Second
	hearthLearnMax     = 15 * time.Minute
	hearthLearnHeavy   = 5 * time.Minute

	hearthFileName = "hearth.json"

	hearthLocusAway      = "away"
	hearthLocusDesk      = "desk"
	hearthLocusPhone     = "phone"
	hearthLocusMessenger = "messenger"
)

type hearthState struct {
	Schema          int     `json:"schema"`
	Locus           string  `json:"locus"`
	Surface         string  `json:"surface"`
	LastTouch       float64 `json:"last_touch_ts"`
	AwaySince       float64 `json:"away_since_ts"`
	Greeting        string  `json:"greeting"`
	GreetingThrough float64 `json:"greeting_through_ts"`
	AckedThrough    float64 `json:"acked_through_ts"`
	WatchesScreen   bool    `json:"watches_screen"`
}

type hearthJournal struct {
	Ts     float64        `json:"ts"`
	Act    string         `json:"act"`
	Detail string         `json:"detail"`
	Ok     bool           `json:"ok"`
	Added  map[string]int `json:"added,omitempty"`
}

func (s *Server) hearthPath() string {
	home := ""
	if s != nil {
		home = ResolveHomeDir(s.homeDir)
	}
	if strings.TrimSpace(home) == "" {
		return ""
	}
	return filepath.Join(home, "soul", hearthFileName)
}

func (s *Server) hearthLoad() hearthState {
	st := hearthState{Schema: hearthSchema, Locus: hearthLocusAway}
	path := s.hearthPath()
	if path == "" {
		return st
	}
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) == 0 {
		return st
	}
	var got hearthState
	if json.Unmarshal(raw, &got) != nil {
		return st
	}
	got.clamp()
	return got
}

func (st *hearthState) clamp() {
	if st.Schema == 0 {
		st.Schema = hearthSchema
	}
	switch st.Locus {
	case hearthLocusDesk, hearthLocusPhone, hearthLocusMessenger, hearthLocusAway:
	default:
		st.Locus = hearthLocusAway
	}
	st.Surface = trimSurface(st.Surface)
	st.Greeting = trimRunes(strings.TrimSpace(st.Greeting), 220)
	// Presence is metabolic. A file that claims she watches the screen is wrong.
	st.WatchesScreen = false
}

func (s *Server) hearthSave(st hearthState) {
	st.clamp()
	path := s.hearthPath()
	if path == "" {
		return
	}
	_ = writeJSONAtomic(path, st)
}

func (s *Server) hearthJournal() []hearthJournal {
	home := ""
	if s != nil {
		home = ResolveHomeDir(s.homeDir)
	}
	if strings.TrimSpace(home) == "" {
		return nil
	}
	f, err := os.Open(filepath.Join(home, "soul", "vigil_journal.jsonl"))
	if err != nil {
		return nil
	}
	defer f.Close()
	out := make([]hearthJournal, 0, 16)
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var e hearthJournal
		if json.Unmarshal([]byte(line), &e) != nil || e.Ts <= 0 {
			continue
		}
		out = append(out, e)
	}
	return out
}

// hearthGreeting turns finished night acts into one sentence. Failed acts
// are not progress. The wording matches the vigil morning line so the
// model and the tray say the same thing.
func hearthGreeting(entries []hearthJournal, since float64) (string, float64) {
	var bits []string
	var through float64
	for _, e := range entries {
		if !e.Ok || e.Ts <= since {
			continue
		}
		if e.Ts > through {
			through = e.Ts
		}
		phrase := hearthPhrase(e)
		if phrase == "" {
			continue
		}
		if len(bits) == 0 || bits[len(bits)-1] != phrase {
			bits = append(bits, phrase)
		}
	}
	if len(bits) == 0 {
		return "", 0
	}
	if len(bits) > 3 {
		bits = bits[:3]
	}
	return trimRunes("While you were away I "+strings.Join(bits, "; ")+".", 220), through
}

// genericLifeDetail is the hunger placeholder, not news. A finished step
// replaces it with what she did. Wording matches vigil.phrase_for.
const genericLifeDetail = "quiet local step toward the active life goal"

func hearthPhrase(e hearthJournal) string {
	detail := strings.TrimSpace(e.Detail)
	switch e.Act {
	case "dream":
		// The vigil journals the top dream line; older entries carry only
		// the hunger ("consolidate N episodes"), which is not news.
		if detail == "" || strings.HasPrefix(detail, "consolidate ") || strings.HasPrefix(detail, "skipped") {
			return "dreamed on our recent episodes"
		}
		return "dreamed on “" + trimRunes(detail, 100) + "”"
	case "life_step":
		if detail != "" && detail != genericLifeDetail {
			return "moved “" + trimRunes(detail, 100) + "” forward"
		}
		return "took a quiet step toward your goal"
	case "needs_you":
		if detail == "" {
			return "waited on you before a step only you can take"
		}
		return "waited on you before “" + trimRunes(detail, 80) + "”"
	case "myelin_verify":
		return "re-checked one of my learned skills"
	case "tend":
		if detail == "" {
			return "noticed something waiting"
		}
		return "noticed “" + trimRunes(detail, 60) + "” has been waiting"
	default:
		return ""
	}
}

func (st *hearthState) live() bool {
	return st.Locus == hearthLocusDesk || st.Locus == hearthLocusPhone || st.Locus == hearthLocusMessenger
}

func (st *hearthState) considerReturn(entries []hearthJournal) {
	if !st.live() {
		return
	}
	since := st.AckedThrough
	if st.GreetingThrough > since {
		since = st.GreetingThrough
	}
	greeting, through := hearthGreeting(entries, since)
	if greeting == "" {
		return
	}
	st.Greeting = greeting
	st.GreetingThrough = through
}

func (s *Server) hearthTouch(surface string, now time.Time) (hearthState, error) {
	locus, clean, ok := locusForSurface(surface)
	if !ok {
		return hearthState{}, errHearthSurface
	}
	s.hearthMu.Lock()
	defer s.hearthMu.Unlock()
	st := s.hearthLoad()
	wasAway := !st.live()
	st.Locus = locus
	st.Surface = clean
	st.LastTouch = unixFloat(now)
	if wasAway {
		st.considerReturn(s.hearthJournal())
	}
	s.hearthSave(st)
	return st, nil
}

func (s *Server) hearthAck(now time.Time) hearthState {
	s.hearthMu.Lock()
	defer s.hearthMu.Unlock()
	st := s.hearthLoad()
	if st.Greeting != "" {
		if st.GreetingThrough > st.AckedThrough {
			st.AckedThrough = st.GreetingThrough
		}
		st.Greeting = ""
		st.GreetingThrough = st.AckedThrough
		st.LastTouch = unixFloat(now)
		s.hearthSave(st)
	}
	return st
}

func (s *Server) hearthTick(now time.Time) hearthState {
	s.hearthMu.Lock()
	defer s.hearthMu.Unlock()
	st := s.hearthLoad()
	if !st.live() || st.LastTouch <= 0 {
		return st
	}
	if unixFloat(now)-st.LastTouch < hearthIdleAway.Seconds() {
		return st
	}
	st.Locus = hearthLocusAway
	st.Surface = ""
	st.AwaySince = unixFloat(now)
	s.hearthSave(st)
	return st
}

func (st hearthState) line(goal, next string) string {
	if g := strings.TrimSpace(st.Greeting); g != "" {
		return g
	}
	if !st.live() {
		return "Here when you are."
	}
	if n := trimRunes(strings.TrimSpace(next), 80); n != "" {
		return "Ready. Next: " + n
	}
	if g := trimRunes(strings.TrimSpace(goal), 80); g != "" {
		return "Ready. Still on " + g + "."
	}
	return "Ready."
}

func (st hearthState) public(now time.Time, goal, next string) map[string]any {
	idle := 0.0
	if st.LastTouch > 0 {
		idle = unixFloat(now) - st.LastTouch
		if idle < 0 {
			idle = 0
		}
	}
	return map[string]any{
		"present":        true,
		"locus":          st.Locus,
		"surface":        st.Surface,
		"greeting":       st.Greeting,
		"line":           st.line(goal, next),
		"idle_s":         int(idle),
		"watches_screen": false,
		"muscle":         "asleep",
	}
}

func (s *Server) hearthPublic(now time.Time, goal, next string) map[string]any {
	st := s.hearthTick(now)
	return st.public(now, goal, next)
}

var errHearthSurface = errors.New("unknown surface")

func (s *Server) handlePartnerTouch(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Surface string `json:"surface"`
	}
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<16))
	if err := dec.Decode(&body); err != nil && !errors.Is(err, io.EOF) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	st, err := s.hearthTouch(body.Surface, time.Now())
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "Unknown surface"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "hearth": st.public(time.Now(), "", "")})
}

func (s *Server) handlePartnerHearthAck(w http.ResponseWriter, r *http.Request) {
	st := s.hearthAck(time.Now())
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "hearth": st.public(time.Now(), "", "")})
}

func (s *Server) startHearthLoop(ctx context.Context) {
	if s == nil {
		return
	}
	loopCtx, cancel := context.WithCancel(ctx)
	s.hearthMu.Lock()
	if s.hearthCancel != nil {
		s.hearthCancel()
	}
	s.hearthCancel = cancel
	s.hearthMu.Unlock()
	go func() {
		orient := time.NewTicker(time.Minute)
		night := time.NewTicker(15 * time.Minute)
		cfg := LoadConfig(s.homeDir)
		def, min, max, heavy := learnClockFrom(cfg)
		interval := def
		learn := time.NewTimer(interval)
		defer orient.Stop()
		defer night.Stop()
		defer learn.Stop()
		for {
			select {
			case <-loopCtx.Done():
				return
			case now := <-orient.C:
				s.hearthTick(now)
			case <-night.C:
				// Her own work waits while the owner has a turn running.
				if !s.partnerOwnerBusy() {
					s.hearthPulseNight(loopCtx)
				}
			case <-learn.C:
				changed := false
				if !s.partnerOwnerBusy() {
					changed = s.hearthPulseLearn(loopCtx)
				}
				cfg = LoadConfig(s.homeDir)
				def, min, max, heavy = learnClockFrom(cfg)
				interval = nextLearnIntervalBounds(interval, changed, s.hearthBusy(), s.recentlyHeavyWindow(time.Now(), heavy), def, min, max)
				learn.Reset(interval)
			}
		}
	}()
}

func (s *Server) stopHearthLoop() {
	if s == nil {
		return
	}
	s.hearthMu.Lock()
	cancel := s.hearthCancel
	s.hearthCancel = nil
	s.hearthMu.Unlock()
	if cancel != nil {
		cancel()
	}
}

// hearthPulseNight spends one muscle-free vigil tick (native/go/mind/vigil).
// A disabled vigil is a no-op. A wake or a failure goes in the learn log.
// This never calls a provider.
func (s *Server) hearthPulseNight(_ context.Context) {
	if s == nil {
		return
	}
	env := mindEnv(s.homeDir)
	if strings.TrimSpace(env.Home) == "" {
		return
	}
	s.innerAct.Store("vigil")
	defer s.innerAct.Store("")
	started := time.Now()
	res, err := vigil.Tick(env)
	if err != nil || res.Woke {
		appendLearnLog(s.homeDir, "pulse", time.Since(started), false, err, vigilChanged(res))
	}
}

// skillTickEvery spaces the learn clock's learned-skill lifecycle ticks, as
// self_inject._SKILL_TICK_TTL did. Turn ends tick on their own.
const skillTickEvery = 10 * time.Minute

// hearthPulseLearn runs one unattended organism tick (learned-skill
// promote/prune at most every 10 minutes, dream when due, compact, drive
// drift) in-process and reports whether anything changed. Code self-inject
// is not part of it. Never calls a provider.
func (s *Server) hearthPulseLearn(_ context.Context) bool {
	if s == nil {
		return false
	}
	env := mindEnv(s.homeDir)
	if strings.TrimSpace(env.Home) == "" {
		return false
	}
	s.innerAct.Store("learning")
	defer s.innerAct.Store("")
	started := time.Now()
	refined, skillErr := s.hearthSkillTick(started)
	rep, err := drives.OrganismTickWith(env, refined)
	appendLearnLog(s.homeDir, "improve", time.Since(started), false, errors.Join(skillErr, err), rep.ChangedMap())
	return rep.Changed
}

// hearthSkillTick is _organism_tick's tick_learned_skills: it returns how
// many learned skills changed status. Skipped within skillTickEvery of the
// last one; a failed tick is retried on the next pulse.
func (s *Server) hearthSkillTick(now time.Time) (int, error) {
	last := s.skillTickAt.Load()
	if last != 0 && now.Sub(time.Unix(0, last)) < skillTickEvery {
		return 0, nil
	}
	changes, err := learn.TickLearnedSkills(ResolveHomeDir(s.homeDir), now, 0)
	if err == nil {
		s.skillTickAt.Store(now.UnixNano())
	}
	for _, c := range changes {
		logSkillChange(c)
	}
	return len(changes), err
}

// nextLearnInterval chooses the delay until the next unattended learn tick.
func nextLearnInterval(current time.Duration, changed, busy, heavy bool) time.Duration {
	return nextLearnIntervalBounds(current, changed, busy, heavy, hearthLearnDefault, hearthLearnMin, hearthLearnMax)
}

func nextLearnIntervalBounds(current time.Duration, changed, busy, heavy bool, def, min, max time.Duration) time.Duration {
	if def <= 0 {
		def = hearthLearnDefault
	}
	if min <= 0 {
		min = hearthLearnMin
	}
	if max <= 0 {
		max = hearthLearnMax
	}
	if current <= 0 {
		current = def
	}
	next := current
	switch {
	case heavy:
		// Recent turn-end / mid-turn learning: check sooner for a while.
		next = min
	case busy && !changed:
		next = minDuration(current*2, max)
	case !changed:
		next = minDuration(current*2, max)
	default:
		// Something changed, but no heavy use — ease toward the default.
		if current > def {
			next = maxDuration(current/2, def)
		} else {
			next = def
		}
	}
	if next < min {
		next = min
	}
	if next > max {
		next = max
	}
	return next
}

func minDuration(a, b time.Duration) time.Duration {
	if a < b {
		return a
	}
	return b
}

func maxDuration(a, b time.Duration) time.Duration {
	if a > b {
		return a
	}
	return b
}

// hearthBusy is true when this PC should not be given more background work:
// the owner is mid-turn, memory is short, or the load average is high. The
// load average exists only on Linux, so on Windows and macOS this rests on
// the same memory and owner checks the background governor uses.
func (s *Server) hearthBusy() bool {
	if s != nil && s.partner != nil {
		if c := s.partnerCapacity(); c.OwnerBusy || c.Paused {
			return true
		}
	}
	return systemBusy()
}

func systemBusy() bool {
	raw, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		return false
	}
	field := strings.Fields(string(raw))
	if len(field) == 0 {
		return false
	}
	load, err := strconv.ParseFloat(field[0], 64)
	if err != nil {
		return false
	}
	cpus := float64(runtime.NumCPU())
	if cpus < 1 {
		cpus = 1
	}
	return load > cpus
}

func (s *Server) recentlyHeavy(now time.Time) bool {
	return s.recentlyHeavyWindow(now, hearthLearnHeavy)
}

func (s *Server) recentlyHeavyWindow(now time.Time, window time.Duration) bool {
	if s == nil {
		return false
	}
	if window <= 0 {
		window = hearthLearnHeavy
	}
	rows := readLearnLog(s.homeDir, 16)
	cut := now.Add(-window)
	for _, row := range rows {
		kind, _ := row["kind"].(string)
		if kind != "turn_end" && kind != "after_tool_batch" && kind != "soul" {
			continue
		}
		ts, _ := row["ts"].(string)
		t, err := time.Parse(time.RFC3339Nano, ts)
		if err != nil {
			t, err = time.Parse(time.RFC3339, ts)
		}
		if err == nil && t.After(cut) {
			return true
		}
	}
	return false
}

func locusForSurface(surface string) (locus, clean string, ok bool) {
	s := strings.ToLower(strings.TrimSpace(surface))
	if s == "" {
		return "", "", false
	}
	clean = trimSurface(s)
	if clean == "" || strings.ContainsAny(clean, "\\/ \t") {
		return "", "", false
	}
	switch {
	case clean == "grove" || clean == "studio" || clean == "webui" || clean == "desktop" || clean == "cli":
		return hearthLocusDesk, clean, true
	case clean == "phone" || strings.HasPrefix(clean, "phone:"):
		return hearthLocusPhone, clean, true
	case messengerSurface(clean):
		return hearthLocusMessenger, clean, true
	default:
		return "", "", false
	}
}

func messengerSurface(clean string) bool {
	for _, name := range []string{
		"messenger", "telegram", "discord", "signal", "slack", "matrix",
		"whatsapp", "teams", "mattermost", "google_chat",
	} {
		if clean == name || strings.HasPrefix(clean, name+":") {
			return true
		}
	}
	return false
}

func trimSurface(s string) string {
	s = strings.TrimSpace(s)
	if utf8.RuneCountInString(s) <= 48 {
		return s
	}
	n := 0
	for i := range s {
		if n == 48 {
			return s[:i]
		}
		n++
	}
	return s
}

func unixFloat(t time.Time) float64 {
	if t.IsZero() {
		return 0
	}
	return float64(t.UnixNano()) / 1e9
}
