package gateway

import (
	"context"
	"crypto/hmac"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

const googleChatAPI = "https://chat.googleapis.com/v1"

// GoogleChatChannel: spaces.messages outbound + webhook inbound.
type GoogleChatChannel struct {
	gateway     *Gateway
	accessToken string
	spaceID     string
	allowed     map[string]struct{}
	allowAll    bool
	client      *http.Client

	mu      sync.Mutex
	running bool
}

// GoogleChatConfig configures a Google Chat adapter.
type GoogleChatConfig struct {
	AccessToken string
	SpaceID     string
	AllowIDs    any
	AllowAll    bool
}

// NewGoogleChat builds a Google Chat channel bound to a gateway hub.
func NewGoogleChat(g *Gateway, cfg GoogleChatConfig) *GoogleChatChannel {
	allowed := ParseIDs(cfg.AllowIDs)
	spaceID := strings.TrimSpace(cfg.SpaceID)
	if spaceID != "" {
		allowed[spaceID] = struct{}{}
	}
	return &GoogleChatChannel{
		gateway:     g,
		accessToken: strings.TrimSpace(cfg.AccessToken),
		spaceID:     spaceID,
		allowed:     allowed,
		allowAll:    cfg.AllowAll,
		client:      &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *GoogleChatChannel) Kind() ChannelKind { return ChannelGoogleChat }

func (c *GoogleChatChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *GoogleChatChannel) Start(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = true
	c.mu.Unlock()
	if c.accessToken != "" {
		space := c.spaceID
		if space == "" {
			space = "(any)"
		}
		log.Printf("google_chat: active (space=%s, inbound=webhook)", space)
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
	if c.accessToken == "" {
		return true, nil
	}
	space := c.spaceName(target)
	if space == "" {
		return false, nil
	}
	status, err := jsonPOST(ctx, c.client,
		googleChatAPI+"/"+space+"/messages",
		map[string]string{"Authorization": "Bearer " + c.accessToken},
		map[string]any{"text": trimRunes(message, 4096)},
	)
	if err != nil {
		log.Printf("google_chat: send failed: %v", err)
		return false, err
	}
	return status == 200 || status == 201, nil
}

// VerifyInboundAuth requires Bearer matching access_token when configured.
func (c *GoogleChatChannel) VerifyInboundAuth(authorization string) bool {
	if c.accessToken == "" {
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
	expected := c.accessToken
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
