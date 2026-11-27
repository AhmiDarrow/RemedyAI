package httpapi

import (
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/dream"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/vigil"
)

// Her inner life runs in-process (native/go/mind). None of it calls a model.

func mindEnv(homeDir string) soul.Env {
	cfg := LoadConfig(homeDir)
	return soul.Env{
		Home:     ResolveHomeDir(homeDir),
		Clock:    time.Now,
		Knobs:    advancedTable(cfg),
		UserName: strings.TrimSpace(cfgString(cfg, "user_name", "")),
	}
}

// updateSoulAfterTurn is the soul half of the old prompt.turn_end: the
// field and proprioception update, then a dream when one is due. It works
// with or without the Python worker. One learn-log line per turn.
func (r *CognitionTurnRunner) updateSoulAfterTurn(req TurnRequest, out cognition.Outcome) {
	home := strings.TrimSpace(r.HomeDir)
	if home == "" {
		return
	}
	cfg := LoadConfig(home)
	if !cfgBool(cfg, "soul_field_enabled", true) {
		return
	}
	// The soul field is the owner's relationship with Remedy and is shown in
	// every later prompt. Only the owner's own turns may write to it: a
	// messenger contact or a peer saying "from now on" pledges nothing.
	if !OriginIsOwner(req.Origin) {
		return
	}
	env := mindEnv(home)
	prov, model := cfgString(cfg, "llm_provider", ""), cfgString(cfg, "llm_model", "")
	if req.Provider != nil && strings.TrimSpace(*req.Provider) != "" {
		prov = *req.Provider
	}
	if req.Model != nil && strings.TrimSpace(*req.Model) != "" {
		model = *req.Model
	}
	turn := soul.Turn{
		UserText: req.Prompt, AssistantText: out.Text, SessionID: req.SessionID,
		Provider: prov, Model: model,
	}
	if proj := strings.TrimSpace(req.ProjectPath); proj != "" && !isUnsetProjectPath(proj) {
		turn.ProjectPath = proj
	}
	started := time.Now()
	f, err := soul.UpdateAfterTurn(env, turn)
	changed := map[string]any{}
	if err == nil && f != nil && !isHiveSessionID(req.SessionID) {
		changed["episodes"] = len(f.Episodes)
		// Occasional densification; the cooldown lives in soul/dream.json.
		if len(f.Episodes) >= 3 || len(f.Pledges) > 0 || len(f.FutureDreams) > 0 {
			var due bool
			if due, err = dream.ShouldDream(env); err == nil && due {
				var res dream.Result
				if res, _, err = drives.Dream(env, false); err == nil && !res.Skipped {
					changed["dreamed"] = res.Top()
				}
			}
		}
	}
	appendLearnLog(home, "soul", time.Since(started), false, err, changed)
}

func vigilChanged(res vigil.Result) map[string]any {
	if !res.Woke {
		return nil
	}
	return map[string]any{"act": res.Act, "detail": res.Detail}
}

// vigilEnabledFor is the owner's night grant for the settings payload. An
// unreadable vigil.json reads as off (the tick reports the error itself).
func vigilEnabledFor(homeDir string) bool {
	st, err := vigil.Load(mindEnv(homeDir))
	return err == nil && st.Enabled
}
