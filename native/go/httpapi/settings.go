package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// SETTABLE_KEYS mirrors Python settings_apply.SETTABLE_KEYS.
var settableKeys = map[string]struct{}{
	"llm_provider": {}, "llm_model": {}, "llm_base_url": {}, "custom_llm_name": {},
	"llm_api_key": {}, "project_path": {}, "name": {}, "user_name": {},
	"agent_gender": {}, "ui_language": {}, "persona": {}, "setup_completed": {},
	"access_scope": {}, "launch_at_login": {}, "start_in_tray": {}, "close_to_tray": {},
	"harness_mode": {}, "harness_min_context_pct": {}, "harness_max_context_pct": {},
	"thinking_level": {}, "approval_mode": {}, "trust_profile": {},
	"show_tool_calls": {}, "tool_process": {},
	"vision_enabled": {}, "vision_model_id": {}, "vision_force_decode": {},
	"web_tools_enabled": {}, "http_bootstrap": {}, "privacy_mode": {},
	"sleev_enabled": {}, "sleev_gateway_url": {}, "sleev_allow_remote_gateway": {},
	"soul_field_enabled": {}, "build_os_advanced": {}, "rmb_enabled": {},
	"retention_session_days": {}, "retention_attachment_days": {},
	"retention_computer_shot_days": {}, "retention_undo_days": {}, "retention_log_days": {},
	"memory_encrypt": {}, "allow_skill_creation": {}, "auto_approve_threshold": {},
	"log_level": {}, "sarcasm_mode": {}, "claimidx_public_ledger": {},
	"enabled_providers": {}, "enabled_models": {}, "last_model_by_provider": {},
	"skills_active_budget": {}, "browser_home_url": {}, "enabled_channels": {},
	"messengers": {}, "assistant": {},
	"connect_enabled": {}, "connect_bind_host": {}, "connect_bind_port": {},
	"connect_paused": {}, "connect_panes": {}, "connect_relay_url": {},
	"connect_rdv_enabled": {},
}

var knownMessengers = map[string]struct{}{
	"telegram": {}, "discord": {}, "slack": {}, "mattermost": {},
	"whatsapp": {}, "teams": {}, "matrix": {}, "google_chat": {}, "signal": {},
}

var messengerSecretFields = map[string]struct{}{
	"bot_token": {}, "access_token": {}, "app_token": {}, "app_password": {},
	"app_secret": {}, "verify_token": {}, "signing_secret": {},
}

const defaultBrowserHomeURL = "https://github.com/AhmiDarrow/RemedyAI"
const defaultVisionModelID = "smolvlm2-2.2b"

var settingsApplyMu sync.Mutex

func (s *Server) handleGetSettings(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.settingsPayload())
}

func (s *Server) handlePutSettings(w http.ResponseWriter, r *http.Request) {
	var updates map[string]any
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&updates); err != nil {
		// Empty body is a no-op success (Settings Save sometimes sends {}).
		if errors.Is(err, io.EOF) {
			path := FindConfigPath(s.homeDir)
			if path == "" {
				path = DefaultConfigPath(s.homeDir)
			}
			writeJSON(w, http.StatusOK, map[string]any{
				"status":      "saved",
				"changes":     []string{},
				"config_path": path,
			})
			return
		}
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	if len(updates) == 0 {
		path := FindConfigPath(s.homeDir)
		if path == "" {
			path = DefaultConfigPath(s.homeDir)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"status":      "saved",
			"changes":     []string{},
			"config_path": path,
		})
		return
	}
	result, err := s.applySettingsUpdate(updates)
	if err != nil {
		if _, ok := err.(settingsValueError); ok {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{
			"detail": "Failed to save settings: " + err.Error(),
		})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

type settingsValueError struct{ msg string }

func (e settingsValueError) Error() string { return e.msg }

func (s *Server) applySettingsUpdate(updates map[string]any) (map[string]any, error) {
	settingsApplyMu.Lock()
	defer settingsApplyMu.Unlock()

	clean := make(map[string]any)
	for k, v := range updates {
		if _, ok := settableKeys[k]; !ok {
			continue
		}
		if v == nil && k != "enabled_providers" {
			continue
		}
		clean[k] = v
	}
	if len(clean) == 0 {
		return nil, settingsValueError{msg: "no recognized settings keys in patch"}
	}

	InvalidateConfigCache()
	home := ResolveHomeDir(s.homeDir)
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
		if path == "" {
			return nil, fmt.Errorf("cannot resolve config path")
		}
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			return nil, err
		}
	}
	cfg := LoadConfig(s.homeDir)
	if cfg == nil {
		cfg = ConfigMap{}
	}
	prevProvider := strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", "")))
	patch := cloneConfig(ConfigMap(clean))

	if rawKey, ok := patch["llm_api_key"]; ok {
		if strings.TrimSpace(fmt.Sprint(rawKey)) == "" {
			delete(patch, "llm_api_key")
		}
	}

	llmTouched := hasAnyKey(clean, "llm_provider", "llm_model", "llm_base_url", "llm_api_key")
	var provider, model, baseURL string
	if llmTouched {
		mergedProv := cfgString(patch, "llm_provider", cfgString(cfg, "llm_provider", "openai"))
		mergedModel := cfgString(patch, "llm_model", cfgString(cfg, "llm_model", ""))
		mergedURL := cfgString(patch, "llm_base_url", cfgString(cfg, "llm_base_url", ""))
		provider, model, baseURL = normalizeLLMSettings(mergedProv, mergedModel, mergedURL)
		// Preserve flexible-provider models as stored when client sent them.
		rawProv := strings.ToLower(strings.TrimSpace(mergedProv))
		if rawProv == "ollama" || rawProv == "custom" || rawProv == "openrouter" {
			provider = rawProv
			if strings.TrimSpace(mergedModel) != "" {
				model = strings.TrimSpace(mergedModel)
			}
			if strings.TrimSpace(mergedURL) != "" {
				baseURL = strings.TrimSpace(mergedURL)
			}
		}
		patch["llm_provider"] = provider
		patch["llm_model"] = model
		patch["llm_base_url"] = baseURL
		if prevProvider != "" && prevProvider != provider {
			patch["last_llm_provider"] = prevProvider
		}
	} else {
		provider = prevProvider
		if provider == "" {
			provider = "openai"
		}
		model = cfgString(cfg, "llm_model", "")
		baseURL = cfgString(cfg, "llm_base_url", "")
	}

	normalizeSettingsPatch(patch, cfg)

	messengersUpdate, _ := patch["messengers"].(map[string]any)
	delete(patch, "messengers")
	assistantUpdate, _ := patch["assistant"].(map[string]any)
	delete(patch, "assistant")

	visionEnabled, hasVE := patch["vision_enabled"]
	visionModelID, hasVM := patch["vision_model_id"]
	visionForce, hasVF := patch["vision_force_decode"]
	delete(patch, "vision_enabled")
	delete(patch, "vision_model_id")
	delete(patch, "vision_force_decode")

	incomingKey, hasKey := patch["llm_api_key"]
	delete(patch, "llm_api_key")

	for k, v := range patch {
		cfg[k] = v
	}

	if hasVE || hasVM || hasVF {
		visionTbl, _ := asStringMap(cfg["vision"])
		if visionTbl == nil {
			visionTbl = map[string]any{}
		}
		if hasVE {
			visionTbl["enabled"] = coerceBool(visionEnabled, false)
		}
		if hasVM {
			if mid := strings.TrimSpace(fmt.Sprint(visionModelID)); mid != "" {
				visionTbl["model_id"] = mid
			}
		}
		if hasVF {
			visionTbl["force_decode"] = coerceBool(visionForce, false)
		}
		cfg["vision"] = visionTbl
	}

	if hasKey {
		key := strings.TrimSpace(fmt.Sprint(incomingKey))
		if key != "" {
			if err := secret.SetProviderSecret(home, provider, key); err != nil {
				return nil, err
			}
		}
	}

	if messengersUpdate != nil {
		applyMessengersUpdate(cfg, messengersUpdate, home)
	}
	if assistantUpdate != nil {
		applyAssistantUpdate(cfg, assistantUpdate)
	}

	cfg = scrubConfigSecrets(cfg)
	cfg["llm_api_key"] = ""
	delete(cfg, "provider_keys")
	if err := WriteConfig(path, cfg); err != nil {
		return nil, err
	}
	s.refreshConnectAfterSettings()

	changes := make([]string, 0, len(patch)+3)
	for k := range patch {
		changes = append(changes, k)
	}
	if hasVE {
		changes = append(changes, "vision_enabled")
	}
	if hasVM {
		changes = append(changes, "vision_model_id")
	}
	if hasVF {
		changes = append(changes, "vision_force_decode")
	}
	if hasKey {
		changes = append(changes, "llm_api_key")
	}
	if messengersUpdate != nil {
		changes = append(changes, "messengers")
	}
	if assistantUpdate != nil {
		changes = append(changes, "assistant")
	}

	visionTbl, _ := asStringMap(cfg["vision"])
	return map[string]any{
		"status":              "saved",
		"changes":             changes,
		"config_path":         path,
		"llm_provider":        cfgString(cfg, "llm_provider", provider),
		"llm_model":           cfgString(cfg, "llm_model", model),
		"llm_base_url":        cfgString(cfg, "llm_base_url", baseURL),
		"persona":             cfgString(cfg, "persona", "default"),
		"project_path":        cfgString(cfg, "project_path", ""),
		"access_scope":        cfgString(cfg, "access_scope", "project"),
		"launch_at_login":     cfgBool(cfg, "launch_at_login", false),
		"start_in_tray":       cfgBool(cfg, "start_in_tray", false),
		"close_to_tray":       cfgBool(cfg, "close_to_tray", false),
		"harness_mode":        cfgString(cfg, "harness_mode", "auto"),
		"thinking_level":      strings.ToLower(cfgString(cfg, "thinking_level", "high")),
		"approval_mode":       strings.ToLower(cfgString(cfg, "approval_mode", "auto")),
		"trust_profile":       normalizeTrustProfile(cfg["trust_profile"]),
		"user_name":           strings.TrimSpace(cfgString(cfg, "user_name", "")),
		"tool_process":        normalizeToolProcess(cfg, nil),
		"vision_enabled":      coerceBool(visionTbl["enabled"], false),
		"vision_model_id":     cfgString(ConfigMap(visionTbl), "model_id", ""),
		"vision_force_decode": coerceBool(visionTbl["force_decode"], false),
		"name":                cfgString(cfg, "name", "Remedy"),
		"web_tools_enabled":   cfgBool(cfg, "web_tools_enabled", true),
		"http_bootstrap":      HTTPBootstrapEnabled(s.homeDir),
		"custom_llm_name":     strings.TrimSpace(cfgString(cfg, "custom_llm_name", "")),
	}, nil
}

func normalizeSettingsPatch(patch, cfg ConfigMap) {
	if v, ok := patch["custom_llm_name"]; ok && v != nil {
		name := strings.TrimSpace(fmt.Sprint(v))
		if len(name) > 80 {
			name = name[:80]
		}
		patch["custom_llm_name"] = name
	}
	if v, ok := patch["project_path"]; ok && v != nil {
		raw := strings.TrimSpace(fmt.Sprint(v))
		if raw == "." || raw == "./" {
			raw = ""
		}
		patch["project_path"] = raw
	}
	if v, ok := patch["access_scope"]; ok && v != nil {
		scope := strings.ToLower(strings.TrimSpace(fmt.Sprint(v)))
		if scope != "project" && scope != "home" && scope != "full" {
			scope = "project"
		}
		patch["access_scope"] = scope
	}
	if v, ok := patch["harness_mode"]; ok && v != nil {
		hm := strings.ToLower(strings.TrimSpace(fmt.Sprint(v)))
		if hm != "off" && hm != "manual" && hm != "auto" {
			hm = "auto"
		}
		patch["harness_mode"] = hm
	}
	if v, ok := patch["thinking_level"]; ok && v != nil {
		tl := strings.ToLower(strings.TrimSpace(fmt.Sprint(v)))
		if tl != "off" && tl != "low" && tl != "medium" && tl != "high" {
			tl = "high"
		}
		patch["thinking_level"] = tl
	}
	if v, ok := patch["approval_mode"]; ok && v != nil {
		am := strings.ToLower(strings.TrimSpace(fmt.Sprint(v)))
		if am == "confirm" || am == "manual" || am == "safe" {
			am = "ask"
		}
		if am != "ask" && am != "auto" && am != "full" {
			am = "auto"
		}
		patch["approval_mode"] = am
	}
	if v, ok := patch["trust_profile"]; ok && v != nil {
		patch["trust_profile"] = normalizeTrustProfile(v)
	}
	if v, ok := patch["tool_process"]; ok && v != nil {
		patch["tool_process"] = normalizeToolProcess(nil, v)
	} else if v, ok := patch["show_tool_calls"]; ok && v != nil {
		if coerceBool(v, false) {
			patch["tool_process"] = "full"
		} else {
			patch["tool_process"] = "off"
		}
	}
	delete(patch, "show_tool_calls")

	for _, flag := range []string{
		"web_tools_enabled", "http_bootstrap", "privacy_mode",
		"sleev_enabled", "sleev_allow_remote_gateway", "soul_field_enabled",
		"build_os_advanced", "rmb_enabled", "memory_encrypt",
		"allow_skill_creation", "sarcasm_mode", "claimidx_public_ledger",
		"setup_completed", "launch_at_login", "start_in_tray", "close_to_tray",
		"connect_enabled", "connect_paused",
	} {
		if v, ok := patch[flag]; ok && v != nil {
			patch[flag] = coerceBool(v, false)
		}
	}
	if v, ok := patch["browser_home_url"]; ok && v != nil {
		patch["browser_home_url"] = normalizeBrowserHomeURL(fmt.Sprint(v))
	}
	if v, ok := patch["agent_gender"]; ok && v != nil {
		g := strings.ToLower(strings.TrimSpace(fmt.Sprint(v)))
		if g != "female" && g != "male" && g != "neutral" {
			g = "female"
		}
		patch["agent_gender"] = g
	}
	if v, ok := patch["name"]; ok && v != nil {
		name := strings.TrimSpace(fmt.Sprint(v))
		if name == "" {
			name = "Remedy"
		}
		if len(name) > 64 {
			name = name[:64]
		}
		patch["name"] = name
	}
	if v, ok := patch["user_name"]; ok && v != nil {
		patch["user_name"] = strings.TrimSpace(fmt.Sprint(v))
	}
	if v, ok := patch["ui_language"]; ok && v != nil {
		lang := strings.TrimSpace(fmt.Sprint(v))
		if lang == "" {
			lang = "auto"
		}
		patch["ui_language"] = lang
	}
	if v, ok := patch["log_level"]; ok && v != nil {
		ll := strings.ToUpper(strings.TrimSpace(fmt.Sprint(v)))
		if ll != "DEBUG" && ll != "INFO" && ll != "WARNING" && ll != "ERROR" {
			ll = "INFO"
		}
		patch["log_level"] = ll
	}
	if v, ok := patch["skills_active_budget"]; ok && v != nil {
		b := cfgInt(ConfigMap{"v": v}, "v", 80)
		if b < 10 {
			b = 10
		}
		if b > 500 {
			b = 500
		}
		patch["skills_active_budget"] = b
	}
	if v, ok := patch["auto_approve_threshold"]; ok && v != nil {
		t := cfgFloat(ConfigMap{"v": v}, "v", 0.8)
		if t < 0 {
			t = 0
		}
		if t > 1 {
			t = 1
		}
		patch["auto_approve_threshold"] = t
	}
	for _, daysKey := range []string{
		"retention_session_days", "retention_attachment_days",
		"retention_computer_shot_days", "retention_undo_days", "retention_log_days",
	} {
		if v, ok := patch[daysKey]; ok && v != nil {
			n := cfgInt(ConfigMap{"v": v}, "v", -1)
			if n < 0 {
				delete(patch, daysKey)
				continue
			}
			if n > 3650 {
				n = 3650
			}
			patch[daysKey] = n
		}
	}
	if v, ok := patch["enabled_channels"]; ok && v != nil {
		patch["enabled_channels"] = normalizeEnabledChannels(v)
	}
	if v, ok := patch["enabled_providers"]; ok {
		if v == nil {
			delete(patch, "enabled_providers")
			delete(cfg, "enabled_providers")
		} else {
			patch["enabled_providers"] = normalizeStringList(v)
			list, _ := patch["enabled_providers"].([]string)
			hasDemo := false
			for _, p := range list {
				if p == "demo" {
					hasDemo = true
					break
				}
			}
			if !hasDemo {
				patch["enabled_providers"] = append([]string{"demo"}, list...)
			}
		}
	}
	if v, ok := patch["harness_min_context_pct"]; ok && v != nil {
		f := cfgFloat(ConfigMap{"v": v}, "v", 0.75)
		if f < 0.05 {
			f = 0.05
		}
		if f > 0.95 {
			f = 0.95
		}
		patch["harness_min_context_pct"] = f
	}
	if v, ok := patch["harness_max_context_pct"]; ok && v != nil {
		f := cfgFloat(ConfigMap{"v": v}, "v", 0.92)
		if f < 0.1 {
			f = 0.1
		}
		if f > 0.99 {
			f = 0.99
		}
		patch["harness_max_context_pct"] = f
	}
	_ = cfg // reserved for future cross-field validation
}

func applyMessengersUpdate(cfg ConfigMap, update map[string]any, home string) {
	enabled := map[string]struct{}{"cli": {}}
	if raw, ok := cfg["enabled_channels"]; ok {
		for _, ch := range normalizeEnabledChannels(raw) {
			enabled[ch] = struct{}{}
		}
	}
	for mid, body := range update {
		mid = strings.ToLower(strings.TrimSpace(mid))
		if _, ok := knownMessengers[mid]; !ok {
			continue
		}
		m, ok := body.(map[string]any)
		if !ok {
			continue
		}
		if coerceBool(m["enabled"], false) {
			enabled[mid] = struct{}{}
		} else if _, has := m["enabled"]; has {
			delete(enabled, mid)
		}
		section, _ := asStringMap(cfg[mid])
		if section == nil {
			section = map[string]any{}
		}
		for k, v := range m {
			kl := strings.ToLower(k)
			if _, isSecret := messengerSecretFields[kl]; isSecret || strings.HasSuffix(kl, "_token") || strings.HasSuffix(kl, "_password") || strings.HasSuffix(kl, "_secret") {
				val := strings.TrimSpace(fmt.Sprint(v))
				if val != "" {
					_ = secret.SetProviderSecret(home, "ch:"+mid+":"+kl, val)
				}
				delete(section, k)
				continue
			}
			if k == "enabled" {
				continue
			}
			section[k] = v
		}
		cfg[mid] = section
	}
	chs := make([]string, 0, len(enabled))
	chs = append(chs, "cli")
	for ch := range enabled {
		if ch != "cli" {
			chs = append(chs, ch)
		}
	}
	cfg["enabled_channels"] = chs
}

func applyAssistantUpdate(cfg ConfigMap, update map[string]any) {
	section, _ := asStringMap(cfg["assistant"])
	if section == nil {
		section = map[string]any{}
	}
	for _, k := range []string{
		"enabled", "timezone", "money_disclaimer_accepted",
		"privacy_ai_accepted", "account_access_accepted",
		"default_calendar_account", "default_mail_account",
	} {
		if v, ok := update[k]; ok {
			section[k] = v
		}
	}
	if brief, ok := update["brief"].(map[string]any); ok {
		section["brief"] = brief
	}
	cfg["assistant"] = section
}

func (s *Server) settingsPayload() map[string]any {
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	path := FindConfigPath(s.homeDir)
	configExists := path != ""
	defaultPath := DefaultConfigPath(s.homeDir)
	setupCompleted := !needsFirstRunSetup(cfg, path)

	rawProvider := cfgString(cfg, "llm_provider", envOr("REMEDY_LLM_PROVIDER", "openai"))
	rawModel := cfgString(cfg, "llm_model", envOr("REMEDY_LLM_MODEL", defaultModelForProvider(rawProvider)))
	rawURL := cfgString(cfg, "llm_base_url", envOr("REMEDY_LLM_BASE_URL", "https://api.openai.com/v1"))
	provider, model, baseURL := normalizeLLMSettings(rawProvider, rawModel, rawURL)
	if rp := strings.ToLower(strings.TrimSpace(rawProvider)); rp == "ollama" || rp == "custom" || rp == "openrouter" {
		provider = rp
		if strings.TrimSpace(rawModel) != "" {
			model = strings.TrimSpace(rawModel)
		}
		if strings.TrimSpace(rawURL) != "" {
			baseURL = strings.TrimSpace(rawURL)
		}
	}

	secretStatus := secret.PublicSecretStatus(home)
	keysSet, _ := secretStatus["provider_keys_set"].(map[string]bool)
	if keysSet == nil {
		keysSet = map[string]bool{}
	}
	effectiveKey := secret.GetProviderSecret(home, provider)
	keySet := effectiveKey != "" && !isPlaceholderKey(effectiveKey)

	projectPath := cfgString(cfg, "project_path", "")
	if projectPath == "" {
		if wd, err := os.Getwd(); err == nil {
			projectPath = wd
		}
	}

	gender := strings.ToLower(strings.TrimSpace(cfgString(cfg, "agent_gender", "female")))
	if gender != "female" && gender != "male" && gender != "neutral" {
		gender = "female"
	}

	visionTbl, _ := asStringMap(cfg["vision"])
	visionEnabled := coerceBool(visionTbl["enabled"], false)
	visionModelID := cfgString(ConfigMap(visionTbl), "model_id", defaultVisionModelID)
	visionForce := coerceBool(visionTbl["force_decode"], false)

	enabledChannels := normalizeEnabledChannels(cfg["enabled_channels"])
	messengers := publicMessengers(cfg, keysSet)

	out := map[string]any{
		"llm_provider":              provider,
		"llm_model":                 model,
		"llm_base_url":              baseURL,
		"custom_llm_name":           strings.TrimSpace(cfgString(cfg, "custom_llm_name", "")),
		"llm_api_key_set":           keySet,
		"provider_keys_set":         keysSet,
		"secrets_encoding":          secretStatus["encoding"],
		"secrets_encoding_warning":  secretStatus["encoding_warning"],
		"llm_ready":                 providerCredentialsReady(cfg, provider, effectiveKey) || keySet,
		"name":                      cfgString(cfg, "name", "Remedy"),
		"user_name":                 strings.TrimSpace(cfgString(cfg, "user_name", "")),
		"agent_gender":              gender,
		"ui_language":               cfgString(cfg, "ui_language", "auto"),
		"ui_languages":              []map[string]any{},
		"persona":                   cfgString(cfg, "persona", "default"),
		"project_path":              projectPath,
		"access_scope":              cfgString(cfg, "access_scope", "project"),
		"launch_at_login":           cfgBool(cfg, "launch_at_login", false),
		"start_in_tray":             cfgBool(cfg, "start_in_tray", false),
		"close_to_tray":             cfgBool(cfg, "close_to_tray", false),
		"harness_mode":              cfgString(cfg, "harness_mode", "auto"),
		"harness_min_context_pct":   cfgFloat(cfg, "harness_min_context_pct", 0.75),
		"harness_max_context_pct":   cfgFloat(cfg, "harness_max_context_pct", 0.92),
		"thinking_level":            strings.ToLower(cfgString(cfg, "thinking_level", "high")),
		"approval_mode":             strings.ToLower(cfgString(cfg, "approval_mode", "auto")),
		"trust_profile":             normalizeTrustProfile(cfg["trust_profile"]),
		"tool_process":              normalizeToolProcess(cfg, nil),
		"web_tools_enabled":         cfgBool(cfg, "web_tools_enabled", true),
		"http_bootstrap":            HTTPBootstrapEnabled(s.homeDir),
		"privacy_mode":              cfgBool(cfg, "privacy_mode", false),
		"sleev_enabled":             cfgBool(cfg, "sleev_enabled", false),
		"sleev_gateway_url":         strings.TrimSpace(cfgString(cfg, "sleev_gateway_url", "")),
		"soul_field_enabled":        cfgBool(cfg, "soul_field_enabled", true),
		"build_os_advanced":         cfgBool(cfg, "build_os_advanced", false),
		"rmb_enabled":               cfgBool(cfg, "rmb_enabled", true),
		"retention_session_days":    cfgInt(cfg, "retention_session_days", 180),
		"retention_attachment_days": cfgInt(cfg, "retention_attachment_days", 90),
		"retention_computer_shot_days": cfgInt(cfg, "retention_computer_shot_days", 14),
		"retention_undo_days":       cfgInt(cfg, "retention_undo_days", 30),
		"retention_log_days":        cfgInt(cfg, "retention_log_days", 30),
		"memory_encrypt":            cfgBool(cfg, "memory_encrypt", false),
		"allow_skill_creation":      cfgBool(cfg, "allow_skill_creation", true),
		"auto_approve_threshold":    cfgFloat(cfg, "auto_approve_threshold", 0.8),
		"log_level":                 strings.ToUpper(cfgString(cfg, "log_level", "INFO")),
		"sarcasm_mode":              cfgBool(cfg, "sarcasm_mode", false),
		"claimidx_public_ledger":    cfgBool(cfg, "claimidx_public_ledger", false),
		"enabled_providers":         cfg["enabled_providers"],
		"enabled_models":            cfgMapOrEmpty(cfg, "enabled_models"),
		"last_model_by_provider":    cfgMapOrEmpty(cfg, "last_model_by_provider"),
		"skills_active_budget":      cfgInt(cfg, "skills_active_budget", 80),
		"browser_home_url":          normalizeBrowserHomeURL(cfgString(cfg, "browser_home_url", "")),
		"version":                   s.version,
		"config_exists":             configExists,
		"setup_completed":           setupCompleted,
		"needs_setup":               !setupCompleted,
		"config_path":               firstNonEmpty(path, defaultPath),
		"enabled_channels":          enabledChannels,
		"messengers":                messengers,
		"vision_enabled":            visionEnabled,
		"vision_model_id":           visionModelID,
		"vision_force_decode":       visionForce,
		"vision": map[string]any{
			"enabled":      visionEnabled,
			"installed":    false,
			"ready":        false,
			"running":      false,
			"model_id":     visionModelID,
			"model_name":   nil,
			"force_decode": visionForce,
		},
		"assistant": publicAssistant(cfg),
		"sleev": map[string]any{
			"enabled":     cfgBool(cfg, "sleev_enabled", false),
			"installed":   false,
			"gateway_url": firstNonEmpty(strings.TrimSpace(cfgString(cfg, "sleev_gateway_url", "")), "http://127.0.0.1:17321"),
			"harness":     "remedy",
		},
	}
	return out
}

func needsFirstRunSetup(cfg ConfigMap, configPath string) bool {
	if configPath == "" {
		return true
	}
	if st, err := os.Stat(configPath); err != nil || st.IsDir() {
		return true
	}
	if len(cfg) == 0 {
		return true
	}
	if v, ok := cfg["setup_completed"]; ok {
		return !coerceBool(v, false)
	}
	// Legacy config without the flag → treat as already set up.
	return false
}

func normalizeToolProcess(cfg ConfigMap, raw any) string {
	if raw == nil && cfg != nil {
		raw = cfg["tool_process"]
		if raw == nil && cfgBool(cfg, "show_tool_calls", false) {
			return "full"
		}
	}
	s := strings.ToLower(strings.TrimSpace(fmt.Sprint(raw)))
	if s == "" || s == "<nil>" {
		s = "off"
	}
	switch s {
	case "medium", "med":
		return "medium"
	case "full", "full+", "fullplus", "full_plus", "debug", "on", "true", "1", "yes":
		return "full"
	default:
		if coerceBool(raw, false) && s != "off" && s != "false" {
			return "full"
		}
		return "off"
	}
}

func normalizeTrustProfile(raw any) string {
	s := strings.ToLower(strings.TrimSpace(fmt.Sprint(raw)))
	switch s {
	case "conservative", "balanced", "autonomous":
		return s
	default:
		return "balanced"
	}
}

func normalizeBrowserHomeURL(raw string) string {
	u := strings.TrimSpace(raw)
	if u == "" {
		return defaultBrowserHomeURL
	}
	low := strings.ToLower(u)
	if strings.HasPrefix(low, "javascript:") || strings.HasPrefix(low, "data:") ||
		strings.HasPrefix(low, "vbscript:") || strings.HasPrefix(low, "file:") ||
		strings.HasPrefix(low, "about:") {
		return defaultBrowserHomeURL
	}
	if !strings.HasPrefix(low, "http://") && !strings.HasPrefix(low, "https://") {
		u = "https://" + u
	}
	return u
}

func normalizeEnabledChannels(raw any) []string {
	list := normalizeStringList(raw)
	if len(list) == 0 {
		return []string{"cli"}
	}
	hasCLI := false
	for _, ch := range list {
		if ch == "cli" {
			hasCLI = true
			break
		}
	}
	if !hasCLI {
		list = append([]string{"cli"}, list...)
	}
	return list
}

func normalizeStringList(raw any) []string {
	switch t := raw.(type) {
	case []string:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s := strings.ToLower(strings.TrimSpace(x)); s != "" {
				out = append(out, s)
			}
		}
		return out
	case []any:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s := strings.ToLower(strings.TrimSpace(fmt.Sprint(x))); s != "" && s != "<nil>" {
				out = append(out, s)
			}
		}
		return out
	case string:
		parts := strings.Split(t, ",")
		out := make([]string, 0, len(parts))
		for _, p := range parts {
			if s := strings.ToLower(strings.TrimSpace(p)); s != "" {
				out = append(out, s)
			}
		}
		return out
	default:
		return nil
	}
}

func publicMessengers(cfg ConfigMap, keysSet map[string]bool) []map[string]any {
	out := make([]map[string]any, 0, len(knownMessengers))
	enabled := map[string]struct{}{}
	for _, ch := range normalizeEnabledChannels(cfg["enabled_channels"]) {
		enabled[ch] = struct{}{}
	}
	names := []string{
		"telegram", "discord", "slack", "mattermost",
		"whatsapp", "teams", "matrix", "google_chat", "signal",
	}
	for _, id := range names {
		_, on := enabled[id]
		tokenSet := false
		for sk := range messengerSecretFields {
			if keysSet["ch:"+id+":"+sk] {
				tokenSet = true
				break
			}
		}
		section, _ := asStringMap(cfg[id])
		if section != nil {
			for sk := range messengerSecretFields {
				if strings.TrimSpace(fmt.Sprint(section[sk])) != "" {
					tokenSet = true
				}
			}
		}
		status := "ready"
		if id == "whatsapp" || id == "teams" || id == "google_chat" || id == "signal" {
			status = "planned"
		}
		out = append(out, map[string]any{
			"id":        id,
			"name":      strings.ToUpper(id[:1]) + id[1:],
			"status":    status,
			"enabled":   on,
			"token_set": tokenSet,
			"inbound":   true,
			"outbound":  true,
		})
	}
	return out
}

func publicAssistant(cfg ConfigMap) map[string]any {
	section, _ := asStringMap(cfg["assistant"])
	out := map[string]any{
		"enabled":                    true,
		"accounts":                   []any{},
		"money_disclaimer_accepted":  false,
		"privacy_ai_accepted":        false,
		"account_access_accepted":    false,
		"providers_planned": []map[string]any{
			{"id": "google", "name": "Google", "status": "planned"},
			{"id": "microsoft", "name": "Microsoft", "status": "planned"},
			{"id": "yahoo", "name": "Yahoo", "status": "planned"},
		},
	}
	if section == nil {
		return out
	}
	if v, ok := section["enabled"]; ok {
		out["enabled"] = coerceBool(v, true)
	}
	if v, ok := section["timezone"]; ok && v != nil {
		out["timezone"] = fmt.Sprint(v)
	}
	for _, k := range []string{"money_disclaimer_accepted", "privacy_ai_accepted", "account_access_accepted"} {
		if v, ok := section[k]; ok {
			out[k] = coerceBool(v, false)
		}
	}
	if brief, ok := section["brief"].(map[string]any); ok {
		out["brief"] = brief
	}
	return out
}

func cfgMapOrEmpty(cfg ConfigMap, key string) any {
	if v, ok := cfg[key]; ok && v != nil {
		return v
	}
	return map[string]any{}
}

func hasAnyKey(m map[string]any, keys ...string) bool {
	for _, k := range keys {
		if _, ok := m[k]; ok {
			return true
		}
	}
	return false
}

func envOr(name, def string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return def
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}
