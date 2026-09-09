package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
)

// SlackChannel: Socket Mode inbound + chat.postMessage outbound.
type SlackChannel struct {
	gateway  *Gateway
	botToken string
	appToken string
	home     string
	channel  string
	access   Access
	client   *http.Client

	mu        sync.Mutex
	running   bool
	cancel    context.CancelFunc
	wg        sync.WaitGroup
	lock      *PollLock
	seen      map[string]struct{}
	seenOrder []string
}

// SlackConfig configures a Slack adapter.
type SlackConfig struct {
	BotToken  string
	AppToken  string
	ChannelID string
	AllowIDs  any
	AllowAll  bool
	HomeDir   string
}

// NewSlack builds a Slack channel bound to a gateway hub.
func NewSlack(g *Gateway, cfg SlackConfig) *SlackChannel {
	chID := strings.TrimSpace(cfg.ChannelID)
	return &SlackChannel{
		gateway:  g,
		botToken: strings.TrimSpace(cfg.BotToken),
		appToken: strings.TrimSpace(cfg.AppToken),
		home:     cfg.HomeDir,
		channel:  chID,
		access:   NewAccess(cfg.AllowIDs, cfg.AllowAll, chID),
		client:   &http.Client{Timeout: 30 * time.Second},
		seen:     make(map[string]struct{}),
	}
}

func (c *SlackChannel) Kind() ChannelKind { return ChannelSlack }

func (c *SlackChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *SlackChannel) Start(ctx context.Context) error {
	c.mu.Lock()
	if c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = true
	runCtx, cancel := context.WithCancel(ctx)
	c.cancel = cancel
	c.mu.Unlock()

	if c.botToken == "" {
		log.Printf("slack: stub mode (no bot token)")
		return nil
	}
	log.Printf("slack: active (channel=%s)", c.channel)
	if c.appToken == "" {
		log.Printf("slack: outbound only (set app_token for Socket Mode inbound)")
		return nil
	}
	if !c.tryStartSocket(runCtx) {
		log.Printf("slack: Socket Mode deferred — another process holds the bot lock; retrying")
		c.wg.Add(1)
		go c.lockRetryLoop(runCtx)
	}
	return nil
}

func (c *SlackChannel) Stop(ctx context.Context) error {
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

func (c *SlackChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.botToken == "" {
		return true, nil
	}
	ch := strings.TrimSpace(target)
	if ch == "" {
		ch = c.channel
	}
	if ch == "" {
		return false, nil
	}
	status, body, err := c.apiJSON(ctx, http.MethodPost, "https://slack.com/api/chat.postMessage", c.botToken,
		map[string]any{"channel": ch, "text": trimRunes(message, 3000)})
	if err != nil {
		log.Printf("slack: send failed: %s", SafeErr(err))
		return false, err
	}
	if status != 200 {
		return false, nil
	}
	var data struct {
		OK bool `json:"ok"`
	}
	_ = json.Unmarshal(body, &data)
	return data.OK, nil
}

func (c *SlackChannel) SendTyping(ctx context.Context, target string) error {
	ch := strings.TrimSpace(target)
	if ch == "" {
		ch = c.channel
	}
	if c.botToken == "" || ch == "" {
		return nil
	}
	_, _, _ = c.apiJSON(ctx, http.MethodPost, "https://slack.com/api/conversations.mark", c.botToken,
		map[string]any{"channel": ch})
	return nil
}

func (c *SlackChannel) tryStartSocket(ctx context.Context) bool {
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

	lock := NewPollLock(c.home, "slack")
	if !lock.TryAcquire() {
		return false
	}
	c.mu.Lock()
	c.lock = lock
	c.mu.Unlock()
	c.wg.Add(1)
	go c.socketLoop(ctx)
	log.Printf("slack: Socket Mode task scheduled")
	return true
}

func (c *SlackChannel) lockRetryLoop(ctx context.Context) {
	defer c.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(20 * time.Second):
		}
		if c.tryStartSocket(ctx) {
			log.Printf("slack: Socket Mode acquired after retry")
			return
		}
	}
}

func (c *SlackChannel) socketLoop(ctx context.Context) {
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
			log.Printf("slack: socket error: %s", SafeErr(err))
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
		}
	}
}

func (c *SlackChannel) socketOnce(ctx context.Context) error {
	_, body, err := c.apiJSON(ctx, http.MethodPost, "https://slack.com/api/apps.connections.open", c.appToken, nil)
	if err != nil {
		return err
	}
	var open struct {
		OK  bool   `json:"ok"`
		URL string `json:"url"`
	}
	if err := json.Unmarshal(body, &open); err != nil {
		return err
	}
	if !open.OK || open.URL == "" {
		log.Printf("slack: Socket Mode open failed: %s", trimRunes(string(body), 160))
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(5 * time.Second):
		}
		return nil
	}

	ws, err := dialWS(open.URL, nil, 20*time.Second)
	if err != nil {
		return err
	}
	defer ws.Close()
	log.Printf("slack: Socket Mode connected")

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
		var envelope map[string]any
		if err := json.Unmarshal(payload, &envelope); err != nil {
			continue
		}
		c.onSocket(ctx, ws, envelope)
	}
}

func (c *SlackChannel) onSocket(ctx context.Context, ws *wsConn, payload map[string]any) {
	ptype, _ := payload["type"].(string)
	if ptype == "hello" {
		return
	}
	if eid, _ := payload["envelope_id"].(string); eid != "" {
		ack, _ := json.Marshal(map[string]any{"envelope_id": eid})
		_ = ws.WriteText(ack)
	}
	if ptype != "events_api" {
		return
	}
	inner, _ := payload["payload"].(map[string]any)
	event, _ := inner["event"].(map[string]any)
	c.handleEvent(ctx, event)
}

func (c *SlackChannel) handleEvent(ctx context.Context, event map[string]any) {
	if event == nil {
		return
	}
	etype, _ := event["type"].(string)
	if etype != "message" {
		return
	}
	if subtype := anyString(event["subtype"]); subtype != "" {
		return
	}
	if botID := anyString(event["bot_id"]); botID != "" {
		return
	}
	if event["user"] == nil {
		return
	}
	text := strings.TrimSpace(anyString(event["text"]))
	if text == "" {
		return
	}
	ch := anyString(event["channel"])
	user := anyString(event["user"])
	eid := anyString(event["client_msg_id"])
	if eid == "" {
		eid = anyString(event["ts"])
	}
	if eid != "" && c.rememberSeen(eid) {
		return
	}
	if ok, reason := c.access.Permit(user, ch); !ok {
		logDeny(ChannelSlack, reason, user, ch)
		if c.gateway != nil {
			c.gateway.recordDenied(ChannelSlack, reason, user, ch)
		}
		return
	}
	sourceID := user
	if sourceID == "" {
		sourceID = ch
	}
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelSlack,
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

func (c *SlackChannel) rememberSeen(id string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if _, ok := c.seen[id]; ok {
		return true
	}
	c.seen[id] = struct{}{}
	c.seenOrder = append(c.seenOrder, id)
	for len(c.seenOrder) > 500 {
		old := c.seenOrder[0]
		c.seenOrder = c.seenOrder[1:]
		delete(c.seen, old)
	}
	return false
}

func (c *SlackChannel) apiJSON(ctx context.Context, method, url, token string, body any) (int, []byte, error) {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return 0, nil, err
		}
		reader = strings.NewReader(string(raw))
	}
	req, err := http.NewRequestWithContext(ctx, method, url, reader)
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, raw, nil
}

func anyString(v any) string {
	if v == nil {
		return ""
	}
	switch t := v.(type) {
	case string:
		return strings.TrimSpace(t)
	default:
		s := strings.TrimSpace(fmt.Sprint(t))
		if s == "<nil>" || s == "null" {
			return ""
		}
		return s
	}
}
