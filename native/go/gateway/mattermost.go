package gateway

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

// MattermostChannel: WebSocket inbound + REST posts outbound.
type MattermostChannel struct {
	gateway   *Gateway
	token     string
	baseURL   string
	channelID string
	teamID    string
	home      string
	allowed   map[string]struct{}
	allowAll  bool
	client    *http.Client

	mu      sync.Mutex
	running bool
	cancel  context.CancelFunc
	wg      sync.WaitGroup
	lock    *PollLock
	seq     int
}

// MattermostConfig configures a Mattermost adapter.
type MattermostConfig struct {
	BotToken  string
	BaseURL   string
	ChannelID string
	TeamID    string
	AllowIDs  any
	AllowAll  bool
	HomeDir   string
}

// NewMattermost builds a Mattermost channel bound to a gateway hub.
func NewMattermost(g *Gateway, cfg MattermostConfig) *MattermostChannel {
	allowed := ParseIDs(cfg.AllowIDs)
	chID := strings.TrimSpace(cfg.ChannelID)
	if chID != "" {
		allowed[chID] = struct{}{}
	}
	return &MattermostChannel{
		gateway:   g,
		token:     strings.TrimSpace(cfg.BotToken),
		baseURL:   strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/"),
		channelID: chID,
		teamID:    strings.TrimSpace(cfg.TeamID),
		home:      cfg.HomeDir,
		allowed:   allowed,
		allowAll:  cfg.AllowAll,
		client:    &http.Client{Timeout: 30 * time.Second},
		seq:       1,
	}
}

func (c *MattermostChannel) Kind() ChannelKind { return ChannelMattermost }

func (c *MattermostChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *MattermostChannel) Start(ctx context.Context) error {
	c.mu.Lock()
	if c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = true
	runCtx, cancel := context.WithCancel(ctx)
	c.cancel = cancel
	c.mu.Unlock()

	if c.token == "" || c.baseURL == "" {
		log.Printf("mattermost: stub mode (missing token or base_url)")
		return nil
	}
	log.Printf("mattermost: active (channel=%s)", c.channelID)
	if !c.tryStartSocket(runCtx) {
		log.Printf("mattermost: WebSocket deferred — another process holds the bot lock; retrying")
		c.wg.Add(1)
		go c.lockRetryLoop(runCtx)
	}
	return nil
}

func (c *MattermostChannel) Stop(ctx context.Context) error {
	_ = ctx
	c.mu.Lock()
	if !c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = false
	cancel := c.cancel
	c.cancel = nil
	lock := c.lock
	c.lock = nil
	c.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	c.wg.Wait()
	if lock != nil {
		lock.Release()
	}
	return nil
}

func (c *MattermostChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.token == "" || c.baseURL == "" {
		return true, nil
	}
	ch := strings.TrimSpace(target)
	if ch == "" {
		ch = c.channelID
	}
	if ch == "" {
		return false, nil
	}
	raw, err := json.Marshal(map[string]any{
		"channel_id": ch,
		"message":    trimRunes(message, 4000),
	})
	if err != nil {
		return false, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/api/v4/posts", strings.NewReader(string(raw)))
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		log.Printf("mattermost: send failed: %s", SafeErr(err))
		return false, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode == 200 || resp.StatusCode == 201, nil
}

func (c *MattermostChannel) SendTyping(ctx context.Context, target string) error {
	_ = ctx
	_ = target
	return nil
}

func (c *MattermostChannel) wsURL() string {
	u, err := url.Parse(c.baseURL)
	if err != nil {
		return ""
	}
	scheme := "wss"
	if u.Scheme == "http" {
		scheme = "ws"
	}
	host := u.Host
	if host == "" {
		host = u.Path
	}
	return scheme + "://" + host + "/api/v4/websocket"
}

func (c *MattermostChannel) tryStartSocket(ctx context.Context) bool {
	c.mu.Lock()
	if c.lock != nil && c.lock.Held {
		c.mu.Unlock()
		return true
	}
	if c.lock != nil {
		c.lock.Release()
		c.lock = nil
	}
	c.mu.Unlock()

	lock := NewPollLock(c.home, "mattermost")
	if !lock.TryAcquire() {
		return false
	}
	c.mu.Lock()
	c.lock = lock
	c.mu.Unlock()
	c.wg.Add(1)
	go c.socketLoop(ctx)
	log.Printf("mattermost: WebSocket task scheduled")
	return true
}

func (c *MattermostChannel) lockRetryLoop(ctx context.Context) {
	defer c.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(20 * time.Second):
		}
		if c.tryStartSocket(ctx) {
			log.Printf("mattermost: WebSocket acquired after retry")
			return
		}
	}
}

func (c *MattermostChannel) socketLoop(ctx context.Context) {
	defer c.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		c.mu.Lock()
		lock := c.lock
		c.mu.Unlock()
		if lock != nil {
			lock.Heartbeat()
		}
		if err := c.socketOnce(ctx); err != nil {
			if ctx.Err() != nil {
				return
			}
			log.Printf("mattermost: WS error: %s", SafeErr(err))
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
		}
	}
}

func (c *MattermostChannel) socketOnce(ctx context.Context) error {
	wsURL := c.wsURL()
	if wsURL == "" {
		return nil
	}
	ws, err := dialWS(wsURL, nil, 20*time.Second)
	if err != nil {
		return err
	}
	defer ws.Close()

	c.mu.Lock()
	seq := c.seq
	c.seq++
	c.mu.Unlock()
	auth, _ := json.Marshal(map[string]any{
		"seq":    seq,
		"action": "authentication_challenge",
		"data":   map[string]any{"token": c.token},
	})
	if err := ws.WriteText(auth); err != nil {
		return err
	}
	log.Printf("mattermost: WebSocket connected")

	done := make(chan struct{})
	defer close(done)
	go func() {
		t := time.NewTicker(20 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-done:
				return
			case <-t.C:
				c.mu.Lock()
				lock := c.lock
				c.mu.Unlock()
				if lock != nil {
					lock.Heartbeat()
				}
			}
		}
	}()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		_ = ws.conn.SetReadDeadline(time.Now().Add(120 * time.Second))
		op, payload, err := ws.ReadMessage()
		if err != nil {
			return err
		}
		if op != 0x1 {
			continue
		}
		var data map[string]any
		if err := json.Unmarshal(payload, &data); err != nil {
			continue
		}
		c.onEvent(ctx, data)
	}
}

func (c *MattermostChannel) onEvent(ctx context.Context, data map[string]any) {
	if anyString(data["event"]) != "posted" {
		return
	}
	inner, _ := data["data"].(map[string]any)
	postRaw := anyString(inner["post"])
	if postRaw == "" {
		return
	}
	var post map[string]any
	if err := json.Unmarshal([]byte(postRaw), &post); err != nil || post == nil {
		return
	}
	// props may be null; only skip when from_bot is explicitly set.
	if props, ok := post["props"].(map[string]any); ok {
		if fromBot, _ := props["from_bot"].(bool); fromBot {
			return
		}
		if anyString(props["from_bot"]) == "true" {
			return
		}
	}
	text := strings.TrimSpace(anyString(post["message"]))
	if text == "" {
		return
	}
	ch := anyString(post["channel_id"])
	user := anyString(post["user_id"])
	if !IsAllowed(c.allowed, c.allowAll, ch, user, c.teamID) {
		return
	}
	sourceID := user
	if sourceID == "" {
		sourceID = ch
	}
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelMattermost,
		SourceID:  sourceID,
		SessionID: ch,
		Payload: map[string]any{
			"message":  text,
			"chat_id":  ch,
			"user_id":  user,
			"username": user,
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
