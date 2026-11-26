package httpapi

import (
	"fmt"
	"sort"
	"strings"
)

// /model: see which model a conversation is on, list the ones on offer, and
// switch. Typed in any conversation, on the PC or a phone.

// slashModelArg is what follows "/model" or "/models" (or "/m"), or ok=false
// when the command is another one.
func slashModelArg(raw string) (arg string, ok bool) {
	fields := strings.Fields(raw)
	if len(fields) == 0 {
		return "", false
	}
	switch strings.ToLower(fields[0]) {
	case "/model", "/models", "/m":
		return strings.TrimSpace(strings.Join(fields[1:], " ")), true
	}
	return "", false
}

// matchModels returns the ids that `want` names: the one it equals, else every
// id that contains all of its words. Case and the choice of space, dash, dot or
// underscore between words do not matter ("fable 5.2" finds "claude-fable-5-2").
func matchModels(ids []string, want string) []string {
	fold := func(s string) string {
		s = strings.ToLower(s)
		return strings.Join(strings.FieldsFunc(s, func(r rune) bool {
			return r == ' ' || r == '-' || r == '_' || r == '.' || r == '/' || r == ':'
		}), " ")
	}
	w := fold(want)
	if w == "" {
		return nil
	}
	for _, id := range ids {
		if strings.EqualFold(id, strings.TrimSpace(want)) || fold(id) == w {
			return []string{id}
		}
	}
	words := strings.Fields(w)
	var out []string
	for _, id := range ids {
		f := " " + fold(id) + " "
		all := true
		for _, word := range words {
			if !strings.Contains(f, word) {
				all = false
				break
			}
		}
		if all {
			out = append(out, id)
		}
	}
	sort.Strings(out)
	return out
}

func (s *Server) slashModel(sessionID, arg string, phone bool) map[string]any {
	cfg := LoadConfig(s.homeDir)
	provider := strings.TrimSpace(cfgString(cfg, "llm_provider", ""))
	current := strings.TrimSpace(cfgString(cfg, "llm_model", ""))
	if s.sessions != nil && sessionID != "" {
		if sess, ok, err := s.sessions.Get(sessionID); err == nil && ok {
			if sess.LLMProvider != nil && strings.TrimSpace(*sess.LLMProvider) != "" {
				provider = strings.TrimSpace(*sess.LLMProvider)
			}
			if sess.Model != nil && strings.TrimSpace(*sess.Model) != "" {
				current = strings.TrimSpace(*sess.Model)
			}
		}
	}
	if provider == "" {
		return map[string]any{"text": "No model provider is set up yet. Connect one in Settings, then use /model."}
	}
	ids := s.offeredModelIDs(provider)
	// "provider/model" picks another connected provider, unless the whole thing
	// is a model of this one (some ids have a slash in them).
	if i := strings.Index(arg, "/"); i > 0 && len(matchModels(ids, arg)) == 0 {
		if other := strings.ToLower(strings.TrimSpace(arg[:i])); other != strings.ToLower(provider) {
			if otherIDs := s.offeredModelIDs(other); len(otherIDs) > 0 {
				provider, arg, ids = other, strings.TrimSpace(arg[i+1:]), otherIDs
			}
		}
	}
	if arg == "" {
		lines := []string{fmt.Sprintf("This conversation is on **%s · %s**.", provider, current)}
		if len(ids) == 0 {
			lines = append(lines, "I could not get the list of models from "+provider+" right now.")
		} else {
			shown := ids
			if len(shown) > 40 {
				shown = shown[:40]
			}
			lines = append(lines, "", "Models from "+provider+":")
			for _, id := range shown {
				lines = append(lines, "  "+id)
			}
			if len(ids) > len(shown) {
				lines = append(lines, fmt.Sprintf("  … and %d more", len(ids)-len(shown)))
			}
		}
		lines = append(lines, "", "To switch, send /model and part of a name, for example /model "+exampleModelWord(ids, current)+".")
		return map[string]any{"text": strings.Join(lines, "\n"), "action": "list_models", "provider": provider, "model": current}
	}
	if sessionID == "" || s.sessions == nil {
		return map[string]any{"text": "Open a conversation first, then use /model to choose its model."}
	}
	var pick string
	switch found := matchModels(ids, arg); {
	case len(found) == 1:
		pick = found[0]
	case len(found) > 1:
		if len(found) > 12 {
			found = found[:12]
		}
		return map[string]any{"text": "More than one model matches “" + arg + "”:\n  " + strings.Join(found, "\n  ") + "\nSend /model with more of the name."}
	case len(ids) == 0:
		// The provider's list is not reachable: take the name as given, checked below.
		pick = arg
	default:
		return map[string]any{"text": "No model from " + provider + " matches “" + arg + "”. Send /model to see the list."}
	}
	if why := (slashCaller{s: s, phone: phone}).allowed("PUT", "/api/sessions/"+sessionID+"/llm"); why != "" {
		return map[string]any{"text": why}
	}
	if err := s.checkModelOffered(provider, pick); err != nil {
		return map[string]any{"text": err.Error()}
	}
	if s.claims != nil {
		epoch, _, claimed := s.claims.TryClaim(sessionID)
		if !claimed {
			return map[string]any{"text": "Remedy is in the middle of a reply here. Stop it or wait, then send /model again."}
		}
		defer s.claims.Release(sessionID, &epoch)
	}
	baseHint := ""
	if strings.EqualFold(provider, cfgString(cfg, "llm_provider", "")) {
		baseHint = cfgString(cfg, "llm_base_url", "")
	}
	p, m, _ := normalizeLLMSettings(provider, pick, baseHint)
	if up, um, has := sessionLLMUpdateFields(&p, &m); has {
		if err := s.sessions.UpdateLLMBind(sessionID, up, um); err != nil {
			return map[string]any{"text": "Could not switch the model: " + err.Error()}
		}
		if up != nil {
			p = *up
		}
		if um != nil {
			m = *um
		}
	}
	s.rememberLastModel(p, m)
	return map[string]any{
		"text":     fmt.Sprintf("Now using **%s · %s** in this conversation.", p, m),
		"action":   "model_changed",
		"provider": p,
		"model":    m,
	}
}

// offeredModelIDs is the ids the provider lists right now, empty when it cannot be asked.
func (s *Server) offeredModelIDs(provider string) []string {
	res := s.listModels(provider, "", "")
	rows, _ := res["models"].([]map[string]any)
	out := make([]string, 0, len(rows))
	for _, r := range rows {
		if id := strings.TrimSpace(fmtString(r["id"])); id != "" {
			out = append(out, id)
		}
	}
	return out
}

// rememberLastModel records the model last used with a provider, as the picker does.
func (s *Server) rememberLastModel(provider, model string) {
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
	}
	if path == "" {
		return
	}
	cfg := LoadConfig(s.homeDir)
	if cfg == nil {
		cfg = ConfigMap{}
	}
	lastBy, _ := asStringMap(cfg["last_model_by_provider"])
	if lastBy == nil {
		lastBy = map[string]any{}
	}
	lastBy[provider] = model
	cfg["last_model_by_provider"] = lastBy
	_ = WriteConfig(path, cfg)
}

// exampleModelWord is a short word from a model other than the current one, for the hint.
func exampleModelWord(ids []string, current string) string {
	for _, id := range ids {
		if id == current {
			continue
		}
		parts := strings.FieldsFunc(id, func(r rune) bool { return r == '-' || r == '_' || r == '.' || r == '/' || r == ':' })
		for _, p := range parts {
			if len(p) >= 4 && !strings.EqualFold(p, "claude") && !strings.EqualFold(p, "model") {
				return strings.ToLower(p)
			}
		}
	}
	return "name"
}
