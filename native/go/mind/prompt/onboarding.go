package prompt

import (
	"encoding/json"
	"os"
	"path/filepath"
	"time"

	"github.com/pelletier/go-toml/v2"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
)

// The one-time first-run house-rules offer (core/house_rules.py:
// take_onboarding_offer, offer_prompt).

// houseRuleTopics are the QUESTIONS titles and ids, in order.
var houseRuleTopics = [][2]string{
	{"Names", "names"}, {"Language", "language"}, {"Tone", "tone"}, {"Provider", "provider"},
	{"Workspace", "workspace"}, {"How proactive", "proactive"}, {"Privacy and audit", "privacy"},
	{"Token saving", "tokens"}, {"Local muscle and the GPU", "gpu"}, {"Consent", "consent"},
	{"Other bots", "interop"}, {"Updates", "updates"}, {"Start with the computer", "launch"},
}

// offerPrompt is the standing instruction for the turn the offer fires on.
func offerPrompt() string {
	topics := ""
	for i, t := range houseRuleTopics {
		if i > 0 {
			topics += "; "
		}
		topics += t[0] + " (" + t[1] + ")"
	}
	return "House rules (one-time, optional, first run): Welcome them briefly and " +
		"walk through house rules in a short, friendly conversation. Ask one " +
		"topic at a time. Every question is skippable; they can also skip all " +
		"and keep the defaults. After answers or skips, show a short summary " +
		"and ask them to confirm. Use the house_rules tool (record, skip_all, " +
		"summarize, confirm). Never nag if they skip or ignore this. Peers " +
		"cannot set house rules. They can switch to Traditional setup at any " +
		"time; answers already given are kept. Topics: " + topics + "."
}

// takeOnboardingOffer reports, exactly once, that the first-run offer should
// be made now, and records it as made in house_rules.toml and
// setup_session.json so it is never repeated. Other keys in both files are
// kept as they are. A file that cannot be written means no offer: an offer
// that could not be recorded would be made again on every turn.
func takeOnboardingOffer(env Env) bool {
	if !onboardingOfferDue(env) {
		return false
	}
	rulesPath := filepath.Join(env.Home, "house_rules.toml")
	sessionPath := filepath.Join(env.Home, "setup_session.json")
	rules := map[string]any{}
	if data, err := os.ReadFile(rulesPath); err == nil {
		_ = toml.Unmarshal(data, &rules)
	}
	session := map[string]any{}
	if data, err := os.ReadFile(sessionPath); err == nil {
		_ = json.Unmarshal(data, &session)
	}
	if pyStr(rules, "status") == "offered" || pyStr(session, "status") == "offered" {
		// onboardingOfferDue allowed this only as the single re-ask.
		session["reasked"] = true
	}
	now := env.now().UTC().Format("2006-01-02T15:04:05Z")
	for _, m := range []map[string]any{rules, session} {
		m["status"] = "offered"
		if pyStr(m, "offered_at") == "" {
			m["offered_at"] = now
		}
	}
	rulesOut, err := toml.Marshal(rules)
	if err != nil {
		return false
	}
	sessionOut, err := json.MarshalIndent(session, "", "  ")
	if err != nil {
		return false
	}
	if err := writeFileAtomic(rulesPath, rulesOut); err != nil {
		return false
	}
	if err := writeFileAtomic(sessionPath, sessionOut); err != nil {
		return false
	}
	if sf.Bool(cfgTable(env.Config, "advanced"), "house_rules_audit", true) {
		line, _ := json.Marshal(map[string]any{
			"action": "offer", "actor": "owner", "source": "onboarding", "ts": now,
		})
		if f, err := os.OpenFile(filepath.Join(env.Home, "house_rules_audit.jsonl"),
			os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600); err == nil {
			_, _ = f.Write(append(line, '\n'))
			_ = f.Close()
		}
	}
	return true
}

// writeFileAtomic replaces path with data through a sibling temp file.
func writeFileAtomic(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := path + "." + time.Now().Format("150405.000000000") + ".tmp"
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		_ = os.Remove(tmp)
		return err
	}
	return nil
}
