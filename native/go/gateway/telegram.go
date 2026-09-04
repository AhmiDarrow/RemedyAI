package gateway

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

// TelegramChannel long-polls getUpdates and sends via sendMessage.
type TelegramChannel struct {
	gateway  *Gateway
	token    string
	home     string
	allowed  map[string]struct{}
	allowAll bool
	apiBase  string
	client   *http.Client

	mu            sync.Mutex
	running       bool
	pollActive    bool
	cancel        context.CancelFunc
	wg            sync.WaitGroup
	lock          *PollLock
	lastUpdateID  int
	conflictUntil time.Time
	lastErrLog    time.Time
	pollStartedAt time.Time
}

// TelegramConfig configures a Telegram adapter.
type TelegramConfig struct {
	BotToken     string
	AllowChatIDs any
	AllowAll     bool
	HomeDir      string
}

// NewTelegram builds a Telegram channel bound to a gateway hub.
func NewTelegram(g *Gateway, cfg TelegramConfig) *TelegramChannel {
	tok := strings.TrimSpace(cfg.BotToken)
	return &TelegramChannel{
		gateway:  g,
		token:    tok,
		home:     cfg.HomeDir,
		allowed:  ParseIDs(cfg.AllowChatIDs),
		allowAll: cfg.AllowAll,
		apiBase:  "https://api.telegram.org/bot" + tok,
		client: &http.Client{
			Timeout: 60 * time.Second,
		},
	}
}

func (c *TelegramChannel) Kind() ChannelKind { return ChannelTelegram }

func (c *TelegramChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *TelegramChannel) Start(ctx context.Context) error {
	c.mu.Lock()
	if c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = true
	runCtx, cancel := context.WithCancel(ctx)
	c.cancel = cancel
	c.mu.Unlock()

	if c.token == "" {
		log.Printf("telegram: stub mode (no token)")
		return nil
	}
	log.Printf("telegram: active (allowlist=%d allow_all=%v)", len(c.allowed), c.allowAll)
	if !c.tryStartPoller(runCtx) {
		log.Printf("telegram: long-poll deferred — another process holds the bot lock; retrying")
		c.wg.Add(1)
		go c.lockRetryLoop(runCtx)
	}
	return nil
}

func (c *TelegramChannel) Stop(ctx context.Context) error {
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

func (c *TelegramChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.token == "" {
		return true, nil
	}
	chatID := strings.TrimSpace(target)
	if chatID == "" {
		for id := range c.allowed {
			chatID = id
			break
		}
	}
	if chatID == "" {
		return false, nil
	}
	body := map[string]any{
		"chat_id": chatID,
		"text":    trimRunes(message, 4096),
	}
	status, _, err := c.apiPost(ctx, "sendMessage", body)
	if err != nil {
		log.Printf("telegram: send failed: %s", SafeErr(err))
		return false, err
	}
	return status == 200, nil
}

func (c *TelegramChannel) SendTyping(ctx context.Context, target string) error {
	if c.token == "" || strings.TrimSpace(target) == "" {
		return nil
	}
	_, _, _ = c.apiPost(ctx, "sendChatAction", map[string]any{
		"chat_id": target,
		"action":  "typing",
	})
	return nil
}

func (c *TelegramChannel) tryStartPoller(ctx context.Context) bool {
	c.mu.Lock()
	if c.pollActive {
		c.mu.Unlock()
		return true
	}
	if c.lock != nil && c.lock.Held {
		// Lock held but poller not marked active — fall through to start loop.
	} else {
		if c.lock != nil {
			c.lock.Release()
			c.lock = nil
		}
		c.mu.Unlock()
		lock := NewPollLock(c.home, "telegram")
		if !lock.TryAcquire() {
			return false
		}
		c.mu.Lock()
		c.lock = lock
	}
	c.pollStartedAt = time.Now()
	c.lastUpdateID = LoadUpdateOffset(c.home, "telegram")
	c.pollActive = true
	c.mu.Unlock()

	if c.lastUpdateID > 0 {
		log.Printf("telegram: resume offset update_id=%d", c.lastUpdateID)
	}
	_, _, _ = c.apiPost(ctx, "deleteWebhook", map[string]any{"drop_pending_updates": false})
	if c.lastUpdateID <= 0 {
		c.drainBacklog(ctx)
	}
	c.wg.Add(1)
	go c.pollLoop(ctx)
	log.Printf("telegram: long-poll task scheduled")
	return true
}

func (c *TelegramChannel) lockRetryLoop(ctx context.Context) {
	defer c.wg.Done()
	delay := 2 * time.Second
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
		if delay < 20*time.Second {
			delay *= 2
			if delay > 20*time.Second {
				delay = 20 * time.Second
			}
		}
		if c.tryStartPoller(ctx) {
			log.Printf("telegram: long-poll acquired after retry")
			return
		}
	}
}

func (c *TelegramChannel) pollLoop(ctx context.Context) {
	defer c.wg.Done()
	defer func() {
		c.mu.Lock()
		c.pollActive = false
		c.mu.Unlock()
	}()
	hbDone := make(chan struct{})
	go func() {
		defer close(hbDone)
		t := time.NewTicker(20 * time.Second)
		defer t.Stop()
		for {
			select {
			case <-ctx.Done():
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
	defer func() { <-hbDone }()

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}
		now := time.Now()
		c.mu.Lock()
		until := c.conflictUntil
		c.mu.Unlock()
		if now.Before(until) {
			wait := time.Until(until)
			if wait > 5*time.Second {
				wait = 5 * time.Second
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(wait):
			}
			continue
		}
		updates, err := c.getUpdates(ctx, 25)
		if err != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
			continue
		}
		for _, u := range updates {
			c.handleUpdate(ctx, u)
		}
	}
}

func (c *TelegramChannel) drainBacklog(ctx context.Context) {
	drained := 0
	for i := 0; i < 50; i++ {
		batch, err := c.getUpdates(ctx, 0)
		if err != nil || len(batch) == 0 {
			break
		}
		for _, u := range batch {
			if id, ok := asInt(u["update_id"]); ok {
				if id > c.lastUpdateID {
					c.lastUpdateID = id
				}
				drained++
			}
		}
		SaveUpdateOffset(c.home, "telegram", c.lastUpdateID)
	}
	if drained > 0 {
		log.Printf("telegram: drained %d backlog update(s); resume at update_id=%d", drained, c.lastUpdateID)
	}
}

func (c *TelegramChannel) getUpdates(ctx context.Context, timeout int) ([]map[string]any, error) {
	offset := 0
	c.mu.Lock()
	if c.lastUpdateID > 0 {
		offset = c.lastUpdateID + 1
	}
	c.mu.Unlock()

	q := url.Values{}
	q.Set("timeout", strconv.Itoa(timeout))
	q.Set("offset", strconv.Itoa(offset))
	q.Set("allowed_updates", `["message","edited_message"]`)

	reqCtx := ctx
	var cancel context.CancelFunc
	if timeout > 0 {
		reqCtx, cancel = context.WithTimeout(ctx, time.Duration(timeout+10)*time.Second)
		defer cancel()
	}
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, c.apiBase+"/getUpdates?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := c.client.Do(req)
	if err != nil {
		now := time.Now()
		c.mu.Lock()
		if now.Sub(c.lastErrLog) > 30*time.Second {
			log.Printf("telegram: getUpdates network: %s", SafeErr(err))
			c.lastErrLog = now
		}
		c.mu.Unlock()
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 4<<20))
	if resp.StatusCode != 200 {
		c.handlePollError(resp.StatusCode, string(raw))
		return nil, fmt.Errorf("telegram getUpdates %d", resp.StatusCode)
	}
	var data struct {
		OK     bool             `json:"ok"`
		Result []map[string]any `json:"result"`
	}
	if err := json.Unmarshal(raw, &data); err != nil || !data.OK {
		return nil, err
	}
	return data.Result, nil
}

func (c *TelegramChannel) handlePollError(status int, text string) {
	now := time.Now()
	c.mu.Lock()
	defer c.mu.Unlock()
	if status == 404 {
		if now.Sub(c.lastErrLog) > 60*time.Second {
			log.Printf("telegram: getUpdates 404 — bot token invalid/revoked. %s", SafeErr(text))
			c.lastErrLog = now
		}
		return
	}
	if status == 409 {
		wait, insist := telegram409Backoff(now, c.pollStartedAt)
		c.conflictUntil = now.Add(wait)
		if now.Sub(c.lastErrLog) > 20*time.Second {
			extra := ""
			if insist {
				extra = " (takeover)"
			}
			log.Printf("telegram: getUpdates 409 (another poller). Backing off %s%s.", wait, extra)
			c.lastErrLog = now
		}
		return
	}
	if now.Sub(c.lastErrLog) > 15*time.Second {
		log.Printf("telegram: getUpdates %d: %s", status, SafeErr(text))
		c.lastErrLog = now
	}
}

func telegram409Backoff(now, pollStartedAt time.Time) (time.Duration, bool) {
	insist := !pollStartedAt.IsZero() && now.Sub(pollStartedAt) < 90*time.Second
	if insist {
		// 2s + jitter from fractional second
		jitter := time.Duration((now.UnixNano()%1700))*time.Millisecond
		return 2*time.Second + jitter, true
	}
	return 25 * time.Second, false
}

func (c *TelegramChannel) handleUpdate(ctx context.Context, update map[string]any) {
	if id, ok := asInt(update["update_id"]); ok {
		c.mu.Lock()
		if id > c.lastUpdateID {
			c.lastUpdateID = id
		}
		offset := c.lastUpdateID
		c.mu.Unlock()
		SaveUpdateOffset(c.home, "telegram", offset)
	}
	msg, _ := update["message"].(map[string]any)
	if msg == nil {
		msg, _ = update["edited_message"].(map[string]any)
	}
	if msg == nil {
		return
	}
	text, _ := msg["text"].(string)
	text = strings.TrimSpace(text)
	if text == "" {
		return
	}
	from, _ := msg["from"].(map[string]any)
	if isBot, _ := from["is_bot"].(bool); isBot {
		return
	}
	chat, _ := msg["chat"].(map[string]any)
	chatID := fmt.Sprint(chat["id"])
	if chatID == "<nil>" {
		chatID = ""
	}
	userID := fmt.Sprint(from["id"])
	if userID == "<nil>" {
		userID = ""
	}
	allowAll := c.allowAll || EnvAllowAll("REMEDY_TELEGRAM_ALLOW_ALL")
	if !IsAllowed(c.allowed, allowAll, chatID, userID) {
		log.Printf("telegram: ignore chat_id=%s user_id=%s (allowlist)", chatID, userID)
		return
	}
	go func() { _ = c.SendTyping(context.Background(), chatID) }()

	username, _ := from["username"].(string)
	sourceID := userID
	if sourceID == "" {
		sourceID = chatID
	}
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelTelegram,
		SourceID:  sourceID,
		SessionID: chatID,
		Payload: map[string]any{
			"message":  text,
			"chat_id":  chatID,
			"user_id":  userID,
			"username": username,
		},
		Raw: trimRunes(fmt.Sprint(update), 1200),
		At:  time.Now().UTC(),
	}
	log.Printf("telegram: inbound chat_id=%s user=%s len=%d", chatID, firstNonEmpty(username, userID), len(text))
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(ev)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, ev)
	}
}

func (c *TelegramChannel) apiPost(ctx context.Context, method string, body map[string]any) (int, []byte, error) {
	payload, err := json.Marshal(body)
	if err != nil {
		return 0, nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.apiBase+"/"+method, strings.NewReader(string(payload)))
	if err != nil {
		return 0, nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return 0, nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, raw, nil
}

func asInt(v any) (int, bool) {
	switch t := v.(type) {
	case float64:
		return int(t), true
	case int:
		return t, true
	case int64:
		return int(t), true
	case json.Number:
		n, err := t.Int64()
		return int(n), err == nil
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(t))
		return n, err == nil
	default:
		return 0, false
	}
}

func trimRunes(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n]
}

func firstNonEmpty(a, b string) string {
	if strings.TrimSpace(a) != "" {
		return a
	}
	return b
}
