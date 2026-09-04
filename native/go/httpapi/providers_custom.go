package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const userProviderPrefix = "custom-"

var slugRE = regexp.MustCompile(`[^a-z0-9]+`)

type customProviderRequest struct {
	Name        string  `json:"name"`
	BaseURL     string  `json:"base_url"`
	APIKey      string  `json:"api_key"`
	Flavour     string  `json:"flavour"`
	RequiresKey *bool   `json:"requires_key"`
	ID          string  `json:"id"`
}

type providerProbeRequest struct {
	Provider string `json:"provider"`
	APIKey   string `json:"api_key"`
	BaseURL  string `json:"base_url"`
}

func isUserProvider(pid string, cfg ConfigMap) bool {
	p := strings.ToLower(strings.TrimSpace(pid))
	if strings.HasPrefix(p, userProviderPrefix) {
		return true
	}
	meta, ok := mergedProviderCatalog(cfg)[p]
	return ok && meta.UserDefined
}

func slugifyProviderName(name string) string {
	s := strings.Trim(slugRE.ReplaceAllString(strings.ToLower(strings.TrimSpace(name)), "-"), "-")
	if len(s) > 40 {
		s = s[:40]
	}
	if s == "" {
		return "endpoint"
	}
	return s
}

func providerIDForName(name string, existing map[string]struct{}, catalog map[string]providerMeta) string {
	base := userProviderPrefix + slugifyProviderName(name)
	taken := map[string]struct{}{}
	for k := range existing {
		taken[k] = struct{}{}
	}
	for k := range catalog {
		taken[k] = struct{}{}
	}
	pid := base
	n := 2
	for {
		_, hit := taken[pid]
		if !hit {
			return pid
		}
		if meta, ok := catalog[pid]; ok && meta.UserDefined {
			return pid
		}
		pid = base + "-" + strconv.Itoa(n)
		n++
	}
}

func normalizeCustomSpec(label, baseURL, flavour, auth string) map[string]any {
	flavour = strings.ToLower(strings.TrimSpace(flavour))
	switch flavour {
	case "openai", "anthropic", "ollama", "gemini":
	default:
		flavour = "openai"
	}
	auth = strings.ToLower(strings.TrimSpace(auth))
	if auth != "none" {
		auth = "api_key"
	}
	return map[string]any{
		"label":    strings.TrimSpace(label),
		"base_url": strings.TrimRight(strings.TrimSpace(baseURL), "/"),
		"flavour":  flavour,
		"auth":     auth,
	}
}

func specsFromConfig(cfg ConfigMap) map[string]map[string]any {
	out := map[string]map[string]any{}
	raw, ok := asStringMap(cfg["custom_providers"])
	if !ok || raw == nil {
		return out
	}
	for pid, specAny := range raw {
		p := strings.ToLower(strings.TrimSpace(pid))
		if !strings.HasPrefix(p, userProviderPrefix) {
			continue
		}
		spec, ok := asStringMap(specAny)
		if !ok {
			continue
		}
		norm := normalizeCustomSpec(
			fmtString(spec["label"]),
			fmtString(spec["base_url"]),
			fmtString(spec["flavour"]),
			fmtString(spec["auth"]),
		)
		if fmtString(norm["base_url"]) == "" {
			continue
		}
		if fmtString(norm["label"]) == "" {
			norm["label"] = strings.TrimPrefix(p, userProviderPrefix)
		}
		out[p] = norm
	}
	return out
}

func upsertCustomSpec(cfg ConfigMap, name, baseURL, flavour, auth, pid string) (ConfigMap, string, error) {
	cfg = cloneConfig(cfg)
	table := map[string]any{}
	if existing, ok := asStringMap(cfg["custom_providers"]); ok {
		for k, v := range existing {
			table[k] = v
		}
	}
	existingIDs := map[string]struct{}{}
	for k := range table {
		existingIDs[strings.ToLower(k)] = struct{}{}
	}
	pid = strings.ToLower(strings.TrimSpace(pid))
	if pid == "" {
		pid = providerIDForName(name, existingIDs, mergedProviderCatalog(cfg))
	}
	spec := normalizeCustomSpec(name, baseURL, flavour, auth)
	if fmtString(spec["base_url"]) == "" {
		return nil, "", errDetail("base_url is required")
	}
	if fmtString(spec["label"]) == "" {
		return nil, "", errDetail("name is required")
	}
	table[pid] = spec
	cfg["custom_providers"] = table
	return cfg, pid, nil
}

func removeCustomSpec(cfg ConfigMap, pid string) ConfigMap {
	cfg = cloneConfig(cfg)
	table := map[string]any{}
	if existing, ok := asStringMap(cfg["custom_providers"]); ok {
		for k, v := range existing {
			table[k] = v
		}
	}
	delete(table, strings.ToLower(strings.TrimSpace(pid)))
	cfg["custom_providers"] = table
	return cfg
}

type detailError string

func (e detailError) Error() string { return string(e) }

func errDetail(msg string) error { return detailError(msg) }

func (s *Server) handleSaveCustomProvider(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid body"})
		return
	}
	var req customProviderRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	name := strings.TrimSpace(req.Name)
	base := strings.TrimRight(strings.TrimSpace(req.BaseURL), "/")
	key := strings.TrimSpace(req.APIKey)
	if name == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "name required"})
		return
	}
	if base == "" || !strings.Contains(base, "://") {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "base_url must be a full URL"})
		return
	}
	pid := strings.ToLower(strings.TrimSpace(req.ID))
	if pid != "" && !strings.HasPrefix(pid, userProviderPrefix) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "not a saved custom endpoint"})
		return
	}

	flavour := strings.ToLower(strings.TrimSpace(req.Flavour))
	var probeNote any
	disc := discoverModels(base, key, "custom", 6*time.Second)
	if disc.OK {
		detected := disc.Flavour
		switch detected {
		case "lmstudio", "llamacpp":
			detected = "openai"
		}
		if flavour == "" {
			flavour = detected
		}
	} else if disc.Error != "" {
		probeNote = disc.Error
	}
	if flavour == "" {
		flavour = "openai"
	}

	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	var requiresKey bool
	if req.RequiresKey != nil {
		requiresKey = *req.RequiresKey
	} else if key != "" {
		requiresKey = true
	} else if pid != "" {
		prev := specsFromConfig(cfg)[pid]
		requiresKey = prev != nil && fmtString(prev["auth"]) == "api_key"
	} else {
		requiresKey = false
	}
	auth := "none"
	if requiresKey {
		auth = "api_key"
	}

	cfg, pid, err = upsertCustomSpec(cfg, name, base, flavour, auth, pid)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	if key != "" {
		if err := secret.SetProviderSecret(home, pid, key); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
			return
		}
	}
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
	}
	if err := WriteConfig(path, cfg); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}

	var entry any
	for _, p := range publicProviderCatalog(cfg) {
		if fmtString(p["id"]) == pid {
			entry = p
			break
		}
	}
	models := make([]map[string]any, 0)
	for _, m := range disc.Models {
		if !boolOr(m["chat"], true) {
			continue
		}
		id := fmtString(m["id"])
		if id == "" {
			continue
		}
		models = append(models, map[string]any{
			"id":   id,
			"name": firstNonEmpty(fmtString(m["name"]), id),
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":        true,
		"id":        pid,
		"provider":  entry,
		"discovery": disc.asDict(),
		"models":    models,
		"note":      probeNote,
	})
}

func (s *Server) handleDeleteCustomProvider(w http.ResponseWriter, r *http.Request) {
	pid := strings.ToLower(strings.TrimSpace(r.PathValue("id")))
	cfg := LoadConfig(s.homeDir)
	if !isUserProvider(pid, cfg) {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "no such saved endpoint"})
		return
	}
	cfg = removeCustomSpec(cfg, pid)
	home := ResolveHomeDir(s.homeDir)
	_ = secret.SetProviderSecret(home, pid, "")
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
	}
	if err := WriteConfig(path, cfg); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "id": pid})
}

func inferKeyOwner(key string) string {
	k := strings.TrimSpace(key)
	if k == "" {
		return ""
	}
	low := strings.ToLower(k)
	if strings.HasPrefix(low, "xai-") || (strings.HasPrefix(k, "eyJ") && strings.Count(k, ".") >= 2) {
		return "xai"
	}
	if strings.HasPrefix(k, "sk-ant") {
		return "anthropic"
	}
	if strings.HasPrefix(k, "gsk_") {
		return "groq"
	}
	if strings.HasPrefix(k, "AIza") {
		return "google"
	}
	if strings.HasPrefix(k, "sk-or-") {
		return "openrouter"
	}
	if strings.HasPrefix(k, "pplx-") {
		return "perplexity"
	}
	return ""
}

func isSubscriptionOAuthToken(key string) bool {
	low := strings.ToLower(strings.TrimSpace(key))
	return strings.HasPrefix(low, "sk-ant-oat") || strings.HasPrefix(low, "sk-ant-sid")
}

func (s *Server) handleProbeProvider(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid body"})
		return
	}
	var req providerProbeRequest
	if err := json.Unmarshal(body, &req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	pid := strings.ToLower(strings.TrimSpace(req.Provider))
	if pid == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "provider required"})
		return
	}
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	key := strings.TrimSpace(req.APIKey)
	keyFromRequest := key != ""
	baseFromRequest := strings.TrimSpace(req.BaseURL) != ""
	if key == "" {
		key = strings.TrimSpace(resolveProviderAPIKey(cfg, pid, home))
	}
	origProvider := pid
	owner := inferKeyOwner(key)
	if owner != "" {
		pid = owner
	}
	merged := mergedProviderCatalog(cfg)
	meta := merged[pid]
	base := strings.TrimSpace(req.BaseURL)
	if owner != "" && base != "" && pid != origProvider {
		base = ""
	}
	if base == "" {
		if pid == strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))) {
			base = strings.TrimSpace(cfgString(cfg, "llm_base_url", ""))
		}
		if base == "" {
			base = meta.BaseURL
		}
	}

	if key != "" && !keyFromRequest && baseFromRequest {
		allowed := map[string]struct{}{}
		if h := urlHost(meta.BaseURL); h != "" {
			allowed[h] = struct{}{}
		}
		if pid == strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))) {
			if h := urlHost(cfgString(cfg, "llm_base_url", "")); h != "" {
				allowed[h] = struct{}{}
			}
		}
		if _, ok := allowed[urlHost(base)]; !ok {
			writeJSON(w, http.StatusOK, map[string]any{
				"ok": false, "provider": pid, "base_url": base,
				"status": nil, "latency_ms": nil, "models": 0, "model_list": []any{},
				"error": "Refused: won't send the stored API key to a custom URL " +
					"that isn't this provider's own endpoint. Paste the key " +
					"explicitly to test an arbitrary endpoint.",
			})
			return
		}
	}

	if pid == "anthropic" && key != "" && isSubscriptionOAuthToken(key) {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "provider": pid, "base_url": base,
			"status": nil, "latency_ms": nil, "models": 0, "model_list": []any{},
			"error": "That is a Claude Code / Max login token, not a Console API key. " +
				"Anthropic does not allow those tokens in Remedy. " +
				"Use a Console key (sk-ant-api…) with API credits, " +
				"or switch provider.",
		})
		return
	}

	probeResult := func(ok bool, status any, latency any, models []map[string]any, errMsg any, flavour any) map[string]any {
		rows := make([]map[string]any, 0, len(models))
		for _, m := range models {
			id := fmtString(m["id"])
			if id == "" {
				continue
			}
			rows = append(rows, map[string]any{
				"id": id, "name": firstNonEmpty(fmtString(m["name"]), id),
			})
		}
		return map[string]any{
			"ok": ok, "provider": pid, "base_url": base,
			"status": status, "latency_ms": latency,
			"models": len(rows), "model_list": rows,
			"flavour": flavour, "error": errMsg,
		}
	}

	if base == "" {
		writeJSON(w, http.StatusOK, probeResult(false, nil, nil, nil, "No base URL for this provider.", nil))
		return
	}
	keyless := containsString(meta.Auth, "none") ||
		pid == "rmb" || pid == "llamacpp" || pid == "custom" || pid == "ollama" || pid == "demo"
	if !keyless && (key == "" || isPlaceholderKey(key)) {
		writeJSON(w, http.StatusOK, probeResult(false, nil, nil, nil,
			"No API key stored. Paste a key and Test, then Save.", nil))
		return
	}
	if pid == "ollama" && !baseFromRequest {
		if env := ollamaBaseURLFromEnv(); env != "" {
			base = env
		}
	}

	t0 := time.Now()
	disc := discoverModels(base, key, pid, 6*time.Second)
	ms := float64(time.Since(t0).Milliseconds())
	// Keep one decimal like Python round(..., 1) for non-integer ms.
	ms = float64(int64(ms*10+0.5)) / 10

	if disc.OK {
		rows := make([]map[string]any, 0)
		for _, m := range disc.Models {
			if !boolOr(m["chat"], true) {
				continue
			}
			rows = append(rows, m)
		}
		if pid == "demo" {
			allowed := map[string]struct{}{}
			for _, m := range meta.Models {
				allowed[m.ID] = struct{}{}
			}
			filtered := make([]map[string]any, 0)
			for _, m := range rows {
				if _, ok := allowed[fmtString(m["id"])]; ok {
					filtered = append(filtered, m)
				}
			}
			if len(filtered) == 0 {
				for _, m := range meta.Models {
					filtered = append(filtered, map[string]any{"id": m.ID, "name": firstNonEmpty(m.Name, m.ID)})
				}
			}
			rows = filtered
		}
		var status any = 200
		if disc.Status != nil {
			status = *disc.Status
		}
		var flavour any
		if disc.Flavour != "" {
			flavour = disc.Flavour
		}
		writeJSON(w, http.StatusOK, probeResult(true, status, ms, rows, nil, flavour))
		return
	}

	errMsg := disc.Error
	if errMsg == "" {
		errMsg = "Provider did not answer."
	}
	if pid == "ollama" && disc.Status == nil {
		errMsg = "Ollama is not running on this machine."
	} else if disc.Status == nil && strings.Contains(strings.ToLower(errMsg), "timed out") {
		errMsg = "Timed out reaching the provider. Check the URL / network."
	} else if disc.Status == nil {
		errMsg = "Could not reach provider: " + errMsg
	} else if *disc.Status == 401 || *disc.Status == 403 {
		errMsg = "Provider rejected the key (HTTP " + strconv.Itoa(*disc.Status) + "): " + errMsg
	} else {
		errMsg = "Provider returned HTTP " + strconv.Itoa(*disc.Status) + ": " + errMsg
	}
	var status any
	var latency any
	if disc.Status != nil {
		status = *disc.Status
		latency = ms
	}
	var flavour any
	if disc.Flavour != "" {
		flavour = disc.Flavour
	}
	writeJSON(w, http.StatusOK, probeResult(false, status, latency, nil, errMsg, flavour))
}
