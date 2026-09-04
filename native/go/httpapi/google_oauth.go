package httpapi

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	googleAuthURL     = "https://accounts.google.com/o/oauth2/v2/auth"
	googleTokenURL    = "https://oauth2.googleapis.com/token"
	googleUserInfoURL = "https://www.googleapis.com/oauth2/v2/userinfo"
	googleRevokeURL   = "https://oauth2.googleapis.com/revoke"
	googleDefaultRedirect = "http://127.0.0.1:7400/api/assistant/google/callback"
	googleConsentVersion  = "gmail_ro_compose_cal_events_v1"
	googlePendingTTL      = 900.0
	googleDoneTTL         = 60.0
)

var googleScopes = []string{
	"openid",
	"email",
	"profile",
	"https://www.googleapis.com/auth/gmail.readonly",
	"https://www.googleapis.com/auth/gmail.compose",
	"https://www.googleapis.com/auth/gmail.modify",
	"https://www.googleapis.com/auth/calendar.events",
}

type googleAppConfig struct {
	ClientID     string
	ClientSecret string
	RedirectURI  string
}

func (c googleAppConfig) configured() bool {
	return strings.TrimSpace(c.ClientID) != ""
}

func (c googleAppConfig) toPublic() map[string]any {
	redir := c.RedirectURI
	if strings.TrimSpace(redir) == "" {
		redir = googleDefaultRedirect
	}
	return map[string]any{
		"client_id_set":     strings.TrimSpace(c.ClientID) != "",
		"client_secret_set": strings.TrimSpace(c.ClientSecret) != "",
		"redirect_uri":      redir,
		"scopes":            append([]string(nil), googleScopes...),
	}
}

type googleTokens struct {
	AccessToken  string
	RefreshToken string
	ExpiresAt    float64
	TokenType    string
	Scope        string
	Email        string
}

func (t googleTokens) connected() bool {
	if strings.TrimSpace(t.AccessToken) != "" &&
		(t.ExpiresAt == 0 || float64(time.Now().Unix()) < t.ExpiresAt-60) {
		return true
	}
	return strings.TrimSpace(t.RefreshToken) != ""
}

func (t googleTokens) toPublic() map[string]any {
	scopes := []string{}
	for _, p := range strings.Fields(t.Scope) {
		if p != "" {
			scopes = append(scopes, p)
		}
	}
	var exp any
	if t.ExpiresAt != 0 {
		exp = t.ExpiresAt
	}
	return map[string]any{
		"provider":    "google",
		"connected":   t.connected(),
		"email":       t.Email,
		"has_refresh": strings.TrimSpace(t.RefreshToken) != "",
		"expires_at":  exp,
		"scopes":      scopes,
	}
}

var (
	googlePendingMu sync.Mutex
	googlePending   = map[string]map[string]any{}
)

func googleTokensPath(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "auth", "google.json")
}

func googleAppConfigPath(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "auth", "google_oauth_app.json")
}

func loadGoogleAppConfig(homeDir string) googleAppConfig {
	cid := firstNonEmpty(
		strings.TrimSpace(os.Getenv("REMEDY_GOOGLE_OAUTH_CLIENT_ID")),
		strings.TrimSpace(os.Getenv("GOOGLE_OAUTH_CLIENT_ID")),
		strings.TrimSpace(os.Getenv("REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_ID")),
	)
	secret := firstNonEmpty(
		strings.TrimSpace(os.Getenv("REMEDY_GOOGLE_OAUTH_CLIENT_SECRET")),
		strings.TrimSpace(os.Getenv("GOOGLE_OAUTH_CLIENT_SECRET")),
		strings.TrimSpace(os.Getenv("REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_SECRET")),
	)
	redirect := firstNonEmpty(
		strings.TrimSpace(os.Getenv("REMEDY_GOOGLE_OAUTH_REDIRECT_URI")),
		googleDefaultRedirect,
	)
	if raw, err := readSealedJSON(googleAppConfigPath(homeDir)); err == nil && raw != nil {
		if v := strings.TrimSpace(fmt.Sprint(nilToEmpty(raw["client_id"]))); v != "" {
			cid = v
		}
		if v := strings.TrimSpace(fmt.Sprint(nilToEmpty(raw["client_secret"]))); v != "" {
			secret = v
		}
		if v := strings.TrimSpace(fmt.Sprint(nilToEmpty(raw["redirect_uri"]))); v != "" {
			redirect = v
		}
	}
	return googleAppConfig{ClientID: cid, ClientSecret: secret, RedirectURI: redirect}
}

func saveGoogleAppConfig(homeDir string, clientID, clientSecret *string, redirectURI *string) (googleAppConfig, error) {
	cur := loadGoogleAppConfig(homeDir)
	if clientID != nil {
		cur.ClientID = strings.TrimSpace(*clientID)
	}
	if clientSecret != nil {
		cur.ClientSecret = strings.TrimSpace(*clientSecret)
	}
	if redirectURI != nil && strings.TrimSpace(*redirectURI) != "" {
		cur.RedirectURI = strings.TrimSpace(*redirectURI)
	}
	if _, err := authDir(homeDir); err != nil {
		return cur, err
	}
	payload := map[string]any{
		"client_id":     cur.ClientID,
		"client_secret": cur.ClientSecret,
		"redirect_uri":  cur.RedirectURI,
		"updated_at":    float64(time.Now().UnixNano()) / 1e9,
	}
	if err := writeSealedJSON(googleAppConfigPath(homeDir), payload); err != nil {
		return cur, err
	}
	return cur, nil
}

func loadGoogleTokens(homeDir string) googleTokens {
	data, err := readSealedJSON(googleTokensPath(homeDir))
	if err != nil || data == nil {
		return googleTokens{TokenType: "Bearer"}
	}
	return googleTokens{
		AccessToken:  strings.TrimSpace(fmt.Sprint(nilToEmpty(data["access_token"]))),
		RefreshToken: strings.TrimSpace(fmt.Sprint(nilToEmpty(data["refresh_token"]))),
		ExpiresAt:    anyFloat(data["expires_at"], 0),
		TokenType:    firstNonEmpty(strings.TrimSpace(fmt.Sprint(nilToEmpty(data["token_type"]))), "Bearer"),
		Scope:        strings.TrimSpace(fmt.Sprint(nilToEmpty(data["scope"]))),
		Email:        strings.TrimSpace(fmt.Sprint(nilToEmpty(data["email"]))),
	}
}

func saveGoogleTokens(homeDir string, tokens googleTokens) error {
	if _, err := authDir(homeDir); err != nil {
		return err
	}
	payload := map[string]any{
		"access_token":  tokens.AccessToken,
		"refresh_token": tokens.RefreshToken,
		"expires_at":    tokens.ExpiresAt,
		"token_type":    firstNonEmpty(tokens.TokenType, "Bearer"),
		"scope":         tokens.Scope,
		"email":         tokens.Email,
		"updated_at":    float64(time.Now().UnixNano()) / 1e9,
	}
	return writeSealedJSON(googleTokensPath(homeDir), payload)
}

func clearGoogleTokens(homeDir string) {
	removeAuthFile(googleTokensPath(homeDir))
}

func googleConsentOK(homeDir string) (bool, string) {
	cfg := LoadConfig(homeDir)
	section, _ := asStringMap(cfg["assistant"])
	if section == nil {
		section = map[string]any{}
	}
	if !coerceBool(section["privacy_ai_accepted"], false) {
		return false, "Accept the AI & privacy notice in Settings → Personal assistant first."
	}
	if !coerceBool(section["account_access_accepted"], false) {
		return false, "Accept account access (OAuth) in Settings → Personal assistant first."
	}
	cv := strings.TrimSpace(fmt.Sprint(nilToEmpty(section["consent_version"])))
	if cv != "" && cv != googleConsentVersion {
		return false, "Privacy terms or account scopes were updated — re-accept in " +
			"Settings → Personal assistant → Connect."
	}
	return true, ""
}

func googlePKCEPair() (verifier, challenge string, err error) {
	buf := make([]byte, 64)
	if _, err = rand.Read(buf); err != nil {
		return "", "", err
	}
	verifier = base64.RawURLEncoding.EncodeToString(buf)
	if len(verifier) > 128 {
		verifier = verifier[:128]
	}
	sum := sha256.Sum256([]byte(verifier))
	challenge = base64.RawURLEncoding.EncodeToString(sum[:])
	return verifier, challenge, nil
}

func googleHTTPForm(endpoint string, form map[string]string) (map[string]any, error) {
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
	req.Header.Set("User-Agent", "RemedyDesktop-Google-OAuth/1.0")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
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
		b, _ := json.Marshal(parsed)
		return nil, fmt.Errorf("%s", string(b))
	}
	return parsed, nil
}

func googleHTTPGetJSON(endpoint, bearer string) (map[string]any, error) {
	req, err := http.NewRequest(http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+bearer)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", "RemedyDesktop-Google-OAuth/1.0")
	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	var parsed map[string]any
	if len(raw) > 0 {
		if err := json.Unmarshal(raw, &parsed); err != nil {
			return nil, err
		}
	}
	if parsed == nil {
		parsed = map[string]any{}
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("http %d", resp.StatusCode)
	}
	return parsed, nil
}

func purgeGooglePendingLocked(now float64) {
	dead := make([]string, 0)
	for key, row := range googlePending {
		created := anyFloat(row["created_at"], 0)
		if doneAt, ok := row["done_at"]; ok && doneAt != nil {
			if now-anyFloat(doneAt, 0) > googleDoneTTL {
				dead = append(dead, key)
				continue
			}
		}
		if created > 0 && now-created > googlePendingTTL {
			dead = append(dead, key)
		}
	}
	for _, k := range dead {
		delete(googlePending, k)
	}
}

func startGoogleOAuth(homeDir, redirectURI string) (map[string]any, error) {
	if ok, reason := googleConsentOK(homeDir); !ok {
		return nil, permissionError{reason}
	}
	app := loadGoogleAppConfig(homeDir)
	if !app.configured() {
		return nil, fmt.Errorf("Google sign-in is not configured for this Remedy build (set REMEDY_GOOGLE_OAUTH_CLIENT_ID).")
	}
	redir := strings.TrimSpace(redirectURI)
	if redir == "" {
		redir = firstNonEmpty(app.RedirectURI, googleDefaultRedirect)
	}
	verifier, challenge, err := googlePKCEPair()
	if err != nil {
		return nil, err
	}
	stateBuf := make([]byte, 18)
	if _, err := rand.Read(stateBuf); err != nil {
		return nil, err
	}
	state := base64.RawURLEncoding.EncodeToString(stateBuf)
	params := url.Values{}
	params.Set("client_id", app.ClientID)
	params.Set("redirect_uri", redir)
	params.Set("response_type", "code")
	params.Set("scope", strings.Join(googleScopes, " "))
	params.Set("state", state)
	params.Set("code_challenge", challenge)
	params.Set("code_challenge_method", "S256")
	params.Set("access_type", "offline")
	params.Set("prompt", "consent")
	params.Set("include_granted_scopes", "true")
	authURL := googleAuthURL + "?" + params.Encode()
	now := float64(time.Now().UnixNano()) / 1e9
	googlePendingMu.Lock()
	purgeGooglePendingLocked(now)
	googlePending[state] = map[string]any{
		"code_verifier": verifier,
		"redirect_uri":  redir,
		"home":          ResolveHomeDir(homeDir),
		"created_at":    now,
		"status":        "pending",
	}
	googlePendingMu.Unlock()
	return map[string]any{
		"status":       "pending",
		"state":        state,
		"auth_url":     authURL,
		"redirect_uri": redir,
		"message":      "Open the link, sign in with Google, then return to Remedy.",
	}, nil
}

type permissionError struct{ msg string }

func (e permissionError) Error() string { return e.msg }

func googlePendingStatus(state string) map[string]any {
	now := float64(time.Now().UnixNano()) / 1e9
	googlePendingMu.Lock()
	purgeGooglePendingLocked(now)
	row := googlePending[state]
	googlePendingMu.Unlock()
	if row == nil {
		return map[string]any{"status": "unknown", "state": state}
	}
	return map[string]any{
		"status": firstNonEmpty(fmt.Sprint(nilToEmpty(row["status"])), "pending"),
		"state":  state,
		"error":  row["error"],
		"email":  row["email"],
	}
}

func completeGoogleOAuth(homeDir, code, state string) (googleTokens, error) {
	now := float64(time.Now().UnixNano()) / 1e9
	googlePendingMu.Lock()
	purgeGooglePendingLocked(now)
	row := googlePending[state]
	if row == nil {
		googlePendingMu.Unlock()
		return googleTokens{}, fmt.Errorf("Invalid or expired OAuth state — start Connect again.")
	}
	status := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["status"])))
	if status == "" {
		status = "pending"
	}
	switch status {
	case "connected", "exchanging", "consumed", "error":
		googlePendingMu.Unlock()
		return googleTokens{}, fmt.Errorf("OAuth state already used — start Connect again.")
	}
	if now-anyFloat(row["created_at"], 0) > googlePendingTTL {
		delete(googlePending, state)
		googlePendingMu.Unlock()
		return googleTokens{}, fmt.Errorf("OAuth session expired — start Connect again.")
	}
	codeVerifier := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["code_verifier"])))
	if codeVerifier == "" {
		delete(googlePending, state)
		googlePendingMu.Unlock()
		return googleTokens{}, fmt.Errorf("OAuth state missing verifier — start Connect again.")
	}
	redirectURI := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["redirect_uri"])))
	rowHome := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["home"])))
	row["status"] = "exchanging"
	delete(row, "code_verifier")
	googlePendingMu.Unlock()

	appHome := homeDir
	if strings.TrimSpace(appHome) == "" {
		appHome = rowHome
	}
	app := loadGoogleAppConfig(appHome)
	form := map[string]string{
		"code":          code,
		"client_id":     app.ClientID,
		"redirect_uri":  firstNonEmpty(redirectURI, app.RedirectURI),
		"grant_type":    "authorization_code",
		"code_verifier": codeVerifier,
	}
	if strings.TrimSpace(app.ClientSecret) != "" {
		form["client_secret"] = app.ClientSecret
	}
	data, err := googleHTTPForm(googleTokenURL, form)
	if err != nil {
		googlePendingMu.Lock()
		googlePending[state] = map[string]any{
			"status":     "error",
			"error":      clipStr(err.Error(), 200),
			"created_at": now,
			"done_at":    float64(time.Now().UnixNano()) / 1e9,
			"home":       rowHome,
		}
		googlePendingMu.Unlock()
		return googleTokens{}, err
	}
	if errMsg := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["error"]))); errMsg != "" {
		detail := firstNonEmpty(strings.TrimSpace(fmt.Sprint(nilToEmpty(data["error_description"]))), errMsg)
		googlePendingMu.Lock()
		googlePending[state] = map[string]any{
			"status":     "error",
			"error":      clipStr(detail, 300),
			"created_at": now,
			"done_at":    float64(time.Now().UnixNano()) / 1e9,
			"home":       rowHome,
		}
		googlePendingMu.Unlock()
		return googleTokens{}, fmt.Errorf("%s", detail)
	}
	access := strings.TrimSpace(fmt.Sprint(nilToEmpty(data["access_token"])))
	if access == "" {
		googlePendingMu.Lock()
		googlePending[state] = map[string]any{
			"status":     "error",
			"error":      "missing access_token",
			"created_at": now,
			"done_at":    float64(time.Now().UnixNano()) / 1e9,
			"home":       rowHome,
		}
		googlePendingMu.Unlock()
		return googleTokens{}, fmt.Errorf("Google token response missing access_token")
	}
	existing := loadGoogleTokens(appHome)
	refresh := firstNonEmpty(
		strings.TrimSpace(fmt.Sprint(nilToEmpty(data["refresh_token"]))),
		existing.RefreshToken,
	)
	expiresIn := anyFloat(data["expires_in"], 3600)
	tokens := googleTokens{
		AccessToken:  access,
		RefreshToken: refresh,
		ExpiresAt:    float64(time.Now().Unix()) + expiresIn,
		TokenType:    firstNonEmpty(strings.TrimSpace(fmt.Sprint(nilToEmpty(data["token_type"]))), "Bearer"),
		Scope:        firstNonEmpty(strings.TrimSpace(fmt.Sprint(nilToEmpty(data["scope"]))), strings.Join(googleScopes, " ")),
	}
	if info, err := googleHTTPGetJSON(googleUserInfoURL, access); err == nil {
		tokens.Email = strings.TrimSpace(fmt.Sprint(nilToEmpty(info["email"])))
	}
	if err := saveGoogleTokens(appHome, tokens); err != nil {
		return googleTokens{}, err
	}
	syncGoogleLinkedAccount(appHome, tokens)
	googlePendingMu.Lock()
	googlePending[state] = map[string]any{
		"status":     "connected",
		"email":      tokens.Email,
		"created_at": now,
		"done_at":    float64(time.Now().UnixNano()) / 1e9,
		"home":       rowHome,
	}
	googlePendingMu.Unlock()
	return tokens, nil
}

func clipStr(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func syncGoogleLinkedAccount(homeDir string, tokens googleTokens) {
	path := filepath.Join(ResolveHomeDir(homeDir), "assistant.json")
	data := map[string]any{
		"version": 1,
		"prefs":   map[string]any{},
		"accounts": []any{},
	}
	_ = readJSONFile(path, &data)
	accounts, _ := data["accounts"].([]any)
	acct := map[string]any{
		"id":           "google_primary",
		"provider":     "google",
		"email":        tokens.Email,
		"capabilities": []string{"mail", "calendar"},
		"status":       "connected",
		"last_sync":    time.Now().UTC().Format(time.RFC3339),
	}
	if !tokens.connected() {
		acct["status"] = "disconnected"
	}
	replaced := false
	out := make([]any, 0, len(accounts)+1)
	for _, raw := range accounts {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		id := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["id"])))
		email := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["email"])))
		provider := strings.TrimSpace(fmt.Sprint(nilToEmpty(row["provider"])))
		if id == "google_primary" || (provider == "google" && email == tokens.Email && tokens.Email != "") {
			out = append(out, acct)
			replaced = true
			continue
		}
		out = append(out, row)
	}
	if !replaced {
		out = append(out, acct)
	}
	data["accounts"] = out
	data["updated_at"] = time.Now().UTC().Format(time.RFC3339)
	_ = writeJSONAtomic(path, data)
}

func disconnectGoogle(homeDir string) {
	tokens := loadGoogleTokens(homeDir)
	if strings.TrimSpace(tokens.AccessToken) != "" {
		// Best-effort revoke; never block disconnect on network.
		go func(tok string) {
			client := &http.Client{Timeout: 3 * time.Second}
			values := url.Values{"token": {tok}}
			req, err := http.NewRequest(http.MethodPost, googleRevokeURL, strings.NewReader(values.Encode()))
			if err != nil {
				return
			}
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
			resp, err := client.Do(req)
			if err == nil {
				_ = resp.Body.Close()
			}
		}(tokens.AccessToken)
	}
	clearGoogleTokens(homeDir)
	tokens.AccessToken = ""
	tokens.RefreshToken = ""
	tokens.ExpiresAt = 0
	syncGoogleLinkedAccount(homeDir, tokens)
}

func googlePublicStatus(homeDir string) map[string]any {
	app := loadGoogleAppConfig(homeDir)
	tokens := loadGoogleTokens(homeDir)
	enc := sealedAuthEncoding(googleTokensPath(homeDir))
	out := tokens.toPublic()
	out["app"] = app.toPublic()
	if app.configured() {
		out["setup_hint"] = nil
	} else {
		out["setup_hint"] = "not_configured"
	}
	out["sign_in_ready"] = app.configured()
	out["tokens_encoding"] = enc
	if enc == "plain" && tokens.connected() {
		out["tokens_encoding_warning"] = "Google tokens are stored as plaintext (DPAPI seal unavailable). " +
			"Anyone with access to your Windows user profile can read them. " +
			"Fix DPAPI / re-connect if this was unexpected."
	}
	return out
}

func googleHTMLPage(title, body string) string {
	escTitle := html.EscapeString(title)
	return `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><title>` + escTitle + ` — Remedy</title>
<style>
body{font-family:system-ui,sans-serif;background:#0f1419;color:#e7ecf1;display:flex;
align-items:center;justify-content:center;min-height:100vh;margin:0}
.card{max-width:28rem;padding:1.5rem;border:1px solid #2a3540;border-radius:12px;background:#1a222c}
h1{font-size:1.15rem;margin:0 0 .75rem} p{line-height:1.45;color:#a8b3bf;font-size:.9rem}
strong{color:#e7ecf1}
</style></head><body><div class="card"><h1>` + escTitle + `</h1>` + body + `</div></body></html>`
}

func writeGoogleHTML(w http.ResponseWriter, status int, title, body string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(status)
	_, _ = io.WriteString(w, googleHTMLPage(title, body))
}

func (s *Server) handleGoogleStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, googlePublicStatus(ResolveHomeDir(s.homeDir)))
}

func (s *Server) handleGoogleSaveApp(w http.ResponseWriter, r *http.Request) {
	var req struct {
		ClientID     *string `json:"client_id"`
		ClientSecret *string `json:"client_secret"`
		RedirectURI  *string `json:"redirect_uri"`
		ClearSecret  bool    `json:"clear_secret"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	var secretPtr *string
	if req.ClearSecret {
		empty := ""
		secretPtr = &empty
	} else if req.ClientSecret != nil {
		secretPtr = req.ClientSecret
	} else {
		cur := loadGoogleAppConfig(home)
		secretPtr = &cur.ClientSecret
	}
	clientID := req.ClientID
	if clientID == nil {
		cur := loadGoogleAppConfig(home)
		clientID = &cur.ClientID
	}
	cfg, err := saveGoogleAppConfig(home, clientID, secretPtr, req.RedirectURI)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok", "app": cfg.toPublic()})
}

func (s *Server) handleGoogleOAuthStart(w http.ResponseWriter, r *http.Request) {
	var req struct {
		RedirectURI string `json:"redirect_uri"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req)
	home := ResolveHomeDir(s.homeDir)
	result, err := startGoogleOAuth(home, req.RedirectURI)
	if err != nil {
		if _, ok := err.(permissionError); ok {
			writeJSON(w, http.StatusForbidden, map[string]string{"detail": err.Error()})
			return
		}
		msg := err.Error()
		if strings.Contains(msg, "not configured") {
			writeJSON(w, http.StatusBadRequest, map[string]string{"detail": msg})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": msg})
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s *Server) handleGoogleOAuthStatus(w http.ResponseWriter, r *http.Request) {
	state := strings.TrimSpace(r.URL.Query().Get("state"))
	if len(state) < 4 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "state required"})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	st := googlePendingStatus(state)
	tokens := loadGoogleTokens(home)
	out := map[string]any{}
	for k, v := range st {
		out[k] = v
	}
	out["credentials"] = tokens.toPublic()
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleGoogleOAuthCallback(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	if errVal := strings.TrimSpace(q.Get("error")); errVal != "" {
		msg := firstNonEmpty(strings.TrimSpace(q.Get("error_description")), errVal)
		writeGoogleHTML(w, http.StatusBadRequest, "Google sign-in failed",
			"<p>"+html.EscapeString(msg)+"</p><p>Close this tab and try again in Settings.</p>")
		return
	}
	code := strings.TrimSpace(q.Get("code"))
	state := strings.TrimSpace(q.Get("state"))
	if code == "" || state == "" {
		writeGoogleHTML(w, http.StatusBadRequest, "Missing code",
			"<p>OAuth callback incomplete. Start Connect again from Settings.</p>")
		return
	}
	home := ResolveHomeDir(s.homeDir)
	tokens, err := completeGoogleOAuth(home, code, state)
	if err != nil {
		writeGoogleHTML(w, http.StatusBadRequest, "Could not finish Google sign-in",
			"<p>"+html.EscapeString(err.Error())+"</p>")
		return
	}
	email := firstNonEmpty(tokens.Email, "connected")
	writeGoogleHTML(w, http.StatusOK, "Google connected",
		"<p>Signed in as <strong>"+html.EscapeString(email)+"</strong>.</p>"+
			"<p>You can close this tab and return to Remedy → Settings → Personal assistant.</p>")
}

func (s *Server) handleGoogleDisconnect(w http.ResponseWriter, _ *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	disconnectGoogle(home)
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok",
		"google": googlePublicStatus(home),
	})
}
