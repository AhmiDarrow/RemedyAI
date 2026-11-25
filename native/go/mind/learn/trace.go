package learn

import (
	"fmt"
	"math"
	"regexp"
	"strings"
	"unicode/utf8"
)

// Step is one tool call of the turn, as the runtime records it.
type Step struct {
	Tool       string
	OK         bool
	Result     string
	Error      string
	DurationMS float64
}

// nstep is a normalised step: production_learn._normalize_steps (py 288-320).
type nstep struct {
	tool    string
	success bool
	result  string // at most 500 characters
	err     string // at most 240 characters
	dur     float64
}

func rcut(s string, n int) string {
	if utf8.RuneCountInString(s) <= n {
		return s
	}
	return string([]rune(s)[:n])
}

func normalizeSteps(raw []Step) []nstep {
	var out []nstep
	for _, s := range raw {
		name := strings.TrimSpace(s.Tool)
		if name == "" {
			continue
		}
		out = append(out, nstep{
			tool:    name,
			success: s.OK && s.Error == "",
			result:  rcut(s.Result, 500),
			err:     rcut(s.Error, 240),
			dur:     s.DurationMS,
		})
	}
	return out
}

// Meta tools alone do not count as real work (agent_learn.py 17-19).
var metaTools = map[string]bool{"skill_search": true, "skill_activate": true, "skill_reload": true, "local_discover": true}

func countRealSteps(steps []nstep) (real int, successes int) {
	for _, s := range steps {
		if !metaTools[s.tool] {
			real++
		}
		if s.success {
			successes++
		}
	}
	return
}

// shouldAutoLearn is agent_learn.should_auto_learn_from_steps (py 124-153).
func shouldAutoLearn(steps []nstep) bool {
	if len(steps) < 3 {
		return false
	}
	real, successes := countRealSteps(steps)
	if real < 3 && len(steps) < 4 {
		return false
	}
	if successes < 3 {
		return false
	}
	if successes < max(2, int(0.5*float64(len(steps)))) {
		return false
	}
	var names []string
	seen := map[string]bool{}
	for _, s := range steps {
		if metaTools[s.tool] || s.tool == "" || seen[s.tool] {
			continue
		}
		seen[s.tool] = true
		names = append(names, s.tool)
	}
	if len(names) < 2 {
		return false
	}
	return !(real < 4 && len(names) < 3)
}

// -- effort and lifecycle gate (core/learning/lifecycle.py) -------------------

const (
	effortLow    = 0.25
	effortMedium = 0.45
	effortHigh   = 0.62

	minSteps       = 3
	minStepRate    = 0.75
	minStepRateHrd = 0.40
	minSuccessful  = 3
)

type effortScore struct {
	score      float64
	failed     int
	recoveries int
	unique     int
	total      int
	duration   float64
	switches   int
	reasons    []string
}

func (e effortScore) band() string {
	switch {
	case e.score >= effortHigh:
		return "high"
	case e.score >= effortMedium:
		return "medium"
	case e.score >= effortLow:
		return "low"
	}
	return "trivial"
}

func (e effortScore) hardWon() bool { return e.score >= effortHigh }

// computeEffort is lifecycle.compute_effort_score (py 195-275). Products are
// converted to float64 so no platform fuses them into an FMA and drifts from
// Python's rounding.
func computeEffort(steps []nstep) effortScore {
	if len(steps) == 0 {
		return effortScore{reasons: []string{"empty trace"}}
	}
	failN, recoveries, switches := 0, 0, 0
	uniq := map[string]bool{}
	var dur float64
	for i, s := range steps {
		if !s.success {
			failN++
		}
		if s.tool != "" {
			uniq[s.tool] = true
		}
		dur += s.dur
		if i+1 < len(steps) {
			nx := steps[i+1]
			if s.tool != "" && nx.tool != "" && s.tool != nx.tool {
				switches++
			}
			if !s.success && nx.success && s.tool != nx.tool {
				recoveries++
			}
		}
	}
	n := len(steps)
	unique := len(uniq)
	fc := math.Min(1.0, float64(failN)/5.0)
	rc := math.Min(1.0, float64(recoveries)/3.0)
	tc := math.Min(1.0, float64(max(0, unique-1))/5.0)
	lc := math.Min(1.0, float64(max(0, n-3))/12.0)
	sc := math.Min(1.0, float64(switches)/6.0)
	dc := math.Min(1.0, dur/120000.0)
	score := float64(0.30*fc) + float64(0.24*rc) + float64(0.14*tc) + float64(0.12*lc) + float64(0.12*sc) + float64(0.08*dc)
	score = math.Max(0.0, math.Min(1.0, score))

	var reasons []string
	if failN > 0 {
		reasons = append(reasons, fmt.Sprintf("%d failed attempt(s)", failN))
	}
	if recoveries > 0 {
		reasons = append(reasons, fmt.Sprintf("%d recovery path(s)", recoveries))
	}
	if unique >= 3 {
		reasons = append(reasons, fmt.Sprintf("%d distinct tools", unique))
	}
	if switches >= 3 {
		reasons = append(reasons, fmt.Sprintf("%d approach switches", switches))
	}
	if dur >= 60000 {
		reasons = append(reasons, fmt.Sprintf("%.0fs of work", dur/1000))
	}
	if n >= 8 {
		reasons = append(reasons, fmt.Sprintf("%d-step trace", n))
	}
	if len(reasons) == 0 {
		reasons = append(reasons, "straightforward path")
	}
	return effortScore{score, failN, recoveries, unique, n, dur, switches, reasons}
}

type decision struct {
	action     string
	reason     string
	confidence float64
}

var toolChainRE = regexp.MustCompile(`^[a-z][a-z0-9_]*(?:-[a-z][a-z0-9_]*)+$`)

var simpleToolStems = map[string]bool{
	"bash": true, "comfyui": true, "web": true, "fetch": true, "read": true, "write": true, "edit": true,
	"list": true, "search": true, "mission": true, "plan": true, "goal": true, "spread": true, "job": true,
	"memory": true, "vision": true, "computer": true, "settings": true, "skill": true,
}

func looksLikeToolChainTitle(title string) bool {
	t := strings.ToLower(strings.TrimSpace(title))
	if t == "" || !toolChainRE.MatchString(t) {
		return false
	}
	var parts []string
	for _, p := range strings.Split(t, "-") {
		if p != "" {
			parts = append(parts, p)
		}
	}
	if len(parts) < 2 {
		return false
	}
	toolish := 0
	for _, p := range parts {
		if strings.Contains(p, "_") || simpleToolStems[p] {
			toolish++
		}
	}
	return toolish >= max(2, (len(parts)+1)/2)
}

func pct(rate float64) string { return fmt.Sprintf("%.0f%%", rate*100) }

// shouldAcceptTrace is SkillLifecyclePolicy.should_accept_trace (py 313-463).
func shouldAcceptTrace(stepCount, successful int, rate float64, hasPattern bool, title string, eff effortScore) decision {
	if stepCount < minSteps {
		return decision{"reject", fmt.Sprintf("Trace too short (%d < %d steps)", stepCount, minSteps), 0.1}
	}
	// overall_success is always true on this path (learn_from_tool_steps passes True).
	if successful < minSuccessful {
		return decision{"reject", fmt.Sprintf("Not enough successful steps (%d < %d)", successful, minSuccessful), 0.2}
	}
	minRate := minStepRate
	if eff.hardWon() {
		minRate = minStepRateHrd
	} else if eff.score >= effortMedium {
		minRate = 0.55
	}
	if rate < minRate {
		return decision{"reject", fmt.Sprintf("Step success rate too low (%s < %s) for effort=%s", pct(rate), pct(minRate), eff.band()), 0.25}
	}
	if !hasPattern && !eff.hardWon() && rate < 0.95 && eff.score < effortMedium {
		return decision{"reject", "No recurring tool pattern and low effort — avoid lucky one-offs", 0.35}
	}
	if eff.band() == "trivial" && !eff.hardWon() {
		if eff.unique < 3 || stepCount < 5 {
			return decision{"reject", fmt.Sprintf("Trivial low-diversity trace (unique_tools=%d, steps=%d) — not worth a skill", eff.unique, stepCount), 0.2}
		}
		if t := strings.TrimSpace(title); t != "" && looksLikeToolChainTitle(t) {
			return decision{"reject", "Trivial tool-chain title without meaningful effort — skip learn", 0.22}
		}
	}
	conf := math.Min(0.95, 0.40+float64(0.20*rate)+float64(0.20*eff.score)+boolF(hasPattern, 0.10)+math.Min(0.10, float64(successful)*0.02))
	why := fmt.Sprintf("Trace accepted (effort=%s %.2f: %s); skill enters probation",
		eff.band(), eff.score, strings.Join(eff.reasons[:min(4, len(eff.reasons))], ", "))
	return decision{"accept", why, conf}
}

func boolF(b bool, v float64) float64 {
	if b {
		return v
	}
	return 0
}

func successCount(steps []nstep) int {
	n := 0
	for _, s := range steps {
		if s.success {
			n++
		}
	}
	return n
}

// pregate is the nanoswarm pattern pre-gate (nanoswarm/pattern_nanobot.py 59-100)
// over this turn's steps. The Python buffer keeps the last 24 steps of a session.
func pregate(steps []nstep, title string) decision {
	if len(steps) > 24 {
		steps = steps[len(steps)-24:]
	}
	eff := computeEffort(steps)
	ok := successCount(steps)
	rate := float64(ok) / float64(max(1, len(steps)))
	names := map[string]bool{}
	for _, s := range steps {
		names[s.tool] = true
	}
	hasPattern := len(steps) >= 3 && len(names) < len(steps)
	return shouldAcceptTrace(len(steps), ok, rate, hasPattern, title, eff)
}
