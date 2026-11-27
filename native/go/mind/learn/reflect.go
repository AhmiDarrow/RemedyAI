package learn

import (
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
)

// tstep is a TraceStep (reflection.py 17-26). The runtime never sends
// arguments; `remedy learn reflect --steps` does, and only their keys are
// kept (argKeys, already str()'d), because only the keys reach the skill.
type tstep struct {
	tool    string
	success bool
	summary string // at most 200 characters
	err     string
	dur     float64
	argKeys []string
}

type trace struct {
	taskID      string
	title       string
	description string
	steps       []tstep
	sessionID   string
}

func (t trace) successRate() float64 {
	if len(t.steps) == 0 {
		return 1.0
	}
	n := 0
	for _, s := range t.steps {
		if s.success {
			n++
		}
	}
	return float64(n) / float64(len(t.steps))
}

// buildTrace is LearningLoop.build_trace_from_dict (learning_loop.py 88-140).
func buildTrace(taskID, title, description string, steps []nstep, session string) trace {
	tr := trace{taskID: taskID, title: title, description: description, sessionID: session}
	for _, s := range steps {
		tr.steps = append(tr.steps, tstep{tool: s.tool, success: s.success, summary: rcut(s.result, 200), err: s.err, dur: s.dur})
	}
	return tr
}

func (t trace) toNSteps() []nstep {
	out := make([]nstep, len(t.steps))
	for i, s := range t.steps {
		out[i] = nstep{tool: s.tool, success: s.success, dur: s.dur}
	}
	return out
}

type toolSeq struct {
	tools   []string
	freq    int
	success float64
}

func (s toolSeq) describe() string { return strings.Join(s.tools, " -> ") + " pattern" }

// extractSequences is ReflectionEngine._extract_tool_sequences (py 134-170).
func extractSequences(steps []tstep) []toolSeq {
	if len(steps) < 2 {
		return nil
	}
	var order []string
	pos := map[string][]int{}
	for _, w := range []int{2, 3} {
		for i := 0; i+w <= len(steps); i++ {
			names := make([]string, w)
			for j := range names {
				names[j] = steps[i+j].tool
			}
			k := strings.Join(names, "\x00")
			if _, ok := pos[k]; !ok {
				order = append(order, k)
			}
			pos[k] = append(pos[k], i)
		}
	}
	var out []toolSeq
	for _, k := range order {
		ps := pos[k]
		if len(ps) < 2 {
			continue
		}
		tools := strings.Split(k, "\x00")
		ok := 0
		for _, p := range ps {
			all := true
			for j := range tools {
				all = all && steps[p+j].success
			}
			if all {
				ok++
			}
		}
		out = append(out, toolSeq{tools, len(ps), float64(ok) / float64(len(ps))})
	}
	sort.SliceStable(out, func(i, j int) bool { return out[i].freq > out[j].freq })
	return out
}

type generated struct {
	name, description, instructions string
	tags, tools                     []string
}

var nonSlug = regexp.MustCompile(`[^a-z0-9]+`)

func slugOf(s string) string {
	return strings.Trim(nonSlug.ReplaceAllString(strings.ToLower(s), "-"), "-")
}

// generate is ReflectionEngine._generate_skill_from_trace (py 191-262).
func generate(tr trace, patterns []toolSeq) *generated {
	if len(tr.steps) < minSteps {
		return nil
	}
	successful := 0
	for _, s := range tr.steps {
		if s.success {
			successful++
		}
	}
	if successful < minSteps {
		return nil
	}
	eff := computeEffort(tr.toNSteps())
	minRate := minStepRate
	if eff.hardWon() {
		minRate = minStepRateHrd
	}
	if tr.successRate() < minRate {
		return nil
	}

	g := &generated{name: proposeName(tr), description: buildDescription(tr), instructions: buildInstructions(tr, patterns, eff)}
	g.tags = suggestTags(tr, eff)
	g.tags = appendOnce(g.tags, "probation")
	g.tags = appendOnce(g.tags, "auto-generated")
	if eff.hardWon() {
		g.tags = appendOnce(g.tags, "hard-won")
	}
	if eff.score >= 0.5 {
		g.tags = appendOnce(g.tags, "high-effort")
	}
	// Python builds this from a set (random order); sorted here so output is stable.
	set := map[string]bool{}
	for _, s := range tr.steps {
		if s.success {
			set[s.tool] = true
		}
	}
	for t := range set {
		g.tools = append(g.tools, t)
	}
	sort.Strings(g.tools)
	return g
}

func appendOnce(l []string, v string) []string {
	for _, x := range l {
		if x == v {
			return l
		}
	}
	return append(l, v)
}

func proposeName(tr trace) string {
	var order []string
	cnt := map[string]int{}
	for _, s := range tr.steps {
		if cnt[s.tool] == 0 {
			order = append(order, s.tool)
		}
		cnt[s.tool]++
	}
	raw := "task"
	best := 0
	for _, t := range order { // Counter.most_common keeps first-seen order on ties
		if cnt[t] > best {
			best, raw = cnt[t], t
		}
	}
	prefix := slugOf(raw)
	if prefix == "" {
		prefix = "task"
	}
	slug := slugOf(tr.title)
	if slug == "" {
		slug = "skill"
	}
	return rcut(prefix+"-"+slug, 64)
}

func successTools(tr trace) []string {
	set := map[string]bool{}
	for _, s := range tr.steps {
		if s.success {
			set[s.tool] = true
		}
	}
	out := make([]string, 0, len(set))
	for t := range set {
		out = append(out, t)
	}
	sort.Strings(out)
	return out
}

func buildDescription(tr trace) string {
	tools := successTools(tr)
	txt := "general tools"
	if len(tools) > 0 {
		txt = strings.Join(tools[:min(8, len(tools))], ", ")
	}
	title := tr.title
	if title == "" {
		title = "multi-step task"
	}
	title = strings.TrimSpace(title)
	parts := []string{fmt.Sprintf("Use when the user needs help with: %s.", title), fmt.Sprintf("Procedure uses: %s.", txt)}
	for _, s := range tr.steps {
		if !s.success {
			parts = append(parts, "Includes recovery steps after failed tool attempts (hard-won path — prefer over naive retries).")
			break
		}
	}
	if tr.description != "" {
		d := strings.TrimSpace(tr.description)
		if len([]rune(d)) > 120 {
			d = rcut(d, 117) + "…"
		}
		parts = append(parts, "Context: "+d)
	}
	return strings.Join(parts, " ")
}

func buildInstructions(tr trace, patterns []toolSeq, eff effortScore) string {
	lines := []string{
		"# " + tr.title, "",
		"This skill was automatically generated from a successful execution.", "",
		"## Lifecycle (read before trusting)", "",
		"- Status starts as **probation** (discovered/validated), not fully active.",
		"- Only promote after repeated successes across **multiple sessions**.",
		"- If steps fail, recover or stop — do not invent success.",
		"- Prefer re-checking paths and tool results over blind replay.", "",
	}
	if eff.score >= 0.5 {
		lines = append(lines, "## Effort / why this matters", "",
			fmt.Sprintf("- **Effort weight:** %.2f (%s)", eff.score, eff.band()),
			fmt.Sprintf("- **Why hard-won:** %s", strings.Join(eff.reasons, ", ")),
			"- Preserve recovery paths below — they cost real work to discover.", "")
	}
	lines = append(lines, "## Steps", "")
	for i, s := range tr.steps {
		if !s.success {
			lines = append(lines, fmt.Sprintf("%d. **%s** [FAILED — do not treat as required success]", i+1, s.tool))
			if s.err != "" {
				lines = append(lines, "   - Error seen: "+s.err,
					"   - Recover: adjust arguments or try an alternate tool, then continue.")
			}
			lines = append(lines, "")
			continue
		}
		lines = append(lines, fmt.Sprintf("%d. **%s** [SUCCESS]", i+1, s.tool))
		if len(s.argKeys) > 0 {
			// Keep arg keys only (py 352-356): sorted, first 12.
			keys := append([]string(nil), s.argKeys...)
			sort.Strings(keys)
			if k := strings.Join(keys[:min(12, len(keys))], ", "); k != "" {
				lines = append(lines, "   - Argument keys: `"+k+"`")
			}
		}
		if s.summary != "" {
			lines = append(lines, "   - Result sketch: "+rcut(s.summary, 240))
		}
		lines = append(lines, "")
	}
	if len(patterns) > 0 {
		lines = append(lines, "## Reusable Patterns", "")
		for _, p := range patterns[:min(3, len(patterns))] {
			if p.success < 0.8 {
				continue
			}
			lines = append(lines, fmt.Sprintf("- `%s` (%dx, %s success)", p.describe(), p.freq, pct(p.success)))
		}
	}
	lines = append(lines, "", "## Failure protocol", "",
		"- If a step fails twice with different args, stop and report — do not loop.",
		"- Never mark the overall skill successful when critical tools failed.",
		"- After failure, prefer discovery (list_dir / status) before retry.", "",
		"## Requirements", "")
	for _, t := range successTools(tr) {
		lines = append(lines, fmt.Sprintf("- Tool: `%s`", t))
	}
	return strings.Join(lines, "\n")
}

func suggestTags(tr trace, eff effortScore) []string {
	tags := []string{"auto-generated", "learned"}
	if eff.hardWon() {
		tags = append(tags, "hard-won", "high-effort")
	} else if eff.score >= 0.5 {
		tags = append(tags, "high-effort")
	}
	r := tr.successRate()
	switch {
	case r >= 0.9:
		tags = append(tags, "high-confidence")
	case r >= 0.7:
		tags = append(tags, "medium-confidence")
	default:
		tags = append(tags, "needs-review")
	}
	for _, s := range tr.steps {
		switch {
		case strings.HasPrefix(s.tool, "git"):
			tags = append(tags, "git")
		case strings.HasPrefix(s.tool, "file"):
			tags = append(tags, "filesystem")
		case strings.HasPrefix(s.tool, "memory"):
			tags = append(tags, "memory")
		case strings.HasPrefix(s.tool, "web"):
			tags = append(tags, "web")
		}
	}
	var out []string
	for _, t := range tags {
		out = appendOnce(out, t)
	}
	return out
}

// reflectionConfidence is ReflectionEngine._compute_confidence (py 355-370).
func reflectionConfidence(tr trace, patterns []toolSeq) float64 {
	score := float64(tr.successRate() * 0.5)
	if len(patterns) > 0 {
		score += math.Min(float64(len(patterns))*0.1, 0.3)
	}
	errs := 0
	for _, s := range tr.steps {
		if !s.success && s.err != "" {
			errs++
		}
	}
	score -= float64(errs) * 0.1
	if len(tr.steps) > 10 {
		score += 0.1
	}
	if len(tr.steps) > 20 {
		score += 0.1
	}
	return math.Max(0.0, math.Min(1.0, score))
}
