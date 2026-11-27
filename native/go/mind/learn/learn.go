// Package learn is the post-turn skill learning step, ported from Python
// (core/production_learn.py, core/agent_learn.py, core/learning_loop.py and
// what they reach in core/learning and nanoswarm). It never calls a model.
package learn

import (
	"crypto/rand"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Turn is what the runtime knows when a turn ends.
type Turn struct {
	Home, SessionID, Message, AssistantText string
	Steps                                   []Step
	Aborted                                 bool
	Now                                     func() time.Time
	// Skills are the learned skills in play this turn (skill_activate'd or
	// auto-suggested). Optional; they are graded by how the turn went.
	Skills []string
}

// Outcome reports what, if anything, was learned.
type Outcome struct {
	Learned bool
	Skill   string
	Merged  bool
	Reason  string
	// Graded maps each skill whose turn outcome was recorded to its verdict.
	Graded map[string]bool
}

// FromTurn is the post-turn learning step. It never calls a model.
func FromTurn(t Turn) (Outcome, error) {
	home := expandHome(t.Home)
	if home == "" {
		return Outcome{Reason: "no home folder"}, nil
	}
	now := t.Now
	if now == nil {
		now = time.Now
	}
	steps := normalizeSteps(t.Steps)
	var out Outcome
	var errs []error
	// agent.py 1217: an interrupted turn is never learned from, only graded.
	if !t.Aborted {
		o, err := learnFromSteps(home, t, steps, now())
		out = o
		errs = append(errs, err)
	} else {
		out.Reason = "turn aborted"
	}
	graded, err := gradeSkills(home, t, steps, now())
	out.Graded = graded
	errs = append(errs, err)
	return out, errors.Join(errs...)
}

func expandHome(h string) string {
	h = strings.TrimSpace(h)
	if h == "~" || strings.HasPrefix(h, "~/") || strings.HasPrefix(h, `~\`) {
		if u, err := os.UserHomeDir(); err == nil {
			h = filepath.Join(u, h[1:])
		}
	}
	return h
}

// learnFromSteps is agent_learn.auto_learn_from_turn (py 156-210).
func learnFromSteps(home string, t Turn, steps []nstep, now time.Time) (Outcome, error) {
	skillsDir := filepath.Join(home, "skills")
	if err := os.MkdirAll(skillsDir, 0o700); err != nil { // LearningLoop.__init__
		return Outcome{}, err
	}
	if !shouldAutoLearn(steps) {
		return Outcome{Reason: "not enough tool work to learn from"}, nil
	}
	title := skillTitle(t.Message, steps)
	if d := pregate(steps, title); d.action == "reject" || d.action == "skip" {
		return Outcome{Reason: d.reason}, nil
	}
	if title == "" {
		title = "multi-tool-task"
	}
	session := strings.TrimSpace(t.SessionID)
	name, merged, reason, err := learnFromToolSteps(skillsDir, now, title, steps, session, safeLearnDescription(t.Message))
	return Outcome{Learned: name != "", Skill: name, Merged: merged, Reason: reason}, err
}

func newUUID() string {
	var b [16]byte
	_, _ = rand.Read(b[:])
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:])
}

// isoNow is datetime.now(UTC).isoformat().
func isoNow(t time.Time) string {
	t = t.UTC()
	if t.Nanosecond()/1000 == 0 {
		return t.Format("2006-01-02T15:04:05") + "+00:00"
	}
	return t.Format("2006-01-02T15:04:05.000000") + "+00:00"
}

// learnFromToolSteps is LearningLoop.learn_from_tool_steps + learn_from_trace
// (learning_loop.py 142-270, 541-562). It returns the skill name when one was
// written or merged, and the lifecycle decision's reason either way.
func learnFromToolSteps(skillsDir string, now time.Time, title string, steps []nstep, session, description string) (string, bool, string, error) {
	tr := buildTrace(newUUID(), rcut(title, 120), description, steps, session)
	if tr.title == "" {
		tr.title = "tool-session"
	}
	sk, merged, reason, err := learnTrace(skillsDir, now, tr)
	if sk == nil {
		return "", merged, reason, err
	}
	return sk.name, merged, reason, err
}

// learnTrace is LearningLoop.learn_from_trace (learning_loop.py 142-270): the
// gate, reflection, merge-or-write. It returns the skill written or merged
// into (nil when none) and the lifecycle decision's reason either way.
func learnTrace(skillsDir string, now time.Time, tr trace) (*skill, bool, string, error) {
	if len(tr.steps) < minSteps {
		return nil, false, "Trace too short for reflection", nil
	}
	eff := computeEffort(tr.toNSteps())
	patterns := extractSequences(tr.steps)
	successful := successCount(tr.toNSteps())
	gate := shouldAcceptTrace(len(tr.steps), successful, tr.successRate(), len(patterns) > 0, tr.title, eff)
	if gate.action != "accept" {
		return nil, false, gate.reason, nil
	}
	gs := generate(tr, patterns)
	if gs == nil {
		return nil, false, "Reflection produced no skill candidate", nil
	}
	conf := math.Max(gate.confidence, reflectionConfidence(tr, patterns))

	if ex, err := readSkill(filepath.Join(skillsDir, safeDirName(gs.name))); err == nil {
		if merged, reason, err := mergeInto(skillsDir, ex, gs, tr, conf, eff); err != nil {
			return nil, false, "", err
		} else if merged {
			return ex, true, reason, nil
		}
		// A library or imported pack is never rewritten (py 594-599). Python then
		// overwrites it with a fresh probation skill; that is not ported.
		return nil, false, fmt.Sprintf("Skill '%s' is a curated or imported pack; not overwritten", ex.name), nil
	}

	status := "discovered"
	if conf >= 0.85 || (eff.score >= effortHigh && conf >= 0.7) {
		status = "validated"
	}
	tags := appendOnce(append([]string(nil), gs.tags...), "probation")
	if eff.hardWon() {
		tags = appendOnce(tags, "hard-won")
	}
	var sess any
	if tr.sessionID != "" {
		sess = tr.sessionID
	}
	sk := &skill{
		name: gs.name, description: gs.description, version: "0.1.0", status: status, tags: tags, tools: gs.tools,
		instructions: gs.instructions,
		meta: omap{
			{"auto_generated", true},
			{"created_at", isoNow(now)},
			{"source_trace_id", tr.taskID},
			{"source_trace_ids", []string{tr.taskID}},
			{"source_task", tr.title},
			{"source_session_id", sess},
			{"reflection_confidence", reflectionConfidence(tr, patterns)},
			{"lifecycle_confidence", conf},
			{"lifecycle", "probation"},
			{"overall_trace_success", true},
			{"trace_success_rate", tr.successRate()},
			{"effort_weight", eff.score},
			{"effort_band", eff.band()},
			{"effort_reasons", append([]string(nil), eff.reasons...)},
		},
	}
	// validate_metadata (skills/validator.py 56-70): errors refuse the candidate.
	if strings.TrimSpace(sk.name) == "" {
		return nil, false, "Validation failed: Skill name is missing or empty", nil
	}
	if utf8Len(sk.description) < 10 {
		return nil, false, "Validation failed: Description must be at least 10 characters", nil
	}
	path, err := writeSkill(skillsDir, sk)
	if err != nil {
		return nil, false, "", err
	}
	abs := path
	if r, err := filepath.EvalSymlinks(path); err == nil {
		abs = r
	} else if a, err := filepath.Abs(path); err == nil {
		abs = a
	}
	sk.meta.set("skill_path", abs)
	sk.meta.set("lifecycle", "probation")
	sk.meta.set("creation_gate", gate.reason)
	sk.meta.set("effort_weight", eff.score)
	sk.meta.set("effort_band", eff.band())
	sk.meta.set("effort_reasons", append([]string(nil), eff.reasons...))
	elevate := eff.hardWon() || (conf >= 0.8 && eff.score >= 0.45)
	if elevate && sk.status == "discovered" {
		sk.status = "validated"
		sk.meta.set("lifecycle", "validated-probation")
		if _, err := writeSkill(skillsDir, sk); err != nil {
			return nil, false, "", err
		}
	}
	reason := fmt.Sprintf("Skill '%s' saved as %s (probation, effort=%s %.2f)", sk.name, sk.status, eff.band(), eff.score)
	return sk, false, reason, nil
}

func utf8Len(s string) int { return len([]rune(s)) }

func toFloat(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	case string:
		f, _ := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return f
	}
	return 0
}

var reVersion = regexp.MustCompile(`^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$`)

func bumpVersion(v string) string {
	if v == "" {
		v = "0.1.0"
	}
	m := reVersion.FindStringSubmatch(strings.TrimSpace(v))
	if m == nil {
		return v + "+m"
	}
	n := func(s string) int { i, _ := strconv.Atoi(s); return i }
	return fmt.Sprintf("%d.%d.%d", n(m[1]), n(m[2]), n(m[3])+1)
}

// splitLines is str.splitlines.
func splitLines(s string) []string {
	var out []string
	start := 0
	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		switch rs[i] {
		case '\n', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029:
		case '\r':
			out = append(out, string(rs[start:i]))
			if i+1 < len(rs) && rs[i+1] == '\n' {
				i++
			}
			start = i + 1
			continue
		default:
			continue
		}
		out = append(out, string(rs[start:i]))
		start = i + 1
	}
	if start < len(rs) {
		out = append(out, string(rs[start:]))
	}
	return out
}

// mergeInto is LearningLoop._merge_into_existing (py 581-687). It reports false
// when the existing pack is a library or imported one.
func mergeInto(skillsDir string, ex *skill, gs *generated, tr trace, conf float64, eff effortScore) (bool, string, error) {
	if s, _ := ex.meta.get("source"); s != nil && scalarText(s) == "library" {
		return false, "", nil
	}
	if v, _ := ex.meta.get("library_id"); v != nil && v != "" && v != false {
		return false, "", nil
	}
	if tv, _ := ex.meta.get("trust"); tv != nil {
		switch scalarText(tv) {
		case "imported", "library-install", "library-update":
			return false, "", nil
		}
	}
	oldEffort, _ := ex.meta.get("effort_weight")
	newEffort := math.Max(toFloat(oldEffort), eff.score)

	if gs.description != "" && utf8Len(gs.description) > utf8Len(ex.description) {
		ex.description = gs.description
	}
	for _, t := range gs.tags {
		ex.tags = appendOnce(ex.tags, t)
	}
	for _, t := range gs.tools {
		ex.tools = appendOnce(ex.tools, t)
	}

	block := fmt.Sprintf("\n\n## Merged experience (%s)\n\n_Trace success rate: %s; effort=%s_\n\n", tr.title, pct(tr.successRate()), eff.band())
	var keep []string
	for _, line := range splitLines(gs.instructions) {
		low := strings.ToLower(line)
		if strings.Contains(low, "fail") || strings.Contains(low, "recover") || strings.Contains(low, "error") {
			keep = append(keep, line)
		}
	}
	if len(keep) > 0 {
		block += strings.Join(keep[:min(40, len(keep))], "\n") + "\n"
	} else {
		block += fmt.Sprintf("- Additional successful path with tools: %s\n", strings.Join(gs.tools[:min(8, len(gs.tools))], ", "))
	}
	ex.instructions = strings.TrimRight(ex.instructions, " \t\r\n\f\v") + block

	ex.meta.set("auto_generated", true)
	lc, _ := ex.meta.get("lifecycle_confidence")
	ex.meta.set("lifecycle_confidence", math.Max(toFloat(lc), conf))
	ex.meta.set("effort_weight", newEffort)
	if newEffort >= 0.62 {
		ex.meta.set("effort_band", "high")
	} else {
		ex.meta.set("effort_band", eff.band())
	}
	var ids []string
	if v, ok := ex.meta.get("source_trace_ids"); ok {
		ids = strList(v)
	}
	ids = appendOnce(ids, tr.taskID)
	if len(ids) > 20 {
		ids = ids[len(ids)-20:]
	}
	ex.meta.set("source_trace_ids", ids)
	ex.meta.set("last_merge_task", tr.title)
	ex.version = bumpVersion(ex.version)

	if _, err := writeSkill(skillsDir, ex); err != nil {
		return false, "", err
	}
	return true, fmt.Sprintf("Merged into existing skill '%s' (v%s, effort=%.2f)", ex.name, ex.version, newEffort), nil
}
