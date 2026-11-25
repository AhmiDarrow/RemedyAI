package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/houserules"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// attachNativeHouseRules serves house_rules and setup.session from the Go
// port, so the first-run conversation and "talk to change a setting" never
// start the Python worker. Confirmed answers go through the same settings
// writer the Settings screen uses.
func attachNativeHouseRules(registry *tools.Registry, s *Server) error {
	if registry == nil || s == nil {
		return nil
	}
	for _, id := range []string{"house_rules", "setup.session"} {
		id := id
		if _, err := registry.Resolve(id, 1); err != nil {
			continue
		}
		err := registry.WrapExecutor(id, 1, func(tools.Executor) tools.Executor {
			return tools.ExecutorFunc(func(_ context.Context, req tools.Request) (tools.Result, error) {
				input := map[string]any{}
				if len(req.Input) > 0 {
					if err := json.Unmarshal(req.Input, &input); err != nil {
						return tools.Result{}, fmt.Errorf("%w: %v", tools.ErrInvalidInput, err)
					}
				}
				if id == "setup.session" {
					// The setup surface says "get" (or nothing) for status.
					if a, _ := input["action"].(string); a == "" || a == "get" {
						input["action"] = "status"
					}
				}
				out, err := houserules.Dispatch(s.houseRulesEnv(), input)
				if err != nil {
					// The result already carries the message meant for the
					// model; the cause is for the log.
					log.Printf("house rules: %v", err)
				}
				raw, mErr := json.Marshal(out)
				if mErr != nil {
					return tools.Result{}, mErr
				}
				return tools.Result{Output: raw}, nil
			})
		})
		if err != nil {
			return err
		}
	}
	return nil
}

func (s *Server) houseRulesEnv() houserules.Env {
	home := ResolveHomeDir(s.homeDir)
	return houserules.Env{
		Home:   home,
		Now:    time.Now,
		Config: map[string]any(LoadConfig(s.homeDir)),
		Apply:  s.applyHouseRulesPatch,
		StoreSecret: func(provider, key string) error {
			return secret.SetProviderSecret(home, provider, key)
		},
	}
}

// applyHouseRulesPatch writes confirmed house-rule answers. The settings
// writer replaces the [advanced] table as a whole, so the patch's advanced
// keys are laid over the current table first.
func (s *Server) applyHouseRulesPatch(patch map[string]any) error {
	updates := make(map[string]any, len(patch))
	for k, v := range patch {
		switch k {
		case "home_dir":
			// Where the config lives, not a setting in it.
		case "advanced":
			adv := advancedTable(LoadConfig(s.homeDir))
			if over, ok := v.(map[string]any); ok {
				for ak, av := range over {
					adv[ak] = av
				}
			}
			updates["advanced"] = adv
		default:
			updates[k] = v
		}
	}
	if len(updates) == 0 {
		return nil
	}
	result, err := s.applySettingsUpdate(updates)
	if err != nil {
		return err
	}
	s.publishSettingsUpdated(result)
	return nil
}
