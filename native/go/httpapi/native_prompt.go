package httpapi

import (
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/prompt"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
)

// The prompt is built in Go (native/go/mind/prompt). The Python worker's
// prompt.* hooks are used only when native_prompt = false in config.toml and
// a worker is attached.

// nativePromptState is the per-process memory the Python worker keeps on
// its cached runtime (the warm skills catalog).
var nativePromptState = struct {
	sync.Mutex
	st prompt.State
}{}

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

// nativeOn reports whether this runner builds its prompt in Go: wherever the
// server supplied its stores, which is every real runtime.
func (r *CognitionTurnRunner) nativeOn() bool {
	return r != nil && r.PromptSources != nil
}

func (r *CognitionTurnRunner) nativePromptEnv(cfg ConfigMap) prompt.Env {
	home := ResolveHomeDir(r.HomeDir)
	userHome, _ := os.UserHomeDir()
	s := &Server{homeDir: r.HomeDir}
	houseRules := false
	if r.Registry != nil {
		_, err := r.Registry.Latest("house_rules")
		houseRules = err == nil
	}
	return prompt.Env{
		Home: home, UserHome: userHome, HelpRoots: helpReadRoots(home),
		Clock: time.Now, Config: map[string]any(cfg),
		Skills:         nativeSkillCatalog{recs: s.discoverSkills()},
		State:          &nativePromptState.st,
		Sources:        r.PromptSources,
		LocalWindow:    providers.LocalContextWindow,
		HouseRulesTool: houseRules,
	}
}
