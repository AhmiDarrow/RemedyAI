package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	googleChatAPI       = "https://chat.googleapis.com/v1"
	googleOAuthTokenURL = "https://oauth2.googleapis.com/token"
)

// GoogleChatChannel: spaces.messages outbound + webhook inbound.
//
// Inbound webhooks are authenticated with the Google-issued bearer JWT
// (RS256, iss chat@system.gserviceaccount.com, aud = Cloud project number).
type GoogleChatChannel struct {
	gateway       *Gateway
	accessToken   string
	refreshToken  string
	clientID      string
	clientSecret  string
	spaceID       string
	projectNumber string
	access        Access
	client        *http.Client
	tokenURL      string // tests override
	apiBase       string // tests override (default googleChatAPI)
	allowJWKSNet  bool   // tests disable network JWKS fetches
	now           func() time.Time

	mu      sync.Mutex
	running bool
}

// GoogleChatConfig configures a Google Chat adapter. SpaceID is scope only;
// AllowIDs holds sender resource names (users/123...). ProjectNumber is the
// Cloud project number the inbound JWT audience must equal.
type GoogleChatConfig struct {
	AccessToken   string
	RefreshToken  string
	ClientID      string
	ClientSecret  string
	SpaceID       string
	ProjectNumber string
	AllowIDs      any
	AllowAll      bool
}

// NewGoogleChat builds a Google Chat channel bound to a gateway hub.
func NewGoogleChat(g *Gateway, cfg GoogleChatConfig) *GoogleChatChannel {
	spaceID := strings.TrimSpace(cfg.SpaceID)
	scopes := []string{}
	if spaceID != "" {
		bare := strings.TrimPrefix(spaceID, "spaces/")
		scopes = append(scopes, bare, "spaces/"+bare)
	}
	return &GoogleChatChannel{
		gateway:       g,
		accessToken:   strings.TrimSpace(cfg.AccessToken),
		refreshToken:  strings.TrimSpace(cfg.RefreshToken),
		clientID:      strings.TrimSpace(cfg.ClientID),
		clientSecret:  strings.TrimSpace(cfg.ClientSecret),
		spaceID:       spaceID,
		projectNumber: strings.TrimSpace(cfg.ProjectNumber),
		access:        NewAccess(cfg.AllowIDs, cfg.AllowAll, scopes...),
		client:        &http.Client{Timeout: 30 * time.Second},
		tokenURL:      googleOAuthTokenURL,
		allowJWKSNet:  true,
		now:           time.Now,
	}
}

// Health reports token/setup honesty for Settings (no silent ready).
func (c *GoogleChatChannel) Health() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return map[string]any{
		"token_set":      strings.TrimSpace(c.accessToken) != "",
		"refresh_set":    strings.TrimSpace(c.refreshToken) != "",
		"oauth_client":   strings.TrimSpace(c.clientID) != "" && strings.TrimSpace(c.clientSecret) != "",
		"project_number": c.projectNumber != "",
	}
}

// canRefresh reports whether durable OAuth refresh is configured.
func (c *GoogleChatChannel) canRefresh() bool {
	return strings.TrimSpace(c.refreshToken) != "" &&
		strings.TrimSpace(c.clientID) != "" &&
		strings.TrimSpace(c.clientSecret) != ""
}

// refreshAccessToken exchanges the refresh token for a new access token.
func (c *GoogleChatChannel) refreshAccessToken(ctx context.Context) error {
	c.mu.Lock()
	refresh := c.refreshToken
	cid := c.clientID
	secret := c.clientSecret
	tokenURL := c.tokenURL
	if tokenURL == "" {
		tokenURL = googleOAuthTokenURL
	}
	cli := c.client
	c.mu.Unlock()
	if refresh == "" || cid == "" || secret == "" {
		return fmt.Errorf("google_chat: refresh requires refresh_token, oauth_client_id, and oauth_client_secret")
	}
	form := url.Values{}
	form.Set("grant_type", "refresh_token")
	form.Set("refresh_token", refresh)
	form.Set("client_id", cid)
	form.Set("client_secret", secret)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, tokenURL, strings.NewReader(form.Encode()))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	resp, err := cli.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("google_chat: token refresh HTTP %d: %s", resp.StatusCode, trimRunes(string(body), 200))
	}
	var parsed struct {
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
		ExpiresIn    int    `json:"expires_in"`
		TokenType    string `json:"token_type"`
	}
	if err := json.Unmarshal(body, &parsed); err != nil {
		return fmt.Errorf("google_chat: token refresh decode: %w", err)
	}
	if strings.TrimSpace(parsed.AccessToken) == "" {
		return fmt.Errorf("google_chat: token refresh returned empty access_token")
	}
	c.mu.Lock()
	c.accessToken = strings.TrimSpace(parsed.AccessToken)
	if rt := strings.TrimSpace(parsed.RefreshToken); rt != "" {
		c.refreshToken = rt
	}
	c.mu.Unlock()
	log.Printf("google_chat: access token refreshed (expires_in=%ds)", parsed.ExpiresIn)
	return nil
}

func (c *GoogleChatChannel) currentAccessToken() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.accessToken
}

func (c *GoogleChatChannel) Kind() ChannelKind { return ChannelGoogleChat }

func (c *GoogleChatChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *GoogleChatChannel) Start(ctx context.Context) error {
	c.mu.Lock()
	c.running = true
	c.mu.Unlock()
	if c.currentAccessToken() == "" && c.canRefresh() {
		if err := c.refreshAccessToken(ctx); err != nil {
			log.Printf("google_chat: initial refresh failed: %v", err)
		}
	}
	tok := c.currentAccessToken()
	if tok != "" {
		space := c.spaceID
		if space == "" {
			space = "(any)"
		}
		refresh := "no-refresh"
		if c.canRefresh() {
			refresh = "refresh-ready"
		} else if strings.TrimSpace(c.refreshToken) != "" {
			refresh = "refresh-token-only"
		}
		log.Printf("google_chat: active (space=%s, inbound=webhook, %s)", space, refresh)
	} else {
		log.Printf("google_chat: stub mode (no access_token)")
	}
	if c.projectNumber == "" {
		log.Printf("google_chat: project_number not set; inbound webhooks will be rejected")
	}
	return nil
}

func (c *GoogleChatChannel) Stop(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = false
	c.mu.Unlock()
	return nil
}

func (c *GoogleChatChannel) spaceName(space string) string {
	s := strings.TrimSpace(space)
	if s == "" {
		s = c.spaceID
	}
	if s != "" && !strings.HasPrefix(s, "spaces/") {
		s = "spaces/" + s
	}
	return s
}

func (c *GoogleChatChannel) Send(ctx context.Context, message, target string) (bool, error) {
	tok := c.currentAccessToken()
	if tok == "" && c.canRefresh() {
		if err := c.refreshAccessToken(ctx); err != nil {
			log.Printf("google_chat: refresh before send failed: %v", err)
			return false, err
		}
		tok = c.currentAccessToken()
	}
	if tok == "" {
		return true, nil
	}
	space := c.spaceName(target)
	if space == "" {
		return false, nil
	}
	base := c.apiBase
	if base == "" {
		base = googleChatAPI
	}
	status, err := jsonPOST(ctx, c.client,
		base+"/"+space+"/messages",
		map[string]string{"Authorization": "Bearer " + tok},
		map[string]any{"text": trimRunes(message, 4096)},
	)
	if err != nil {
		log.Printf("google_chat: send failed: %v", err)
		return false, err
	}
	if (status == 401 || status == 403) && c.canRefresh() {
		if rerr := c.refreshAccessToken(ctx); rerr != nil {
			log.Printf("google_chat: refresh after %d failed: %v", status, rerr)
			return false, rerr
		}
		tok = c.currentAccessToken()
		status, err = jsonPOST(ctx, c.client,
			base+"/"+space+"/messages",
			map[string]string{"Authorization": "Bearer " + tok},
			map[string]any{"text": trimRunes(message, 4096)},
		)
		if err != nil {
			log.Printf("google_chat: send retry failed: %v", err)
			return false, err
		}
	}
	return status == 200 || status == 201, nil
}

// VerifyInboundAuth requires a Google-issued RS256 JWT whose audience is the
// configured project number. There is no bypass.
func (c *GoogleChatChannel) VerifyInboundAuth(authorization string) bool {
	auth := strings.TrimSpace(authorization)
	if !strings.HasPrefix(strings.ToLower(auth), "bearer ") {
		log.Printf("google_chat: webhook missing Bearer Authorization")
		return false
	}
	if c.projectNumber == "" {
		log.Printf("google_chat: webhook rejected: set google_chat.project_number to verify the Google JWT audience")
		return false
	}
	token := strings.TrimSpace(auth[7:])
	return VerifyGoogleChatJWT(token, c.projectNumber, c.now(), c.allowJWKSNet)
}

// HandleEvent processes a Chat app MESSAGE event.
func (c *GoogleChatChannel) HandleEvent(ctx context.Context, data map[string]any) bool {
	etype := anyString(data["type"])
	if etype == "" {
		etype = anyString(data["eventType"])
	}
	msg, _ := data["message"].(map[string]any)
	if msg == nil {
		msg = map[string]any{}
	}
	if etype != "" && !strings.EqualFold(etype, "MESSAGE") {
		if len(msg) == 0 {
			return false
		}
	}
	text := strings.TrimSpace(anyString(msg["text"]))
	if text == "" {
		text = strings.TrimSpace(anyString(msg["argumentText"]))
	}
	if text == "" {
		return false
	}
	space, _ := data["space"].(map[string]any)
	if space == nil {
		space, _ = msg["space"].(map[string]any)
	}
	spaceName := anyString(space["name"])
	if spaceName == "" {
		log.Printf("google_chat: event without space rejected")
		return false
	}
	spaceID := strings.TrimPrefix(spaceName, "spaces/")
	sender, _ := msg["sender"].(map[string]any)
	if sender == nil {
		sender, _ = data["user"].(map[string]any)
	}
	if sender == nil {
		sender = map[string]any{}
	}
	if anyString(sender["type"]) == "BOT" {
		return false
	}
	// Only the resource name is an identity. displayName is free text.
	userName := anyString(sender["name"])
	if ok, reason := c.access.Permit(userName, spaceName, spaceID); !ok {
		logDeny(ChannelGoogleChat, reason, userName, spaceName)
		c.gateway.recordDenied(ChannelGoogleChat, reason, userName, spaceName)
		return false
	}
	chatID := spaceName
	username := anyString(sender["displayName"])
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelGoogleChat,
		SourceID:  userName,
		SessionID: chatID,
		Payload: map[string]any{
			"message":    text,
			"chat_id":    chatID,
			"channel_id": chatID,
			"user_id":    userName,
			"username":   username,
			"space_id":   spaceID,
		},
		Raw: trimRunes(text, 500),
		At:  time.Now().UTC(),
	}
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(ev)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, ev)
	}
	return true
}
