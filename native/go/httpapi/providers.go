package httpapi

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

func (s *Server) handleListProviders(w http.ResponseWriter, _ *http.Request) {
	cfg := LoadConfig(s.homeDir)
	writeJSON(w, http.StatusOK, map[string]any{
		"providers": publicProviderCatalog(cfg),
	})
}

func (s *Server) handleListFreeProviders(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"options": freeOptionsPublic(),
	})
}

func (s *Server) handleOllamaDetect(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, detectOllama("", 1500*time.Millisecond))
}

func (s *Server) handleListConnectedProviders(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.listConnectedProviders())
}

func (s *Server) handleListModels(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.listModels(
		r.URL.Query().Get("provider"),
		r.URL.Query().Get("base_url"),
		r.URL.Query().Get("api_key"),
	))
}

func (s *Server) listConnectedProviders() map[string]any {
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	catalog := publicProviderCatalog(cfg)
	secretStatus := secret.PublicSecretStatus(home)
	keysSet, _ := secretStatus["provider_keys_set"].(map[string]bool)
	if keysSet == nil {
		keysSet = map[string]bool{}
	}
	storedKeys := secret.LoadProviderKeys(home)

	ollama := detectOllama("", 1200*time.Millisecond)
	enabledRaw := cfg["enabled_providers"]
	enabledModelsCfg, _ := asStringMap(cfg["enabled_models"])
	lastBy, _ := asStringMap(cfg["last_model_by_provider"])

	classified := make([]struct {
		id        string
		meta      map[string]any
		connected bool
		reason    string
	}, 0, len(catalog))
	connectedIDs := map[string]struct{}{}
	catalogIDs := map[string]struct{}{}

	ollamaUp := ollama["available"] == true
	for _, meta := range catalog {
		pid, _ := meta["id"].(string)
		if pid == "" {
			continue
		}
		catalogIDs[pid] = struct{}{}
		connected, reason := classifyProviderConnection(pid, cfg, home, storedKeys, keysSet, ollamaUp)
		classified = append(classified, struct {
			id        string
			meta      map[string]any
			connected bool
			reason    string
		}{pid, meta, connected, reason})
		if connected {
			connectedIDs[pid] = struct{}{}
		}
	}

	enabledSet := effectiveProviderAllowlist(enabledRaw, catalogIDs, connectedIDs)

	items := make([]map[string]any, 0, len(classified))
	for _, row := range classified {
		pid, meta, connected, reason := row.id, row.meta, row.connected, row.reason
		enabled := enabledSet == nil || enabledSet[pid]
		if pid == "demo" && connected {
			enabled = true
		}
		models := cloneAnyMaps(meta["models"])
		if pid != "demo" && enabledModelsCfg != nil {
			if allowRaw, ok := enabledModelsCfg[pid]; ok {
				allow := stringSetFromAny(allowRaw)
				if len(allow) > 0 {
					filtered := make([]map[string]any, 0, len(models))
					for _, m := range models {
						if id, _ := m["id"].(string); allow[id] {
							filtered = append(filtered, m)
						}
					}
					if len(filtered) >= 2 || len(models) <= 1 {
						models = filtered
					}
				}
			}
		}
		if pid == "ollama" && ollama["available"] == true {
			if names, ok := ollama["models"].([]string); ok && len(names) > 0 {
				models = make([]map[string]any, 0, len(names))
				for _, n := range names {
					models = append(models, map[string]any{
						"id": n, "name": n, "provider": "ollama", "source": "endpoint",
					})
				}
			}
		}

		lastModel := ""
		if lastBy != nil {
			lastModel = strings.TrimSpace(fmtString(lastBy[pid]))
		}
		if lastModel == "" {
			lastModel = strings.TrimSpace(fmtString(meta["default_model"]))
		}
		if pid != "demo" && looksLikeDemoModel(lastModel) {
			if len(models) > 0 {
				lastModel = fmtString(models[0]["id"])
			} else {
				lastModel = fmtString(meta["default_model"])
			}
		}
		if len(models) > 0 && lastModel != "" {
			found := false
			for _, m := range models {
				if fmtString(m["id"]) == lastModel {
					found = true
					break
				}
			}
			if !found {
				lastModel = fmtString(models[0]["id"])
			}
		}

		item := cloneMap(meta)
		item["connected"] = connected
		item["connect_reason"] = reason
		item["enabled"] = enabled
		item["picker_eligible"] = connected && enabled
		item["models"] = models
		item["last_model"] = lastModel
		rawCatalog := providerCatalog[pid].Models
		if metaUser, ok := mergedProviderCatalog(cfg)[pid]; ok {
			rawCatalog = metaUser.Models
		}
		item["catalog_models"] = catalogModelsMaps(rawCatalog)
		items = append(items, item)
	}

	connected := make([]map[string]any, 0)
	picker := make([]map[string]any, 0)
	for _, p := range items {
		if p["connected"] == true {
			connected = append(connected, p)
		}
		if p["picker_eligible"] == true {
			picker = append(picker, p)
		}
	}
	var enabledOut any
	if enabledSet == nil {
		enabledOut = nil
	} else {
		list := make([]string, 0, len(enabledSet))
		for id := range enabledSet {
			list = append(list, id)
		}
		sortStrings(list)
		enabledOut = list
	}
	return map[string]any{
		"providers":         items,
		"connected":         connected,
		"picker":            picker,
		"active_provider":   strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))),
		"active_model":      cfgString(cfg, "llm_model", ""),
		"enabled_providers": enabledOut,
	}
}

func looksLikeDemoModel(id string) bool {
	low := strings.ToLower(strings.TrimSpace(id))
	if low == "" {
		return false
	}
	for _, x := range []string{"gemini-3.1-flash-lite", "codestral-latest", "gpt-oss:20b", "kimi-k3"} {
		if strings.Contains(low, x) {
			return true
		}
	}
	return strings.Contains(low, "(demo)") || strings.HasSuffix(low, " demo")
}

func classifyProviderConnection(
	pid string,
	cfg ConfigMap,
	home string,
	keys map[string]string,
	keysSet map[string]bool,
	ollamaAvailable bool,
) (bool, string) {
	pid = strings.ToLower(strings.TrimSpace(pid))
	if pid == "demo" {
		return true, "demo"
	}
	if pid == "ollama" {
		if ollamaAvailable {
			return true, "ollama_up"
		}
		return false, "ollama_down"
	}
	// xAI OAuth / console key live in auth/xai.json (not provider_keys.json).
	if pid == "xai" && loadXaiCredentials(home).connected() {
		return true, "oauth_or_key"
	}
	if keys[pid] != "" || keysSet[pid] {
		return true, "api_key"
	}
	merged := mergedProviderCatalog(cfg)
	if meta, ok := merged[pid]; ok && meta.UserDefined && containsString(meta.Auth, "none") {
		return true, "saved_endpoint"
	}
	if resolved := resolveProviderAPIKey(cfg, pid, home); resolved != "" && !isPlaceholderKey(resolved) {
		return true, "resolved_key"
	}
	active := strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", "")))
	if (pid == "rmb" || pid == "llamacpp" || pid == "custom") && active == pid {
		base := strings.TrimSpace(cfgString(cfg, "llm_base_url", ""))
		if base != "" && isLocalURL(base) {
			return true, "active_local"
		}
	}
	return false, "no_credentials"
}

func resolveProviderAPIKey(cfg ConfigMap, provider, home string) string {
	prov := strings.ToLower(strings.TrimSpace(provider))
	if home == "" {
		home = ResolveHomeDir("")
	}
	if k := strings.TrimSpace(secret.GetProviderSecret(home, prov)); k != "" {
		if prov == "xai" && !looksLikeXaiCredential(k) {
			k = ""
		} else {
			return k
		}
	}
	// xAI device-OAuth / console key store (auth/xai.json) — parity with
	// remedy.interfaces.config.resolve_provider_api_key → resolve_bearer.
	if prov == "xai" {
		if tok := resolveXaiBearer(home); tok != "" {
			return tok
		}
	}
	meta, ok := mergedProviderCatalog(cfg)[prov]
	if !ok {
		meta = providerCatalog[prov]
	}
	for _, envName := range meta.EnvKeys {
		if v := strings.TrimSpace(os.Getenv(envName)); v != "" {
			if prov == "xai" && !looksLikeXaiCredential(v) {
				continue
			}
			return v
		}
	}
	if prov == strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))) {
		if v := strings.TrimSpace(os.Getenv("REMEDY_LLM_API_KEY")); v != "" {
			if prov == "xai" && !looksLikeXaiCredential(v) {
				return ""
			}
			return v
		}
	}
	return ""
}

// looksLikeXaiCredential accepts console keys (xai-…) and OAuth JWTs.
func looksLikeXaiCredential(raw string) bool {
	s := strings.TrimSpace(raw)
	if s == "" || isPlaceholderKey(s) {
		return false
	}
	low := strings.ToLower(s)
	if strings.HasPrefix(low, "xai-") {
		return true
	}
	// JWT access tokens from device OAuth.
	if strings.HasPrefix(s, "eyJ") && strings.Count(s, ".") >= 2 {
		return true
	}
	// Allow other opaque bearer shapes stored via the xAI auth path.
	return len(s) >= 20
}

func effectiveProviderAllowlist(enabledRaw any, catalogIDs, connectedIDs map[string]struct{}) map[string]bool {
	var enabledSet map[string]bool
	switch t := enabledRaw.(type) {
	case string:
		if strings.TrimSpace(t) == "" {
			return nil
		}
		enabledSet = map[string]bool{}
		for _, part := range strings.Split(t, ",") {
			if s := strings.ToLower(strings.TrimSpace(part)); s != "" {
				enabledSet[s] = true
			}
		}
	case []string:
		if len(t) == 0 {
			return nil
		}
		enabledSet = map[string]bool{}
		for _, x := range t {
			if s := strings.ToLower(strings.TrimSpace(x)); s != "" {
				enabledSet[s] = true
			}
		}
	case []any:
		if len(t) == 0 {
			return nil
		}
		enabledSet = map[string]bool{}
		for _, x := range t {
			if s := strings.ToLower(strings.TrimSpace(fmtString(x))); s != "" {
				enabledSet[s] = true
			}
		}
	default:
		return nil
	}
	if len(enabledSet) == 0 {
		return nil
	}
	// Buggy Settings save: allowlist == currently connected → treat as all-on.
	onlyConnected := true
	for id := range enabledSet {
		if id == "demo" {
			continue
		}
		if _, ok := connectedIDs[id]; !ok {
			onlyConnected = false
			break
		}
	}
	if onlyConnected {
		missing := 0
		for id := range catalogIDs {
			if !enabledSet[id] {
				missing++
			}
		}
		if missing >= 2 {
			return nil
		}
	}
	return enabledSet
}

func (s *Server) listModels(reqProvider, overrideURL, overrideKey string) map[string]any {
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	activeProvider := strings.ToLower(strings.TrimSpace(firstNonEmpty(
		cfgString(cfg, "llm_provider", ""),
		os.Getenv("REMEDY_LLM_PROVIDER"),
		"openai",
	)))
	activeID := firstNonEmpty(cfgString(cfg, "llm_model", ""), os.Getenv("REMEDY_LLM_MODEL"))
	activeURL := firstNonEmpty(cfgString(cfg, "llm_base_url", ""), os.Getenv("REMEDY_LLM_BASE_URL"))

	merged := mergedProviderCatalog(cfg)
	reqProvider = strings.ToLower(strings.TrimSpace(reqProvider))
	var configuredProvider, configuredID, baseURL, apiKey string
	explicitID := false

	if reqProvider != "" {
		if meta, ok := merged[reqProvider]; ok {
			configuredProvider = reqProvider
			baseURL = meta.BaseURL
			if reqProvider == "ollama" {
				if env := ollamaBaseURLFromEnv(); env != "" {
					baseURL = env
				}
			}
			if reqProvider == activeProvider && activeURL != "" {
				baseURL = activeURL
			}
			if lastBy, ok := asStringMap(cfg["last_model_by_provider"]); ok {
				if v := strings.TrimSpace(fmtString(lastBy[reqProvider])); v != "" {
					configuredID = v
					explicitID = true
				}
			}
			if configuredID == "" && len(meta.Models) > 0 {
				configuredID = meta.Models[0].ID
			}
			apiKey = resolveProviderAPIKey(cfg, reqProvider, home)
		}
	}
	if configuredProvider == "" {
		configuredProvider = activeProvider
		configuredID = activeID
		explicitID = configuredID != ""
		baseURL = activeURL
		apiKey = resolveProviderAPIKey(cfg, configuredProvider, home)
	}

	np, nm, nu := normalizeLLMSettings(configuredProvider, configuredID, baseURL)
	flexible := map[string]struct{}{
		"openrouter": {}, "custom": {}, "ollama": {}, "poe": {}, "rmb": {}, "llamacpp": {},
	}
	if _, ok := flexible[configuredProvider]; !ok {
		configuredProvider, configuredID, baseURL = np, nm, nu
	} else if baseURL == "" {
		baseURL = nu
	}
	if configuredID == "" {
		configuredID = nm
	}

	meta := merged[configuredProvider]
	if meta.Label == "" {
		meta = providerCatalog["custom"]
	}
	catalog := catalogModelsForProviderMerged(configuredProvider, merged)

	overrideURL = strings.TrimSpace(overrideURL)
	overrideKey = strings.TrimSpace(overrideKey)
	if overrideURL != "" {
		if strings.TrimSpace(apiKey) != "" && overrideKey == "" {
			allowed := map[string]struct{}{}
			if h := urlHost(baseURL); h != "" {
				allowed[h] = struct{}{}
			}
			if h := urlHost(meta.BaseURL); h != "" {
				allowed[h] = struct{}{}
			}
			if configuredProvider == activeProvider {
				if h := urlHost(cfgString(cfg, "llm_base_url", "")); h != "" {
					allowed[h] = struct{}{}
				}
			}
			if _, ok := allowed[urlHost(overrideURL)]; !ok {
				return map[string]any{
					"provider": configuredProvider,
					"models":   []any{},
					"error": "Refused: won't send the stored API key to a custom URL " +
						"that isn't this provider's own endpoint. Paste the key " +
						"explicitly to list models on an arbitrary endpoint.",
				}
			}
		}
		baseURL = overrideURL
	}
	if overrideKey != "" {
		apiKey = overrideKey
	}

	liveEnv := strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_LIVE_MODELS")))
	liveOff := liveEnv == "0" || liveEnv == "false" || liveEnv == "no" || liveEnv == "off"
	liveForce := liveEnv == "1" || liveEnv == "true" || liveEnv == "yes" || liveEnv == "on"
	keyless := containsString(meta.Auth, "none") ||
		configuredProvider == "ollama" || configuredProvider == "rmb" ||
		configuredProvider == "llamacpp" || configuredProvider == "demo"
	realKey := apiKey != "" && !isPlaceholderKey(apiKey)
	canLive := baseURL != "" && (liveForce || keyless || isLocalURL(baseURL) || realKey)

	disc := discoveryResult{Attempted: false, URL: baseURL, Error: "no key"}
	if baseURL == "" {
		disc.Error = "no base URL"
	}
	if !liveOff && canLive {
		disc = discoverModelsCached(baseURL, apiKey, configuredProvider)
	}

	mergedRows := make([]map[string]any, 0)
	seen := map[string]struct{}{}
	if configuredProvider == "demo" {
		liveIDs := map[string]struct{}{}
		if disc.OK {
			for _, r := range disc.Models {
				liveIDs[fmtString(r["id"])] = struct{}{}
			}
		}
		for _, c := range catalog {
			mid := fmtString(c["id"])
			if mid == "" {
				continue
			}
			if disc.OK && len(liveIDs) > 0 {
				if _, ok := liveIDs[mid]; !ok {
					continue
				}
			}
			seen[mid] = struct{}{}
			row := cloneMap(c)
			row["default"] = false
			if disc.OK {
				row["source"] = "endpoint"
			} else {
				row["source"] = "catalog"
			}
			mergedRows = append(mergedRows, row)
		}
		if len(mergedRows) == 0 {
			for _, c := range catalog {
				row := cloneMap(c)
				row["default"] = false
				row["source"] = "catalog"
				mergedRows = append(mergedRows, row)
			}
		}
	} else {
		if disc.OK {
			for _, m := range disc.Models {
				mid := fmtString(m["id"])
				if mid == "" || !boolOr(m["chat"], true) {
					continue
				}
				if _, ok := seen[mid]; ok {
					continue
				}
				seen[mid] = struct{}{}
				name := mid
				for _, c := range catalog {
					if fmtString(c["id"]) == mid {
						name = firstNonEmpty(fmtString(c["name"]), mid)
						break
					}
				}
				if n := fmtString(m["name"]); n != "" && name == mid {
					name = n
				}
				row := map[string]any{
					"id": mid, "name": name, "provider": configuredProvider,
					"default": false, "source": "endpoint",
				}
				if ctx := m["context_window"]; ctx != nil {
					row["context_window"] = ctx
				}
				mergedRows = append(mergedRows, row)
			}
		}
		if len(mergedRows) == 0 {
			for _, c := range catalog {
				mid := fmtString(c["id"])
				if mid == "" {
					continue
				}
				seen[mid] = struct{}{}
				row := cloneMap(c)
				row["default"] = false
				row["source"] = "catalog"
				mergedRows = append(mergedRows, row)
			}
		}
	}

	if len(mergedRows) == 0 {
		id := firstNonEmpty(configuredID, "default")
		mergedRows = []map[string]any{{
			"id": id, "name": id, "provider": configuredProvider,
			"default": true, "source": "config",
		}}
	}

	ids := map[string]struct{}{}
	for _, m := range mergedRows {
		ids[fmtString(m["id"])] = struct{}{}
	}
	if configuredID != "" {
		if _, ok := ids[configuredID]; !ok {
			_, flex := flexible[configuredProvider]
			keep := explicitID && (!disc.OK || flex)
			if configuredProvider == "demo" {
				keep = explicitID && !disc.OK && demoModelAllowed(configuredID, catalog)
			}
			if keep {
				mergedRows = append([]map[string]any{{
					"id": configuredID, "name": configuredID, "provider": configuredProvider,
					"default": true, "source": "config",
				}}, mergedRows...)
				ids[configuredID] = struct{}{}
			}
		}
	}

	preferred := []string{configuredID}
	if lastBy, ok := asStringMap(cfg["last_model_by_provider"]); ok {
		preferred = append(preferred, strings.TrimSpace(fmtString(lastBy[configuredProvider])))
	}
	if len(catalog) > 0 {
		preferred = append(preferred, fmtString(catalog[0]["id"]))
	}
	defaultID := chooseDefaultModel(mergedRows, preferred, disc.Loaded)
	if defaultID == "" {
		defaultID = "default"
	}
	for _, m := range mergedRows {
		m["default"] = fmtString(m["id"]) == defaultID
		delete(m, "chat")
	}

	return map[string]any{
		"models":    mergedRows,
		"default":   defaultID,
		"provider":  configuredProvider,
		"base_url":  baseURL,
		"discovery": disc.asDict(),
		"loaded":    disc.Loaded,
	}
}

func catalogModelsForProviderMerged(provider string, merged map[string]providerMeta) []map[string]any {
	prov := strings.ToLower(strings.TrimSpace(provider))
	meta, ok := merged[prov]
	if !ok {
		return catalogModelsForProvider(prov)
	}
	out := make([]map[string]any, 0, len(meta.Models))
	for _, m := range meta.Models {
		out = append(out, map[string]any{
			"id": m.ID, "name": firstNonEmpty(m.Name, m.ID),
			"provider": prov, "default": false,
		})
	}
	return out
}

func demoModelAllowed(mid string, catalog []map[string]any) bool {
	for _, c := range catalog {
		if fmtString(c["id"]) == mid {
			return true
		}
	}
	return false
}

func chooseDefaultModel(rows []map[string]any, preferred []string, loaded []string) string {
	ids := map[string]struct{}{}
	for _, r := range rows {
		ids[fmtString(r["id"])] = struct{}{}
	}
	for _, p := range preferred {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if _, ok := ids[p]; ok {
			return p
		}
	}
	for _, p := range loaded {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		if _, ok := ids[p]; ok {
			return p
		}
	}
	if len(rows) > 0 {
		return fmtString(rows[0]["id"])
	}
	return ""
}

func urlHost(raw string) string {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Hostname() == "" {
		return ""
	}
	return strings.ToLower(u.Hostname())
}

func boolOr(v any, def bool) bool {
	if v == nil {
		return def
	}
	b, ok := v.(bool)
	if !ok {
		return def
	}
	return b
}

func cloneMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func cloneAnyMaps(v any) []map[string]any {
	switch t := v.(type) {
	case []map[string]any:
		out := make([]map[string]any, 0, len(t))
		for _, m := range t {
			out = append(out, cloneMap(m))
		}
		return out
	case []any:
		out := make([]map[string]any, 0, len(t))
		for _, item := range t {
			if m, ok := asStringMap(item); ok {
				out = append(out, cloneMap(m))
			}
		}
		return out
	default:
		return nil
	}
}

func stringSetFromAny(v any) map[string]bool {
	out := map[string]bool{}
	switch t := v.(type) {
	case []string:
		for _, x := range t {
			if s := strings.TrimSpace(x); s != "" {
				out[s] = true
			}
		}
	case []any:
		for _, x := range t {
			if s := strings.TrimSpace(fmtString(x)); s != "" {
				out[s] = true
			}
		}
	}
	return out
}

// --- discovery -------------------------------------------------------------

type discoveryResult struct {
	Attempted bool
	OK        bool
	Status    *int
	Error     string
	URL       string
	Cached    bool
	Flavour   string
	Models    []map[string]any
	Loaded    []string
}

func (d discoveryResult) asDict() map[string]any {
	var status any
	if d.Status != nil {
		status = *d.Status
	}
	var err any
	if d.Error != "" {
		err = d.Error
	}
	var flavour any
	if d.Flavour != "" {
		flavour = d.Flavour
	}
	return map[string]any{
		"attempted": d.Attempted,
		"ok":        d.OK,
		"status":    status,
		"error":     err,
		"url":       d.URL,
		"cached":    d.Cached,
		"flavour":   flavour,
	}
}

type discoveryCacheEntry struct {
	at   time.Time
	disc discoveryResult
}

var (
	discoveryMu    sync.Mutex
	discoveryCache = map[string]discoveryCacheEntry{}
)

func discoverModelsCached(baseURL, apiKey, providerHint string) discoveryResult {
	keySig := "-"
	if apiKey != "" {
		tail := apiKey
		if len(tail) > 4 {
			tail = tail[len(tail)-4:]
		}
		keySig = fmtString(len(apiKey)) + ":" + tail
	}
	cacheKey := providerHint + "|" + baseURL + "|" + keySig
	now := time.Now()
	discoveryMu.Lock()
	if ent, ok := discoveryCache[cacheKey]; ok {
		ttl := 12 * time.Second
		if ent.disc.OK {
			ttl = 180 * time.Second
		}
		if now.Sub(ent.at) < ttl {
			cp := ent.disc
			cp.Cached = true
			cp.Models = cloneAnyMaps(ent.disc.Models)
			cp.Loaded = append([]string{}, ent.disc.Loaded...)
			discoveryMu.Unlock()
			return cp
		}
	}
	discoveryMu.Unlock()

	disc := discoverModels(baseURL, apiKey, providerHint, 4*time.Second)
	discoveryMu.Lock()
	discoveryCache[cacheKey] = discoveryCacheEntry{at: now, disc: disc}
	discoveryMu.Unlock()
	return disc
}

func discoverModels(baseURL, apiKey, providerHint string, timeout time.Duration) discoveryResult {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	out := discoveryResult{Attempted: true, URL: base + "/models"}
	if base == "" {
		out.Error = "no base URL"
		return out
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, base+"/models", nil)
	if err != nil {
		out.Error = err.Error()
		return out
	}
	req.Header.Set("Accept", "application/json")
	key := strings.TrimSpace(apiKey)
	if key == "" {
		key = "unused"
	}
	req.Header.Set("Authorization", "Bearer "+key)
	if strings.EqualFold(providerHint, "anthropic") || strings.Contains(base, "anthropic") {
		req.Header.Set("x-api-key", key)
		req.Header.Set("anthropic-version", "2023-06-01")
	}
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	if err != nil {
		out.Error = err.Error()
		return out
	}
	defer resp.Body.Close()
	status := resp.StatusCode
	out.Status = &status
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 2<<20))
	if status < 200 || status >= 300 {
		out.Error = strings.TrimSpace(string(body))
		if out.Error == "" {
			out.Error = resp.Status
		}
		if len(out.Error) > 200 {
			out.Error = out.Error[:200]
		}
		return out
	}
	var payload map[string]any
	if err := json.Unmarshal(body, &payload); err != nil {
		out.Error = "invalid JSON"
		return out
	}
	rawList, _ := payload["data"].([]any)
	if rawList == nil {
		if arr, ok := payload["models"].([]any); ok {
			rawList = arr
		}
	}
	models := make([]map[string]any, 0, len(rawList))
	for _, item := range rawList {
		m, ok := asStringMap(item)
		if !ok {
			continue
		}
		id := firstNonEmpty(fmtString(m["id"]), fmtString(m["name"]))
		if id == "" {
			continue
		}
		name := firstNonEmpty(fmtString(m["name"]), id)
		row := map[string]any{"id": id, "name": name, "chat": true}
		if ctxWin := m["context_window"]; ctxWin != nil {
			row["context_window"] = ctxWin
		} else if ctxWin := m["context_length"]; ctxWin != nil {
			row["context_window"] = ctxWin
		}
		models = append(models, row)
	}
	out.OK = true
	out.Models = models
	out.Flavour = "openai"
	if strings.Contains(base, "11434") || providerHint == "ollama" {
		out.Flavour = "ollama"
	}
	return out
}

func detectOllama(baseURL string, timeout time.Duration) map[string]any {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if base == "" {
		base = ollamaBaseURLFromEnv()
	}
	if base == "" {
		base = providerCatalog["ollama"].BaseURL
	}
	// Probe native tags API (strip /v1).
	tagsURL := strings.TrimSuffix(base, "/v1") + "/api/tags"
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, tagsURL, nil)
	if err != nil {
		return map[string]any{
			"available": false, "base_url": base, "models": []string{}, "tags_url": tagsURL,
		}
	}
	resp, err := (&http.Client{Timeout: timeout}).Do(req)
	if err != nil {
		return map[string]any{
			"available": false, "base_url": base, "models": []string{}, "tags_url": tagsURL,
		}
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return map[string]any{
			"available": false, "base_url": base, "models": []string{}, "tags_url": tagsURL,
		}
	}
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var payload map[string]any
	_ = json.Unmarshal(body, &payload)
	names := []string{}
	if arr, ok := payload["models"].([]any); ok {
		for _, item := range arr {
			m, ok := asStringMap(item)
			if !ok {
				continue
			}
			name := firstNonEmpty(fmtString(m["name"]), fmtString(m["model"]))
			if name != "" {
				names = append(names, name)
			}
		}
	}
	return map[string]any{
		"available": true, "base_url": base, "models": names, "tags_url": tagsURL,
	}
}

func ollamaBaseURLFromEnv() string {
	host := strings.TrimSpace(os.Getenv("OLLAMA_HOST"))
	if host == "" {
		return ""
	}
	if !strings.Contains(host, "://") {
		host = "http://" + host
	}
	host = strings.TrimRight(host, "/")
	if strings.HasSuffix(host, "/v1") {
		return host
	}
	return host + "/v1"
}
