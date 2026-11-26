package httpapi

import (
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
)

// Routines are standing jobs the owner gave Remedy: "every weekday at 7,
// brief me", "every 2 hours, check the order status". The schedule is in the
// owner's local time. A routine runs in Remedy's own conversation, in a fresh
// child, or in a goal's conversation.

type partnerSchedule struct {
	Kind         string   `json:"kind"` // every | daily | weekdays | weekly | once
	EveryMinutes int      `json:"every_minutes,omitempty"`
	At           string   `json:"at,omitempty"`   // HH:MM local
	Days         []string `json:"days,omitempty"` // mon..sun for weekly
	OnceAt       string   `json:"once_at,omitempty"`
}

type partnerRoutine struct {
	ID     string `json:"id"`
	Title  string `json:"title"`
	Prompt string `json:"prompt"`
	// Mode "remind" posts Prompt as a note with no model call; "task" has
	// Remedy do it. Reminders cost nothing — prefer them when they suffice.
	Mode        string          `json:"mode"`
	Schedule    partnerSchedule `json:"schedule"`
	Target      string          `json:"target"`  // head | child | goal:<id>
	Deliver     string          `json:"deliver"` // grove | messenger | both
	Enabled     bool            `json:"enabled"`
	CreatedBy   string          `json:"created_by"`
	// Outside is set when someone other than the owner had Remedy create the
	// routine: every run of it asks before changing anything.
	Outside bool `json:"outside,omitempty"`
	CreatedAt   string          `json:"created_at"`
	NextRun     string          `json:"next_run,omitempty"`
	LastRun     string          `json:"last_run,omitempty"`
	LastState   string          `json:"last_state,omitempty"`
	LastSummary string          `json:"last_summary,omitempty"`
	Runs        int             `json:"runs"`
}

const partnerMinEveryMinutes = 15

var weekdayNames = map[string]time.Weekday{
	"sun": time.Sunday, "mon": time.Monday, "tue": time.Tuesday, "wed": time.Wednesday,
	"thu": time.Thursday, "fri": time.Friday, "sat": time.Saturday,
}

func parseClock(raw string) (h, m int, err error) {
	raw = strings.TrimSpace(raw)
	hs, ms, ok := strings.Cut(raw, ":")
	if !ok {
		return 0, 0, fmt.Errorf("time %q must look like 07:30", raw)
	}
	h, err1 := strconv.Atoi(strings.TrimSpace(hs))
	m, err2 := strconv.Atoi(strings.TrimSpace(ms))
	if err1 != nil || err2 != nil || h < 0 || h > 23 || m < 0 || m > 59 {
		return 0, 0, fmt.Errorf("time %q must look like 07:30", raw)
	}
	return h, m, nil
}

// normalizeSchedule validates a schedule and fills defaults.
func normalizeSchedule(sc partnerSchedule) (partnerSchedule, error) {
	sc.Kind = strings.ToLower(strings.TrimSpace(sc.Kind))
	switch sc.Kind {
	case "every", "interval", "hourly":
		sc.Kind = "every"
		if sc.EveryMinutes <= 0 {
			sc.EveryMinutes = 60
		}
		if sc.EveryMinutes < partnerMinEveryMinutes {
			return sc, fmt.Errorf("routines run at most every %d minutes", partnerMinEveryMinutes)
		}
		sc.At, sc.Days, sc.OnceAt = "", nil, ""
	case "daily", "weekdays":
		if sc.At == "" {
			sc.At = "08:00"
		}
		if _, _, err := parseClock(sc.At); err != nil {
			return sc, err
		}
		sc.EveryMinutes, sc.Days, sc.OnceAt = 0, nil, ""
	case "weekly":
		if sc.At == "" {
			sc.At = "08:00"
		}
		if _, _, err := parseClock(sc.At); err != nil {
			return sc, err
		}
		days := []string{}
		seen := map[string]bool{}
		for _, d := range sc.Days {
			k := strings.ToLower(strings.TrimSpace(d))
			if len(k) > 3 {
				k = k[:3]
			}
			if _, ok := weekdayNames[k]; !ok {
				return sc, fmt.Errorf("unknown day %q", d)
			}
			if !seen[k] {
				seen[k] = true
				days = append(days, k)
			}
		}
		if len(days) == 0 {
			return sc, errors.New("weekly routines need at least one day")
		}
		sc.Days = days
		sc.EveryMinutes, sc.OnceAt = 0, ""
	case "once":
		if _, err := time.Parse(time.RFC3339, strings.TrimSpace(sc.OnceAt)); err != nil {
			return sc, errors.New("once_at must be a full date and time (RFC 3339)")
		}
		sc.EveryMinutes, sc.At, sc.Days = 0, "", nil
	default:
		return sc, fmt.Errorf("schedule kind %q must be every, daily, weekdays, weekly or once", sc.Kind)
	}
	return sc, nil
}

// nextRunAfter is the first run strictly after `after`, in loc. A finished
// once-routine returns the zero time.
func nextRunAfter(sc partnerSchedule, after time.Time, loc *time.Location) time.Time {
	if loc == nil {
		loc = time.Local
	}
	local := after.In(loc)
	switch sc.Kind {
	case "every":
		mins := sc.EveryMinutes
		if mins < partnerMinEveryMinutes {
			mins = partnerMinEveryMinutes
		}
		return after.Add(time.Duration(mins) * time.Minute)
	case "once":
		t, err := time.Parse(time.RFC3339, sc.OnceAt)
		if err != nil || !t.After(after) {
			return time.Time{}
		}
		return t
	}
	h, m, err := parseClock(sc.At)
	if err != nil {
		return time.Time{}
	}
	for i := 0; i <= 8; i++ {
		day := local.AddDate(0, 0, i)
		cand := time.Date(day.Year(), day.Month(), day.Day(), h, m, 0, 0, loc)
		if !cand.After(after) {
			continue
		}
		wd := cand.Weekday()
		switch sc.Kind {
		case "daily":
			return cand
		case "weekdays":
			if wd != time.Saturday && wd != time.Sunday {
				return cand
			}
		case "weekly":
			for _, d := range sc.Days {
				if weekdayNames[d] == wd {
					return cand
				}
			}
		}
	}
	return time.Time{}
}

// describeSchedule is the owner-facing phrase ("Weekdays at 07:00").
func describeSchedule(sc partnerSchedule) string {
	switch sc.Kind {
	case "every":
		if sc.EveryMinutes%60 == 0 {
			hrs := sc.EveryMinutes / 60
			if hrs == 1 {
				return "Every hour"
			}
			return fmt.Sprintf("Every %d hours", hrs)
		}
		return fmt.Sprintf("Every %d minutes", sc.EveryMinutes)
	case "daily":
		return "Every day at " + sc.At
	case "weekdays":
		return "Weekdays at " + sc.At
	case "weekly":
		names := make([]string, 0, len(sc.Days))
		for _, d := range sc.Days {
			names = append(names, strings.ToUpper(d[:1])+d[1:])
		}
		return strings.Join(names, ", ") + " at " + sc.At
	case "once":
		if t, err := time.Parse(time.RFC3339, sc.OnceAt); err == nil {
			return "Once, " + t.In(time.Local).Format("Mon Jan 2 15:04")
		}
		return "Once"
	}
	return sc.Kind
}

// inQuietHours reports whether local clock time t falls in [start, end),
// wrapping midnight when start > end. Empty or equal bounds mean no quiet.
func inQuietHours(t time.Time, start, end string) bool {
	sh, sm, err1 := parseClock(start)
	eh, em, err2 := parseClock(end)
	if err1 != nil || err2 != nil {
		return false
	}
	s, e := sh*60+sm, eh*60+em
	if s == e {
		return false
	}
	cur := t.Hour()*60 + t.Minute()
	if s < e {
		return cur >= s && cur < e
	}
	return cur >= s || cur < e
}

func normalizeRoutineTarget(raw string) string {
	t := strings.TrimSpace(raw)
	low := strings.ToLower(t)
	switch {
	case low == "" || low == "head" || low == "remedy" || low == "home":
		return "head"
	case low == "child" || low == "new_child" || low == "new":
		return "child"
	case strings.HasPrefix(low, "goal:") && len(t) > 5:
		return "goal:" + strings.TrimSpace(t[5:])
	}
	return "head"
}

func normalizeRoutineMode(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "remind", "reminder", "note":
		return "remind"
	default:
		return "task"
	}
}

func normalizeDeliver(raw string) string {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "messenger", "phone", "chat":
		return "messenger"
	case "both":
		return "both"
	default:
		return "grove"
	}
}

// addRoutine validates and stores a routine.
func (h *partnerHub) addRoutine(r partnerRoutine) (*partnerRoutine, error) {
	r.Title = truncateRunes(strings.TrimSpace(r.Title), 120)
	r.Prompt = truncateRunes(strings.TrimSpace(r.Prompt), 4000)
	if r.Prompt == "" {
		return nil, errors.New("say what the routine should do")
	}
	if r.Title == "" {
		r.Title = truncateRunes(r.Prompt, 60)
	}
	sc, err := normalizeSchedule(r.Schedule)
	if err != nil {
		return nil, err
	}
	r.Schedule = sc
	r.Mode = normalizeRoutineMode(r.Mode)
	r.Target = normalizeRoutineTarget(r.Target)
	r.Deliver = normalizeDeliver(r.Deliver)
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.st.Routines) >= partnerRoutinesMax {
		return nil, fmt.Errorf("Remedy already keeps %d routines — retire one first", partnerRoutinesMax)
	}
	now := h.now()
	r.ID = "rt-" + shortHexID(8)
	r.CreatedAt = now.UTC().Format(time.RFC3339)
	r.Enabled = true
	if next := nextRunAfter(r.Schedule, now, time.Local); !next.IsZero() {
		r.NextRun = next.UTC().Format(time.RFC3339)
	} else {
		return nil, errors.New("that schedule never runs (is the time in the past?)")
	}
	cp := r
	h.st.Routines = append(h.st.Routines, &cp)
	h.recordLocked(partnerActivity{Kind: "routine", Title: "New routine: " + cp.Title, Detail: describeSchedule(cp.Schedule), RoutineID: cp.ID})
	h.saveLocked()
	out := cp
	return &out, nil
}

func (h *partnerHub) findRoutineLocked(id string) (int, *partnerRoutine) {
	id = strings.TrimSpace(id)
	for i, r := range h.st.Routines {
		if r.ID == id {
			return i, r
		}
	}
	return -1, nil
}

// dueRoutines returns copies of enabled routines whose next run has passed,
// and advances their next run so one slow tick never double-fires.
func (h *partnerHub) dueRoutines(now time.Time) []partnerRoutine {
	h.mu.Lock()
	defer h.mu.Unlock()
	var due []partnerRoutine
	changed := false
	for _, r := range h.st.Routines {
		if !r.Enabled || r.NextRun == "" {
			continue
		}
		next, err := time.Parse(time.RFC3339, r.NextRun)
		if err != nil || next.After(now) {
			continue
		}
		due = append(due, *r)
		// Advance from now, not from the missed slot: a PC that slept
		// through three runs owes one catch-up run, not three.
		if n := nextRunAfter(r.Schedule, now, time.Local); n.IsZero() {
			r.NextRun = ""
			r.Enabled = r.Schedule.Kind != "once" && r.Enabled
		} else {
			r.NextRun = n.UTC().Format(time.RFC3339)
		}
		changed = true
	}
	if changed {
		h.saveLocked()
	}
	return due
}
