package httpapi

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	xaiAccountsServer = "https://accounts.x.ai"
	xaiOAuthServer    = "https://auth.x.ai"
	xaiDeviceCodeURL  = xaiOAuthServer + "/oauth2/device/code"
	xaiTokenURL       = xaiOAuthServer + "/oauth2/token"
	xaiOAuthBuildID   = "auth.x.ai-device-v4-api-access"
	xaiDefaultScope   = "openid profile email offline_access grok-cli:access api:access"
	// Public xAI device-OAuth client id (override via REMEDY_XAI_OAUTH_CLIENT_ID).
	xaiDefaultClientID = "b1a00492-073a-47ea-816f-4c329264a828"
	xaiGrantDevice     = "urn:ietf:params:oauth:grant-type:device_code"
)

var xaiDeviceCodeCandidates = []string{
	xaiDeviceCodeURL,
	"https://auth.x.ai/oauth2/device/code",
}

type xaiCredentials struct {
	AuthMethod   string
	APIKey       string
	AccessToken  string
	RefreshToken string
	ExpiresAt    *float64
	TokenType    string
}

var (
	xaiPollMu       sync.Mutex
	xaiPollSessions = map[string]map[string]any{}
)

func xaiClientID() string {
	if v := strings.TrimSpace(os.Getenv("REMEDY_XAI_OAUTH_CLIENT_ID")); v != "" {
		return v
	}
	return xaiDefaultClientID
}

func xaiAuthPath(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "auth", "xai.json")
}

func coerceExpiresAt(v any) *float64 {
	if v == nil {
		return nil
	}
	switch t := v.(type) {
	case float64:
		return &t
	case int:
		f := float64(t)
		return &f
	case int64:
		f := float64(t)
		return &f
	case json.Number:
		f, err := t.Float64()
		if err != nil {
			return nil
		}
		return &f
	case string:
		s := strings.TrimSpace(t)
		if s == "" {
			return nil
		}
		var f float64
		if _, err := fmt.Sscanf(s, "%f", &f); err != nil {
			return nil
		}
		return &f
	default:
		return nil
	}
}

func (c xaiCredentials) connected() bool {
	switch c.AuthMethod {
	case "api_key":
		return strings.TrimSpace(c.APIKey) != ""
	case "oauth":
		if strings.TrimSpace(c.AccessToken) == "" {
			return false
		}
		if c.ExpiresAt != nil && time.Now().Unix() >= int64(*c.ExpiresAt)-60 {
			return strings.TrimSpace(c.RefreshToken) != ""
		}
		return true
	default:
		return false
	}
}

func (c xaiCredentials) toPublic(homeDir string) map[string]any {
	out := map[string]any{
		"provider":    "xai",
		"auth_method": c.AuthMethod,
		"connected":   c.connected(),
		"has_api_key": strings.TrimSpace(c.APIKey) != "",
		"has_oauth":   strings.TrimSpace(c.AccessToken) != "" || strings.TrimSpace(c.RefreshToken) != "",
		"expires_at":  nil,
	}
	if c.ExpiresAt != nil {
		out["expires_at"] = *c.ExpiresAt
	}
	enc := sealedAuthEncoding(xaiAuthPath(homeDir))
	out["credentials_encoding"] = enc
	if enc == "plain" && c.connected() {
		out["credentials_encoding_warning"] = "xAI credentials are stored as plaintext (DPAPI seal unavailable). " +
			"Anyone with access to your Windows user profile can read them. " +
			"Fix DPAPI / re-connect if this was unexpected."
	}
	return out
}

func loadXaiCredentials(homeDir string) xaiCredentials {
	path := xaiAuthPath(homeDir)
	data, err := readSealedJSON(path)
	if err != nil {
		envKey := strings.TrimSpace(os.Getenv("XAI_API_KEY"))
		if envKey == "" {
			envKey = strings.TrimSpace(os.Getenv("REMEDY_XAI_API_KEY"))
		}
		if envKey != "" {
			return xaiCredentials{AuthMethod: "api_key", APIKey: envKey, TokenType: "Bearer"}
		}
		return xaiCredentials{AuthMethod: "none", TokenType: "Bearer"}
	}
	method := strings.TrimSpace(fmt.Sprint(data["auth_method"]))
	if method == "" {
		method = "none"
	}
	return xaiCredentials{
		AuthMethod:   method,
		APIKey:       strings.TrimSpace(fmt.Sprint(nilToEmpty(data["api_key"]))),
		AccessToken:  strings.TrimSpace(fmt.Sprint(nilToEmpty(data["access_token"]))),
		RefreshToken: strings.TrimSpace(fmt.Sprint(nilToEmpty(data["refresh_token"]))),
		ExpiresAt:    coerceExpiresAt(data["expires_at"]),
		TokenType:    firstNonEmpty(strings.TrimSpace(fmt.Sprint(nilToEmpty(data["token_type"]))), "Bearer"),
	}
}

func nilToEmpty(v any) any {
	if v == nil {
		return ""
	}
	return v
}

func saveXaiCredentials(homeDir string, creds xaiCredentials) error {
	if _, err := authDir(homeDir); err != nil {
		return err
	}
	payload := map[string]any{
		"auth_method":   creds.AuthMethod,
		"api_key":       emptyToNil(creds.APIKey),
		"access_token":  emptyToNil(creds.AccessToken),
		"refresh_token": emptyToNil(creds.RefreshToken),
		"expires_at":    nil,
		"token_type":    firstNonEmpty(creds.TokenType, "Bearer"),
		"updated_at":    float64(time.Now().UnixNano()) / 1e9,
	}
	if creds.ExpiresAt != nil {
		payload["expires_at"] = *creds.ExpiresAt
	}
	return writeSealedJSON(xaiAuthPath(homeDir), payload)
}

func emptyToNil(s string) any {
	if strings.TrimSpace(s) == "" {
		return nil
	}
	return s
}

func clearXaiCredentials(homeDir string) {
	removeAuthFile(xaiAuthPath(homeDir))
}

func saveXaiAPIKey(homeDir, apiKey string) (xaiCredentials, error) {
	key := strings.TrimSpace(apiKey)
	if key == "" {
		return xaiCredentials{}, fmt.Errorf("API key is empty")
	}
	creds := xaiCredentials{AuthMethod: "api_key", APIKey: key, TokenType: "Bearer"}
	if err := saveXaiCredentials(homeDir, creds); err != nil {
		return xaiCredentials{}, err
	}
	return creds, nil
}

func xaiHTTPForm(endpoint string, form map[string]string) (map[string]any, error) {
	if strings.Contains(endpoint, "accounts.x.ai") &&
		(strings.Contains(endpoint, "/oauth2/device/code") || strings.Contains(endpoint, "/oauth2/token")) {
		return nil, fmt.Errorf(`{"error":"refusing accounts.x.ai for device/token API","url":%q,"hint":"Use https://auth.x.ai — accounts.x.ai returns 307 to /sign-in","oauth_build":%q}`,
			endpoint, xaiOAuthBuildID)
	}
	values := url.Values{}
	for k, v := range form {
		values.Set(k, v)
	}
	req, err := http.NewRequest(http.MethodPost, endpoint, strings.NewReader(values.Encode()))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "RemedyDesktop-xAI-Auth/1.0 ("+xaiOAuthBuildID+")")
	client := &http.Client{
		Timeout: 30 * time.Second,
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		parsed := map[string]any{
			"error":       string(raw),
			"status":      resp.StatusCode,
			"request_url": endpoint,
			"oauth_build": xaiOAuthBuildID,
			"location":    resp.Header.Get("Location"),
			"hint": "xAI OAuth API redirected to a web sign-in page. " +
				"This usually means a stale local API binary is still calling " +
				"accounts.x.ai. Install a current build or replace remedy-runtime. " +
				"Expected host: auth.x.ai (build " + xaiOAuthBuildID + ").",
		}
		b, _ := json.Marshal(parsed)
		return nil, fmt.Errorf("%s", string(b))
	}
	var parsed map[string]any
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &parsed); err != nil {
			parsed = map[string]any{"error": string(raw), "status": resp.StatusCode}
		}
	}
	if parsed == nil {
		parsed = map[string]any{}
	}
	if resp.StatusCode >= 400 {
		parsed["status"] = resp.StatusCode
		parsed["request_url"] = endpoint
		parsed["oauth_build"] = xaiOAuthBuildID
		b, _ := json.Marshal(parsed)
		return nil, fmt.Errorf("%s", string(b))
	}
	return parsed, nil
}

func startXaiDeviceLogin(homeDir string) (map[string]any, error) {
	clientID := xaiClientID()
	scope := strings.TrimSpace(os.Getenv("REMEDY_XAI_OAUTH_SCOPE"))
	if scope == "" {
		scope = xaiDefaultScope
	}
	form := map[string]string{
		"client_id": clientID,
		"scope":     scope,
	}
	var (
		data    map[string]any
		lastErr error
		usedURL = xaiDeviceCodeURL
	)
	for _, u := range xaiDeviceCodeCandidates {
		got, err := xaiHTTPForm(u, form)
		if err != nil {
			lastErr = err
			continue
		}
		data = got
		usedURL = u
		break
	}
	if data == nil {
		if lastErr != nil {
			return nil, lastErr
		}
		return nil, fmt.Errorf(`{"error":"all device-code endpoints failed","oauth_build":%q}`, xaiOAuthBuildID)
	}
	deviceCode := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["device_code"])))
	userCode := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["user_code"])))
	if deviceCode == "" || userCode == "" {
		return nil, fmt.Errorf("Unexpected device-code response: %v", data)
	}
	interval := anyInt(data["interval"], 5)
	expiresIn := anyInt(data["expires_in"], 900)
	verificationURI := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["verification_uri"])))
	if verificationURI == "" {
		verificationURI = strings.TrimSpace(fmt.Sprint(nilToEmpty(data["verification_url"])))
	}
	if verificationURI == "" {
		verificationURI = xaiAccountsServer + "/oauth2/device"
	}
	verificationComplete := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["verification_uri_complete"])))
	if verificationComplete == "" {
		verificationComplete = verificationURI + "?user_code=" + url.QueryEscape(userCode)
	}
	sessionID := userCode
	home := ResolveHomeDir(homeDir)
	xaiPollMu.Lock()
	xaiPollSessions[sessionID] = map[string]any{
		"device_code": deviceCode,
		"client_id":   clientID,
		"interval":    interval,
		"expires_at":  float64(time.Now().Unix()) + float64(expiresIn),
		"home":        home,
		"status":      "pending",
		"error":       nil,
	}
	xaiPollMu.Unlock()
	go pollXaiUntilDone(sessionID)

	host := ""
	if u, err := url.Parse(usedURL); err == nil {
		host = u.Host
	}
	return map[string]any{
		"session_id":                 sessionID,
		"user_code":                  userCode,
		"device_code":                deviceCode,
		"verification_uri":           verificationURI,
		"verification_uri_complete":  verificationComplete,
		"interval":                   interval,
		"expires_in":                 expiresIn,
		"status":                     "pending",
		"oauth_host":                 host,
		"oauth_build":                xaiOAuthBuildID,
		"message": fmt.Sprintf(
			"Open %s and approve access, or go to %s and enter code %s.",
			verificationComplete, verificationURI, userCode,
		),
	}, nil
}

func pollXaiUntilDone(sessionID string) {
	xaiPollMu.Lock()
	sess := cloneAnyMap(xaiPollSessions[sessionID])
	xaiPollMu.Unlock()
	if len(sess) == 0 {
		return
	}
	deviceCode, _ := sess["device_code"].(string)
	clientID, _ := sess["client_id"].(string)
	interval := anyInt(sess["interval"], 5)
	if interval < 3 {
		interval = 3
	}
	expiresAt := anyFloat(sess["expires_at"], float64(time.Now().Unix())+900)
	home, _ := sess["home"].(string)

	for float64(time.Now().Unix()) < expiresAt {
		time.Sleep(time.Duration(interval) * time.Second)
		data, err := xaiHTTPForm(xaiTokenURL, map[string]string{
			"grant_type":  xaiGrantDevice,
			"device_code": deviceCode,
			"client_id":   clientID,
		})
		if err != nil {
			errCode := err.Error()
			if strings.Contains(errCode, "authorization_pending") || strings.Contains(errCode, "slow_down") {
				if strings.Contains(errCode, "slow_down") {
					interval = min(interval+2, 15)
				}
				continue
			}
			if strings.Contains(errCode, "expired") || strings.Contains(errCode, "access_denied") {
				xaiPollMu.Lock()
				if row, ok := xaiPollSessions[sessionID]; ok {
					row["status"] = "error"
					row["error"] = errCode
				}
				xaiPollMu.Unlock()
				return
			}
			continue
		}
		access := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["access_token"])))
		if access == "" {
			continue
		}
		expiresIn := anyInt(data["expires_in"], 3600)
		exp := float64(time.Now().Unix()) + float64(expiresIn)
		creds := xaiCredentials{
			AuthMethod:   "oauth",
			AccessToken:  access,
			RefreshToken: strings.TrimSpace(fmt.Sprint(nilToEmpty(data["refresh_token"]))),
			ExpiresAt:    &exp,
			TokenType:    firstNonEmpty(strings.TrimSpace(fmt.Sprint(nilToEmpty(data["token_type"]))), "Bearer"),
		}
		_ = saveXaiCredentials(home, creds)
		xaiPollMu.Lock()
		if row, ok := xaiPollSessions[sessionID]; ok {
			row["status"] = "connected"
			row["error"] = nil
		}
		xaiPollMu.Unlock()
		return
	}
	xaiPollMu.Lock()
	if row, ok := xaiPollSessions[sessionID]; ok {
		row["status"] = "error"
		row["error"] = "expired_token"
	}
	xaiPollMu.Unlock()
}

func xaiLoginStatus(homeDir, sessionID string) map[string]any {
	creds := loadXaiCredentials(homeDir)
	out := map[string]any{
		"credentials": creds.toPublic(homeDir),
	}
	sessionID = strings.TrimSpace(sessionID)
	if sessionID == "" {
		return out
	}
	xaiPollMu.Lock()
	sess := xaiPollSessions[sessionID]
	xaiPollMu.Unlock()
	if sess == nil {
		out["session"] = map[string]any{
			"session_id": sessionID,
			"status":     "unknown",
			"error":      nil,
		}
		return out
	}
	status, _ := sess["status"].(string)
	out["session"] = map[string]any{
		"session_id": sessionID,
		"status":     status,
		"error":      sess["error"],
	}
	if status == "connected" {
		out["credentials"] = loadXaiCredentials(homeDir).toPublic(homeDir)
	}
	return out
}

func cloneAnyMap(in map[string]any) map[string]any {
	if in == nil {
		return nil
	}
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func anyFloat(v any, def float64) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	case int64:
		return float64(t)
	default:
		return def
	}
}

func (s *Server) handleXaiAuthStatus(w http.ResponseWriter, _ *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	writeJSON(w, http.StatusOK, loadXaiCredentials(home).toPublic(home))
}

func (s *Server) handleXaiOAuthMeta(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"oauth_build":     xaiOAuthBuildID,
		"device_code_url": xaiDeviceCodeURL,
		"token_url":       xaiTokenURL,
		"accounts_server": xaiAccountsServer,
	})
}

func (s *Server) handleXaiAuthLogin(w http.ResponseWriter, _ *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	result, err := startXaiDeviceLogin(home)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]string{
			"detail": "xAI OAuth start failed: " + err.Error(),
		})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) handleXaiAuthLoginStatus(w http.ResponseWriter, r *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	sessionID := strings.TrimSpace(r.URL.Query().Get("session_id"))
	// GET must not steal the chat provider (Python only refreshes when already on xAI).
	writeJSON(w, http.StatusOK, xaiLoginStatus(home, sessionID))
}

func (s *Server) handleXaiAuthAPIKey(w http.ResponseWriter, r *http.Request) {
	var req struct {
		APIKey string `json:"api_key"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	creds, err := saveXaiAPIKey(home, req.APIKey)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	key := strings.TrimSpace(req.APIKey)
	cfg := LoadConfig(s.homeDir)
	prev := strings.ToLower(strings.TrimSpace(cfgString(cfg, "llm_provider", "")))
	if prev == "xai" || prev == "" {
		provider, model, baseURL := normalizeLLMSettings("xai", cfgString(cfg, "llm_model", ""), cfgString(cfg, "llm_base_url", "https://api.x.ai/v1"))
		path := FindConfigPath(s.homeDir)
		if path == "" {
			path = DefaultConfigPath(s.homeDir)
		}
		if path != "" {
			if cfg == nil {
				cfg = ConfigMap{}
			}
			cfg["llm_provider"] = provider
			cfg["llm_model"] = model
			cfg["llm_base_url"] = baseURL
			cfg["llm_api_key"] = ""
			_ = WriteConfig(path, scrubConfigSecrets(cfg))
		}
		_ = secret.SetProviderSecret(home, "xai", key)
	}
	out := creds.toPublic(home)
	out["status"] = "saved"
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleXaiAuthLogout(w http.ResponseWriter, _ *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	clearXaiCredentials(home)
	cfg := LoadConfig(s.homeDir)
	if strings.EqualFold(cfgString(cfg, "llm_provider", ""), "xai") {
		path := FindConfigPath(s.homeDir)
		if path != "" {
			cfg["llm_api_key"] = ""
			_ = WriteConfig(path, scrubConfigSecrets(cfg))
		}
		_ = secret.SetProviderSecret(home, "xai", "")
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":    "logged_out",
		"provider":  "xai",
		"connected": false,
	})
}
