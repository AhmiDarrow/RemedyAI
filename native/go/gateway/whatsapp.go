package gateway

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

const whatsappGraphBase = "https://graph.facebook.com/v19.0"

// WhatsAppChannel: Cloud API outbound + webhook inbound (public HTTPS).
type WhatsAppChannel struct {
	gateway       *Gateway
	accessToken   string
	phoneNumberID string
	verifyToken   string
	appSecret     string
	allowed       map[string]struct{}
	allowAll      bool
	client        *http.Client

	mu      sync.Mutex
	running bool
}

// WhatsAppConfig configures a WhatsApp Cloud API adapter.
type WhatsAppConfig struct {
	AccessToken   string
	PhoneNumberID string
	VerifyToken   string
	AppSecret     string
	AllowFrom     any
	AllowAll      bool
}

// NewWhatsApp builds a WhatsApp channel bound to a gateway hub.
func NewWhatsApp(g *Gateway, cfg WhatsAppConfig) *WhatsAppChannel {
	return &WhatsAppChannel{
		gateway:       g,
		accessToken:   strings.TrimSpace(cfg.AccessToken),
		phoneNumberID: strings.TrimSpace(cfg.PhoneNumberID),
		verifyToken:   strings.TrimSpace(cfg.VerifyToken),
		appSecret:     strings.TrimSpace(cfg.AppSecret),
		allowed:       ParseIDs(cfg.AllowFrom),
		allowAll:      cfg.AllowAll,
		client:        &http.Client{Timeout: 30 * time.Second},
	}
}

func (c *WhatsAppChannel) Kind() ChannelKind { return ChannelWhatsApp }

func (c *WhatsAppChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *WhatsAppChannel) Start(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = true
	c.mu.Unlock()
	if c.accessToken != "" && c.phoneNumberID != "" {
		log.Printf("whatsapp: active (phone_number_id=%s, inbound=webhook)", c.phoneNumberID)
	} else {
		log.Printf("whatsapp: stub mode (missing token or phone_number_id)")
	}
	return nil
}

func (c *WhatsAppChannel) Stop(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	c.running = false
	c.mu.Unlock()
	return nil
}

func (c *WhatsAppChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.accessToken == "" || c.phoneNumberID == "" {
		return true, nil
	}
	to := strings.TrimPrefix(strings.TrimSpace(target), "+")
	if to == "" {
		return false, nil
	}
	status, err := jsonPOST(ctx, c.client,
		whatsappGraphBase+"/"+c.phoneNumberID+"/messages",
		map[string]string{"Authorization": "Bearer " + c.accessToken},
		map[string]any{
			"messaging_product": "whatsapp",
			"to":                to,
			"type":              "text",
			"text":              map[string]any{"body": trimRunes(message, 4096)},
		},
	)
	if err != nil {
		log.Printf("whatsapp: send failed: %v", err)
		return false, err
	}
	return status == 200 || status == 201, nil
}

// VerifyWebhookChallenge handles Meta hub.mode / hub.verify_token / hub.challenge.
func (c *WhatsAppChannel) VerifyWebhookChallenge(mode, token, challenge string) (string, bool) {
	if mode != "subscribe" {
		return "", false
	}
	presented := strings.TrimSpace(token)
	expected := strings.TrimSpace(c.verifyToken)
	if presented == "" || expected == "" {
		return "", false
	}
	if len(presented) != len(expected) {
		return "", false
	}
	if !hmac.Equal([]byte(presented), []byte(expected)) {
		return "", false
	}
	return challenge, true
}

// VerifySignature checks X-Hub-Signature-256 (fail-closed without app_secret).
func (c *WhatsAppChannel) VerifySignature(body []byte, signatureHeader string) bool {
	if c.appSecret == "" {
		log.Printf("whatsapp: webhook POST rejected: app_secret not configured")
		return false
	}
	if !strings.HasPrefix(signatureHeader, "sha256=") {
		return false
	}
	expected := strings.TrimPrefix(signatureHeader, "sha256=")
	mac := hmac.New(sha256.New, []byte(c.appSecret))
	_, _ = mac.Write(body)
	digest := hex.EncodeToString(mac.Sum(nil))
	if len(digest) != len(expected) {
		return false
	}
	return hmac.Equal([]byte(digest), []byte(expected))
}

// HandleWebhookPayload parses Cloud API webhook JSON; returns count handled.
func (c *WhatsAppChannel) HandleWebhookPayload(ctx context.Context, data map[string]any) int {
	n := 0
	entries, _ := data["entry"].([]any)
	for _, entryRaw := range entries {
		entry, _ := entryRaw.(map[string]any)
		if entry == nil {
			continue
		}
		changes, _ := entry["changes"].([]any)
		for _, changeRaw := range changes {
			change, _ := changeRaw.(map[string]any)
			if change == nil {
				continue
			}
			value, _ := change["value"].(map[string]any)
			if value == nil {
				continue
			}
			messages, _ := value["messages"].([]any)
			for _, msgRaw := range messages {
				msg, _ := msgRaw.(map[string]any)
				if msg == nil {
					continue
				}
				if anyString(msg["type"]) != "text" {
					continue
				}
				textObj, _ := msg["text"].(map[string]any)
				text := ""
				if textObj != nil {
					text = strings.TrimSpace(anyString(textObj["body"]))
				}
				if text == "" {
					continue
				}
				sender := anyString(msg["from"])
				if !IsAllowed(c.allowed, c.allowAll, sender) {
					continue
				}
				c.emitMessage(ctx, text, sender)
				n++
			}
		}
	}
	return n
}

func (c *WhatsAppChannel) emitMessage(ctx context.Context, text, sender string) {
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelWhatsApp,
		SourceID:  sender,
		SessionID: sender,
		Payload: map[string]any{
			"message":    text,
			"chat_id":    sender,
			"channel_id": sender,
			"user_id":    sender,
			"username":   sender,
		},
		Raw: trimRunes(text, 500),
		At:  time.Now().UTC(),
	}
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(ev)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, ev)
	}
}

// DecodeJSONObject parses JSON object bytes (empty → {}).
func DecodeJSONObject(body []byte) (map[string]any, error) {
	s := strings.TrimSpace(string(body))
	if s == "" {
		return map[string]any{}, nil
	}
	var data any
	dec := json.NewDecoder(strings.NewReader(s))
	dec.UseNumber()
	if err := dec.Decode(&data); err != nil {
		return nil, err
	}
	if data == nil {
		return map[string]any{}, nil
	}
	m, ok := data.(map[string]any)
	if !ok {
		return nil, errInvalidJSONObject
	}
	return m, nil
}

var errInvalidJSONObject = errors.New("invalid json object")
