package httpapi

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	updateOfferID        = "major-changes-2026-11"
	updateOfferTitle     = "House rules"
	updateOfferBody      = "Remedy now has smarter token saving, local muscle sharing, and talking with other bots (A2A and MCP). It's worth a few minutes to go over the house rules together."
	updateOfferPrimary   = "Let's go"
	updateOfferSecondary = "Not now"
)

// First-run setup handoff: the installer places app files, the app downloads
// the mind with a progress bar, then Remedy continues setup as a conversation
// (Traditional setup is always available). Shared draft lives in setup_session.json.

var setupSessionMu sync.Mutex

type setupSession struct {
	Version           int            `json:"version"`
	Mode              string         `json:"mode"`
	Status            string         `json:"status"`
	OnboardingPending bool           `json:"onboarding_pending"`
	MindFailure       map[string]any `json:"mind_failure"`
	Draft             map[string]any `json:"draft"`
	Skipped           []string       `json:"skipped"`
	SecretsSet        []string       `json:"secrets_set"`
	OfferedAt         string         `json:"offered_at"`
	CompletedAt       string         `json:"completed_at"`
	Source            string         `json:"source"`
	SetupSessionID    string         `json:"setup_session_id"`
	FallbackSuggested bool           `json:"fallback_suggested,omitempty"`
	UpdateOffer       string         `json:"update_offer,omitempty"`
	UpdateOfferID     string         `json:"update_offer_id,omitempty"`
	UpdateOfferAt     string         `json:"update_offer_at,omitempty"`
}

func setupSessionPath(home string) string {
	return filepath.Join(ResolveHomeDir(home), "setup_session.json")
}

func emptySetupSession() setupSession {
	return setupSession{
		Version: 1,
		Mode:    "conversation",
		Status:  "pending",
		Draft:   map[string]any{},
		Skipped: []string{},
	}
}

func loadSetupSession(home string) setupSession {
	s := emptySetupSession()
	data, err := os.ReadFile(setupSessionPath(home))
	if err != nil {
		return s
	}
	_ = json.Unmarshal(data, &s)
	if s.Draft == nil {
		s.Draft = map[string]any{}
	}
	s.Draft = scrubSetupMap(s.Draft)
	if s.Mode != "traditional" {
		s.Mode = "conversation"
	}
	if s.Version == 0 {
		s.Version = 1
	}
	return s
}

func saveSetupSession(home string, sess setupSession) error {
	sess.Version = 1
	sess.Draft = scrubSetupMap(sess.Draft)
	if sess.Draft == nil {
		sess.Draft = map[string]any{}
	}
	return writeJSONAtomic(setupSessionPath(home), sess)
}

func isSetupSecretKey(k string) bool {
	kl := strings.ToLower(strings.TrimSpace(k))
	if kl == "llm_api_key" || kl == "api_key" || kl == "token" || kl == "password" || kl == "secret" {
		return true
	}
	return strings.HasSuffix(kl, "_token") || strings.HasSuffix(kl, "_password") ||
		strings.HasSuffix(kl, "_secret") || strings.HasSuffix(kl, "_key")
}

func scrubSetupMap(in map[string]any) map[string]any {
	if in == nil {
		return map[string]any{}
	}
	out := map[string]any{}
	for k, v := range in {
		if isSetupSecretKey(k) {
			continue
		}
		out[k] = v
	}
	return out
}

func classifyMindFailure(status map[string]any) map[string]any {
	if status == nil {
		return nil
	}
	progress, _ := status["progress"].(map[string]any)
	errStr := ""
	phase := ""
	if progress != nil {
		errStr = strings.TrimSpace(fmtSprint(progress["error"]))
		phase = strings.ToLower(strings.TrimSpace(fmtSprint(progress["phase"])))
	}
	if errStr == "" {
		errStr = strings.TrimSpace(fmtSprint(status["error"]))
	}
	health, _ := status["health"].(map[string]any)
	if errStr != "" || phase == "failed" || phase == "error" {
		reason := "download"
		low := strings.ToLower(errStr)
		if strings.Contains(low, "verify") || strings.Contains(low, "sha") || strings.Contains(low, "checksum") || strings.Contains(low, "hash") {
			reason = "verify"
		} else if strings.Contains(low, "memory") || strings.Contains(low, "ram") || strings.Contains(low, "oom") {
			reason = "low_ram"
		}
		if errStr == "" {
			errStr = "The mind could not be prepared."
		}
		return map[string]any{"reason": reason, "detail": errStr, "at": time.Now().UTC().Format("2006-01-02T15:04:05Z")}
	}
	if health != nil {
		ram, ramOK := anyToFloat(health["ram_gb"])
		min, minOK := anyToFloat(health["min_ram_gb"])
		if ramOK && minOK && ram < min {
			return map[string]any{
				"reason": "low_ram",
				"detail": "This computer does not have enough RAM to start the mind comfortably.",
				"at":     time.Now().UTC().Format("2006-01-02T15:04:05Z"),
			}
		}
	}
	return nil
}

func fmtSprint(v any) string {
	if v == nil {
		return ""
	}
	return strings.TrimSpace(strings.ReplaceAll(
		// fmt is already imported across the package via other files; use a local format.
		func() string {
			b, err := json.Marshal(v)
			if err != nil {
				return ""
			}
			s := string(b)
			if len(s) >= 2 && s[0] == '"' {
				var out string
				if json.Unmarshal(b, &out) == nil {
					return out
				}
			}
			if s == "null" {
				return ""
			}
			return s
		}(), `"`, ``))
}

func mindIsReady(status map[string]any) bool {
	if status == nil || classifyMindFailure(status) != nil {
		return false
	}
	if setupAsBool(status["installed"]) && (setupAsBool(status["ready"]) || setupAsBool(status["running"])) {
		return true
	}
	return setupAsBool(status["installed"])
}

func setupAsBool(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		s := strings.ToLower(strings.TrimSpace(t))
		return s == "1" || s == "true" || s == "yes" || s == "on"
	default:
		return false
	}
}

func (s *Server) setupSnapshot() map[string]any {
	home := s.remedyHomeDir()
	setupSessionMu.Lock()
	sess := loadSetupSession(home)
	setupSessionMu.Unlock()

	mind := s.visionStatusPayload(true)
	if mind == nil {
		mind = map[string]any{}
	}
	failure := sess.MindFailure
	if live := classifyMindFailure(mind); live != nil {
		failure = live
		setupSessionMu.Lock()
		sess.MindFailure = live
		sess.FallbackSuggested = true
		_ = saveSetupSession(home, sess)
		setupSessionMu.Unlock()
	}
	progress, _ := mind["progress"].(map[string]any)
	var progressOut any
	if progress != nil {
		progressOut = map[string]any{
			"phase":       progress["phase"],
			"message":     progress["message"],
			"bytes_done":  progress["bytes_done"],
			"bytes_total": progress["bytes_total"],
			"error":       progress["error"],
			"cancellable": progress["cancellable"],
		}
	}
	return map[string]any{
		"version":               1,
		"mode":                  sess.Mode,
		"status":                sess.Status,
		"onboarding_pending":    sess.OnboardingPending,
		"mind_ready":            mindIsReady(mind),
		"mind_failure":          failure,
		"mind_progress":         progressOut,
		"fallback_suggested":    sess.FallbackSuggested || failure != nil,
		"draft":                 sess.Draft,
		"skipped":               sess.Skipped,
		"secrets_set":           sess.SecretsSet,
		"setup_session_id":      sess.SetupSessionID,
		"traditional_available": true,
		"questions":             setupQuestionCatalog(),
		"update_offer":          s.updateOfferPayload(sess),
	}
}

func (s *Server) handleGetSetup(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.setupSnapshot())
}

type setupPatch struct {
	Mode              *string        `json:"mode"`
	Draft             map[string]any `json:"draft"`
	Skipped           []string       `json:"skipped"`
	Action            string         `json:"action"`
	SetupSessionID    *string        `json:"setup_session_id"`
	OnboardingPending *bool          `json:"onboarding_pending"`
	Confirm           bool           `json:"confirm"`
}

func (s *Server) handlePutSetup(w http.ResponseWriter, r *http.Request) {
	var patch setupPatch
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&patch); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	home := s.remedyHomeDir()
	setupSessionMu.Lock()
	sess := loadSetupSession(home)
	if patch.Mode != nil {
		mode := strings.ToLower(strings.TrimSpace(*patch.Mode))
		if mode == "traditional" {
			sess.Mode = "traditional"
		} else {
			sess.Mode = "conversation"
		}
	}
	if patch.Draft != nil {
		if sess.Draft == nil {
			sess.Draft = map[string]any{}
		}
		for k, v := range patch.Draft {
			if isSetupSecretKey(k) {
				// Secrets belong in the secure store via PUT /api/settings (llm_api_key).
				continue
			}
			sess.Draft[k] = v
		}
		if sess.Status == "pending" {
			sess.Status = "in_progress"
		}
	}
	if patch.Skipped != nil {
		sess.Skipped = patch.Skipped
	}
	if patch.SetupSessionID != nil {
		sess.SetupSessionID = strings.TrimSpace(*patch.SetupSessionID)
	}
	if patch.OnboardingPending != nil {
		sess.OnboardingPending = *patch.OnboardingPending
	}
	action := strings.ToLower(strings.TrimSpace(patch.Action))
	var actionErr error
	switch action {
	case "skip_all":
		actionErr = s.finalizeSetupSession(&sess, "skipped")
	case "confirm":
		actionErr = s.finalizeSetupSession(&sess, "complete")
	case "begin":
		sess.OnboardingPending = true
		if sess.Status == "" || sess.Status == "pending" {
			sess.Status = "pending"
		}
	case "update_offer_go", "update_offer_accept":
		if sess.UpdateOffer != "accepted" && sess.UpdateOffer != "declined" {
			s.recordUpdateOffer(&sess, "accepted")
			// Start the same conversation as a revisit; the pop-up itself is done.
			sess.OnboardingPending = true
			sess.Mode = "conversation"
			sess.Status = "pending"
			sess.Source = "revisit"
		}
	case "update_offer_later", "update_offer_dismiss":
		if sess.UpdateOffer != "accepted" && sess.UpdateOffer != "declined" {
			s.recordUpdateOffer(&sess, "declined")
		}
	}
	var saveErr error
	if actionErr == nil {
		saveErr = saveSetupSession(home, sess)
	}
	setupSessionMu.Unlock()
	if actionErr != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": actionErr.Error()})
		return
	}
	if saveErr != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": "failed to save setup session"})
		return
	}
	writeJSON(w, http.StatusOK, s.setupSnapshot())
}

func (s *Server) finalizeSetupSession(sess *setupSession, status string) error {
	patch := map[string]any{"setup_completed": true}
	advanced := map[string]any{}
	rootKeys := map[string]struct{}{
		"name": {}, "user_name": {}, "agent_gender": {}, "ui_language": {},
		"persona": {}, "sarcasm_mode": {}, "llm_provider": {}, "llm_model": {},
		"llm_base_url": {}, "custom_llm_name": {}, "project_path": {},
		"approval_mode": {}, "trust_profile": {}, "privacy_mode": {},
		"vision_force_decode": {}, "launch_at_login": {}, "update_check": {},
	}
	for k, v := range sess.Draft {
		if isSetupSecretKey(k) {
			continue
		}
		if _, ok := rootKeys[k]; ok {
			patch[k] = v
		} else {
			advanced[k] = v
		}
	}
	if len(advanced) > 0 {
		patch["advanced"] = advanced
	}
	if _, err := s.applySettingsUpdate(patch); err != nil {
		return err
	}
	if len(advanced) > 0 {
		// Re-apply interop / muscle / house-rules advanced knobs so a
		// partial settings patch cannot drop them from [advanced].
		s.mergeForwardAdvanced(advanced)
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	sess.Status = status
	sess.CompletedAt = now
	sess.OnboardingPending = false
	sess.Source = status
	s.recordUpdateOffer(sess, "accepted")
	return nil
}

func (s *Server) handleSetupMindRetry(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	out, err := s.vision.Install(r.Context(), home, visionDefaultModelID, "", false)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	setupSessionMu.Lock()
	sess := loadSetupSession(s.remedyHomeDir())
	sess.MindFailure = nil
	sess.FallbackSuggested = false
	_ = saveSetupSession(s.remedyHomeDir(), sess)
	setupSessionMu.Unlock()
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) recordUpdateOffer(sess *setupSession, choice string) {
	if sess == nil {
		return
	}
	sess.UpdateOffer = choice
	sess.UpdateOfferID = updateOfferID
	sess.UpdateOfferAt = time.Now().UTC().Format("2006-01-02T15:04:05Z")
}

func (s *Server) updateOfferAvailable(sess setupSession) bool {
	cfg := LoadConfig(s.homeDir)
	if !coerceBool(cfg["setup_completed"], false) {
		return false
	}
	if adv, ok := cfg["advanced"].(map[string]any); ok {
		// onboarding defaults to true when unset.
		if v, exists := adv["onboarding"]; exists && !coerceBool(v, true) {
			return false
		}
	}
	if sess.UpdateOffer == "accepted" || sess.UpdateOffer == "declined" {
		return false
	}
	if sess.Status == "complete" || sess.Status == "skipped" {
		return false
	}
	if sess.OnboardingPending && sess.Source != "revisit" {
		return false
	}
	return true
}

func (s *Server) updateOfferPayload(sess setupSession) map[string]any {
	return map[string]any{
		"available": s.updateOfferAvailable(sess),
		"id":        updateOfferID,
		"title":     updateOfferTitle,
		"body":      updateOfferBody,
		"primary":   updateOfferPrimary,
		"secondary": updateOfferSecondary,
	}
}

func setupQuestionCatalog() []map[string]any {
	return []map[string]any{
		{"id": "names", "title": "Names", "prompt": "What should I call you, and what should you call me?", "group": "setup", "skippable": true},
		{"id": "language", "title": "Language", "prompt": "Which language should I use? The default is this computer's language.", "group": "setup", "skippable": true},
		{"id": "tone", "title": "Tone", "prompt": "How should I talk — balanced, concise, detailed, or playful?", "group": "setup", "skippable": true},
		{"id": "provider", "title": "Provider", "prompt": "If you want a cloud model as muscle, which provider should I use? You can skip and add a key later.", "group": "setup", "skippable": true},
		{"id": "workspace", "title": "Workspace", "prompt": "Is there a default project folder I should use? You can leave this empty.", "group": "setup", "skippable": true},
		{"id": "proactive", "title": "How proactive", "prompt": "Should I reach out between visits? Reaching out stays off unless you say yes.", "group": "house_rules", "skippable": true},
		{"id": "privacy", "title": "Privacy and audit", "prompt": "Should I keep a visible audit of inner work, and turn on privacy mode?", "group": "house_rules", "skippable": true},
		{"id": "tokens", "title": "Token saving", "prompt": "I can handle easy chats and pictures on this PC and send the muscle a short description instead of the raw image.", "group": "house_rules", "skippable": true},
		{"id": "gpu", "title": "Local muscle and the GPU", "prompt": "If a local muscle is running, how should we share the GPU? Auto is fine for most machines.", "group": "house_rules", "skippable": true},
		{"id": "consent", "title": "Consent", "prompt": "I will always ask before spending money, sending messages, or deleting things. Should I also ask before other tools run?", "group": "house_rules", "skippable": true},
		{"id": "interop", "title": "Other bots", "prompt": "Should other bots be able to talk to me, and should I answer them automatically? Both stay off until you turn them on.", "group": "house_rules", "skippable": true},
		{"id": "updates", "title": "Updates", "prompt": "Updates happen in the app. Should I check for them automatically and let you know, or only when you ask?", "group": "setup", "skippable": true},
		{"id": "launch", "title": "Start with the computer", "prompt": "Should I start with this computer so I'm ready when you are?", "group": "setup", "skippable": true},
	}
}

func (s *Server) mergeForwardAdvanced(extra map[string]any) {
	if s == nil || len(extra) == 0 {
		return
	}
	InvalidateConfigCache()
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
	adv, _ := cfg["advanced"].(map[string]any)
	if adv == nil {
		adv = map[string]any{}
	}
	for k, v := range extra {
		adv[k] = v
	}
	cfg["advanced"] = adv
	_ = WriteConfig(path, cfg)
	InvalidateConfigCache()
}
