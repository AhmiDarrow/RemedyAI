package httpapi

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/prompt"
)

// The native prompt.* port (native/go/mind/prompt) sits behind the
// native_prompt config key, default off. With it off nothing here runs.
// With it on, an operation uses the Go port only when the port covers every
// section that call needs (prompt.Missing is empty); otherwise the Python
// worker answers, exactly as before.

// nativePromptState is the per-process memory the Python worker keeps on
// its cached runtime (the warm skills catalog).
var nativePromptState = struct {
	sync.Mutex
	st prompt.State
}{}

// useNativePrompt is the routing decision for one call.
func useNativePrompt(cfg ConfigMap, missing []string) bool {
	return cfgBool(cfg, "native_prompt", false) && len(missing) == 0
}

// nativeSkillCatalog is the installed skill library, ranked by the native
// skill.search.
type nativeSkillCatalog struct{ recs []skillRecord }

func (c nativeSkillCatalog) Count() int { return len(c.recs) }

func (c nativeSkillCatalog) Rank(query string, limit int, hint string) []prompt.SkillRow {
	ranked := rankSkills(c.recs, query, limit, hint)
	out := make([]prompt.SkillRow, 0, len(ranked))
	for _, r := range ranked {
		out = append(out, prompt.SkillRow{Name: r.rec.Name, Status: r.rec.Status, Description: r.rec.Description})
	}
	return out
}

// helpReadRoots are the shipped manual folders (help_docs.help_read_roots):
// REMEDY_DEV_ROOT's docs, then the staged copy under the home.
func helpReadRoots(home string) []string {
	var cands []string
	if dev := strings.TrimSpace(os.Getenv("REMEDY_DEV_ROOT")); dev != "" {
		cands = append(cands, filepath.Join(dev, "docs", "manual"), filepath.Join(dev, "desktop", "src", "help", "articles"))
	}
	cands = append(cands, filepath.Join(home, "help", "manual"), filepath.Join(home, "help", "articles"))
	var out []string
	seen := map[string]bool{}
	for _, c := range cands {
		key := strings.ToLower(filepath.Clean(c))
		if st, err := os.Stat(c); err == nil && st.IsDir() && !seen[key] {
			seen[key] = true
			out = append(out, filepath.Clean(c))
		}
	}
	return out
}

func (r *CognitionTurnRunner) nativePromptEnv(cfg ConfigMap) prompt.Env {
	home := ResolveHomeDir(r.HomeDir)
	userHome, _ := os.UserHomeDir()
	s := &Server{homeDir: r.HomeDir}
	return prompt.Env{
		Home: home, UserHome: userHome, HelpRoots: helpReadRoots(home),
		Clock: time.Now, Config: map[string]any(cfg),
		Skills: nativeSkillCatalog{recs: s.discoverSkills()},
		State:  &nativePromptState.st,
	}
}

// nativePrompt answers op in-process when native_prompt is on and the port
// is complete for this call. ok=false means: call the Python worker.
func (r *CognitionTurnRunner) nativePrompt(op string, input map[string]any) (map[string]any, bool) {
	if r == nil {
		return nil, false
	}
	cfg := LoadConfig(r.HomeDir)
	if !cfgBool(cfg, "native_prompt", false) {
		return nil, false
	}
	env := r.nativePromptEnv(cfg)
	if !useNativePrompt(cfg, prompt.Missing(op, env, input)) {
		return nil, false
	}
	nativePromptState.Lock()
	defer nativePromptState.Unlock()
	var res prompt.Result
	switch op {
	case prompt.OpAssemble:
		var err error
		if res, err = prompt.Assemble(env, input); err != nil {
			return nil, false
		}
	case prompt.OpShouldContinue:
		res = prompt.ShouldContinue(env, input)
	case prompt.OpSlimEpoch:
		res = prompt.SlimEpoch(env, input)
	case prompt.OpAfterToolBatch:
		res = prompt.AfterToolBatch(env, input)
	case prompt.OpTurnEnd:
		res = prompt.TurnEnd(env, input)
	default:
		return nil, false
	}
	if !res.Complete() || res.Output == nil {
		return nil, false
	}
	return res.Output, true
}
