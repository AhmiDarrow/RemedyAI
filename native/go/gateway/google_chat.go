package gateway

import (
	"context"
	"crypto/hmac"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	googleChatAPI      = "https://chat.googleapis.com/v1"
	googleOAuthTokenURL = "https://oauth2.googleapis.com/token"
)

// GoogleChatChannel: spaces.messages outbound + webhook inbound.
type GoogleChatChannel struct {
	gateway      *Gateway
	accessToken  string
	refreshToken string
	clientID     string
	clientSecret string
	spaceID      string
	allowed      map[string]struct{}
	allowAll     bool
	client       *http.Client
	tokenURL     string // tests override
	apiBase      string // tests override (default googleChatAPI)

	mu      sync.Mutex
	running bool
}

// GoogleChatConfig configures a Google Chat adapter.
type GoogleChatConfig struct {
	AccessToken  string
	RefreshToken string
	ClientID     string
	ClientSecret string
	SpaceID      string
	AllowIDs     any
	AllowAll     bool
}

// NewGoogleChat builds a Google Chat channel bound to a gateway hub.
func NewGoogleChat(g *Gateway, cfg GoogleChatConfig) *GoogleChatChannel {
	allowed := ParseIDs(cfg.AllowIDs)
	spaceID := strings.TrimSpace(cfg.SpaceID)
	if spaceID != "" {
		allowed[spaceID] = struct{}{}
	}
	return &GoogleChatChannel{
		gateway:      g,
		accessToken:  strings.TrimSpace(cfg.AccessToken),
		refreshToken: strings.TrimSpace(cfg.RefreshToken),
		clientID:     strings.TrimSpace(cfg.ClientID),
		clientSecret: strings.TrimSpace(cfg.ClientSecret),
		spaceID:      spaceID,
		allowed:      allowed,
		allowAll:     cfg.AllowAll,
		client:       &http.Client{Timeout: 30 * time.Second},
		tokenURL:     googleOAuthTokenURL,
	}
}

// Health reports token/setup honesty for Settings (no silent ready).
func (c *GoogleChatChannel) Health() map[string]any {
	c.mu.Lock()
	defer c.mu.Unlock()
	return map[string]any{
		"token_set":    strings.TrimSpace(c.accessToken) != "",
		"refresh_set":  strings.TrimSpace(c.refreshToken) != "",
		"oauth_client": strings.TrimSpace(c.clientID) != "" && strings.TrimSpace(c.clientSecret) != "",
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

// VerifyInboundAuth requires Bearer matching access_token when configured.
func (c *GoogleChatChannel) VerifyInboundAuth(authorization string) bool {
	expected := c.currentAccessToken()
	if expected == "" {
		return false
	}
	auth := strings.TrimSpace(authorization)
	if !strings.HasPrefix(strings.ToLower(auth), "bearer ") {
		if envTruthy("REMEDY_GCHAT_ALLOW_NO_AUTH") {
			return true
		}
		log.Printf("google_chat: webhook missing Bearer Authorization")
		return false
	}
	presented := strings.TrimSpace(auth[7:])
	if len(presented) != len(expected) {
		return false
	}
	return hmac.Equal([]byte(presented), []byte(expected))
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
	if space == nil {
		space = map[string]any{}
	}
	spaceName := anyString(space["name"])
	if spaceName == "" {
		spaceName = c.spaceID
	}
	spaceID := strings.ReplaceAll(spaceName, "spaces/", "")
	sender, _ := msg["sender"].(map[string]any)
	if sender == nil {
		sender, _ = data["user"].(map[string]any)
	}
	if sender == nil {
		sender = map[string]any{}
	}
	userName := anyString(sender["name"])
	if userName == "" {
		userName = anyString(sender["displayName"])
	}
	if anyString(sender["type"]) == "BOT" {
		return false
	}
	if len(c.allowed) == 0 && !c.allowAll {
		log.Printf("google_chat: ignore (empty allowlist, allow_all=false) space=%s", firstNonEmpty(spaceID, spaceName))
		return false
	}
	if !IsAllowed(c.allowed, c.allowAll, spaceName, spaceID, userName) {
		return false
	}
	chatID := spaceName
	if chatID == "" {
		chatID = spaceID
	}
	if chatID == "" {
		chatID = "default"
	}
	sourceID := userName
	if sourceID == "" {
		sourceID = chatID
	}
	username := anyString(sender["displayName"])
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelGoogleChat,
		SourceID:  sourceID,
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

func envTruthy(key string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}
