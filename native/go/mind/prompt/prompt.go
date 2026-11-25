// Package prompt is the native port of the Python worker's prompt.* hooks
// (src/remedy/runtime/prompt_assemble.py and the modules it calls). The
// Python is the specification; testdata/ holds what it returned.
//
// The port goes section by section. A section whose producer leans on a
// subsystem with no native equivalent (the metabolism, the memory harness,
// the middleman store, project fingerprinting, ...) is not built here: it
// is reported in Missing, never faked. A caller may use a result only when
// Missing is empty; until then it keeps calling the Python worker.
package prompt

import (
	"sort"
	"time"
)

// Operation ids, as the worker registers them.
const (
	OpAssemble       = "prompt.assemble"
	OpSlimEpoch      = "prompt.slim_epoch"
	OpShouldContinue = "prompt.should_continue"
	OpAfterToolBatch = "prompt.after_tool_batch"
	OpTurnEnd        = "prompt.turn_end"
)

// Env is everything a call reads besides its input mapping.
type Env struct {
	// Home is the Remedy home (REMEDY_HOME).
	Home string
	// UserHome is the OS profile folder (Python's Path.home()).
	UserHome string
	// HelpRoots are the shipped owner's-manual folders, always readable
	// (help_docs.help_read_roots).
	HelpRoots []string
	// Clock is time.time() / datetime.now().
	Clock func() time.Time
	// Config is the parsed config.toml.
	Config map[string]any
	// Skills is the installed skill library; nil reads as none installed.
	Skills SkillCatalog
	// State is per-process memory the Python keeps on its cached runtime.
	// Nil means a fresh process.
	State *State
	// Sources is the runtime-owned data (memory, plan, checklist). Nil reads
	// as none.
	Sources Sources
	// LocalWindow is the context size of the local model in use, in tokens;
	// 0 when unknown.
	LocalWindow int
	// HouseRulesTool reports whether the house_rules tool is registered, so
	// the first-run offer is only made where it can be followed.
	HouseRulesTool bool
}

func (e Env) now() time.Time {
	if e.Clock != nil {
		return e.Clock()
	}
	return time.Now()
}

// SkillCatalog is the narrow skill-library reader assemble needs.
type SkillCatalog interface {
	// Count is how many skill packs are installed.
	Count() int
	// Rank is skill.search: up to limit packs for query, best first.
	Rank(query string, limit int, workspaceHint string) []SkillRow
}

// SkillRow is one ranked pack.
type SkillRow struct {
	Name        string
	Status      string
	Description string
}

// State is what the Python worker remembers between calls in one process.
type State struct {
	// WarmSkills is the last skills catalog body (nanoswarm rank cache).
	WarmSkills []string
}

// Section is one named piece of a built text: the text plus the separator
// that joins it to the piece before it.
type Section struct {
	Name string
	Sep  string
	Text string
}

// Result is one operation's answer.
type Result struct {
	// Output has the worker's output shape. It is complete only when
	// Missing is empty.
	Output map[string]any
	// Sections are the pieces this port built, in order.
	Sections []Section
	// Missing names what the Python would also build that this port does
	// not, so the output is incomplete.
	Missing []string
}

// Complete reports whether the result can stand in for the Python one.
func (r Result) Complete() bool { return len(r.Missing) == 0 }

// Join concatenates sections (sep + text each).
func Join(secs []Section) string {
	n := 0
	for _, s := range secs {
		n += len(s.Sep) + len(s.Text)
	}
	b := make([]byte, 0, n)
	for _, s := range secs {
		b = append(b, s.Sep...)
		b = append(b, s.Text...)
	}
	return string(b)
}

// notPortedOps are the operations with no native implementation at all,
// and what they would need.
var notPortedOps = map[string][]string{
	// The Memory Harness: an in-process Session Brief per session (history
	// thread, compress count), the build-state observer, and the send policy.
	OpSlimEpoch: {"slim_epoch:session_brief", "slim_epoch:build_state", "slim_epoch:send_policy"},
	// Build-engine state, the per-turn gate tracker, mission / brief open
	// work and the react_policy completion heuristics.
	OpShouldContinue: {"should_continue:build_state", "should_continue:gate_tracker", "should_continue:missions", "should_continue:heuristics"},
	// Metabolism evidence ledger (in-process) and the learn log.
	OpAfterToolBatch: {"after_tool_batch:metabolism"},
	// Post-turn prep, CAS residue, skill genome, the nanoswarm pattern
	// buffer, the LearningLoop and alive outcomes.
	OpTurnEnd: {"turn_end:metabolism", "turn_end:skill_genome", "turn_end:learning_loop", "turn_end:alive_outcome"},
}

// Missing is the side-effect-free readiness check: what the Go port cannot
// build for this call. A caller runs the Go operation only when it is empty.
func Missing(op string, env Env, input map[string]any) []string {
	switch op {
	case OpAssemble:
		return assembleMissing(env, parseAssembleInput(input))
	case OpShouldContinue:
		if boolIn(input, "chat_mode") {
			return nil
		}
	}
	if m, ok := notPortedOps[op]; ok {
		return append([]string(nil), m...)
	}
	return []string{op + ":unknown"}
}

// ShouldContinue is prompt.should_continue. Only the chat-mode answer is
// ported; anything else reports what is missing and no output.
func ShouldContinue(env Env, input map[string]any) Result {
	if boolIn(input, "chat_mode") {
		return Result{Output: map[string]any{
			"ok": true, "continue": false, "nudge": "", "reason": "chat_mode",
		}}
	}
	return Result{Missing: Missing(OpShouldContinue, env, input)}
}

// SlimEpoch, AfterToolBatch and TurnEnd are not ported (see notPortedOps).
func SlimEpoch(env Env, input map[string]any) Result {
	return Result{Missing: Missing(OpSlimEpoch, env, input)}
}

func AfterToolBatch(env Env, input map[string]any) Result {
	return Result{Missing: Missing(OpAfterToolBatch, env, input)}
}

func TurnEnd(env Env, input map[string]any) Result {
	return Result{Missing: Missing(OpTurnEnd, env, input)}
}

func sortedUnique(in []string) []string {
	seen := map[string]bool{}
	out := make([]string, 0, len(in))
	for _, s := range in {
		if !seen[s] {
			seen[s] = true
			out = append(out, s)
		}
	}
	sort.Strings(out)
	return out
}
