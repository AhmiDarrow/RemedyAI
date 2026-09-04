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

const (
	discordAPI        = "https://discord.com/api/v10"
	discordGatewayURL = "wss://gateway.discord.gg/?v=10&encoding=json"
	// GUILDS | GUILD_MESSAGES | DIRECT_MESSAGES | MESSAGE_CONTENT
	discordIntents = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)
)

// DiscordChannel connects via Gateway WS inbound + REST outbound.
type DiscordChannel struct {
	gateway   *Gateway
	token     string
	home      string
	channelID string
	guildID   string
	allowed   map[string]struct{}
	allowAll  bool
	client    *http.Client

	mu          sync.Mutex
	running     bool
	cancel      context.CancelFunc
	wg          sync.WaitGroup
	lock        *PollLock
	seq         *int
	heartbeatMS int
	sessionID   string
}

// DiscordConfig configures a Discord adapter.
type DiscordConfig struct {
	BotToken  string
	ChannelID string
	GuildID   string
	AllowIDs  any
	AllowAll  bool
	HomeDir   string
}

// NewDiscord builds a Discord channel bound to a gateway hub.
func NewDiscord(g *Gateway, cfg DiscordConfig) *DiscordChannel {
	tok := strings.TrimSpace(cfg.BotToken)
	allowed := ParseIDs(cfg.AllowIDs)
	chID := strings.TrimSpace(cfg.ChannelID)
	if chID != "" {
		allowed[chID] = struct{}{}
	}
	return &DiscordChannel{
		gateway:     g,
		token:       tok,
		home:        cfg.HomeDir,
		channelID:   chID,
		guildID:     strings.TrimSpace(cfg.GuildID),
		allowed:     allowed,
		allowAll:    cfg.AllowAll,
		client:      &http.Client{Timeout: 30 * time.Second},
		heartbeatMS: 41250,
	}
}

func (c *DiscordChannel) Kind() ChannelKind { return ChannelDiscord }

func (c *DiscordChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *DiscordChannel) Start(ctx context.Context) error {
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
		log.Printf("discord: stub mode (no token)")
		return nil
	}
	log.Printf("discord: active (default_channel=%s)", c.channelID)
	if !c.tryStartGateway(runCtx) {
		log.Printf("discord: gateway deferred — another process holds the bot lock; retrying")
		c.wg.Add(1)
		go c.lockRetryLoop(runCtx)
	}
	return nil
}

func (c *DiscordChannel) Stop(ctx context.Context) error {
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

func (c *DiscordChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.token == "" {
		return true, nil
	}
	chID := strings.TrimSpace(target)
	if chID == "" {
		chID = c.channelID
	}
	if chID == "" {
		return false, nil
	}
	body, _ := json.Marshal(map[string]any{"content": trimRunes(message, 2000)})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		discordAPI+"/channels/"+chID+"/messages", strings.NewReader(string(body)))
	if err != nil {
		return false, err
	}
	req.Header.Set("Authorization", "Bot "+c.token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		log.Printf("discord: send failed: %s", SafeErr(err))
		return false, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode == 200 || resp.StatusCode == 201, nil
}

func (c *DiscordChannel) SendTyping(ctx context.Context, target string) error {
	chID := strings.TrimSpace(target)
	if chID == "" {
		chID = c.channelID
	}
	if c.token == "" || chID == "" {
		return nil
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost,
		discordAPI+"/channels/"+chID+"/typing", nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bot "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

func (c *DiscordChannel) tryStartGateway(ctx context.Context) bool {
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

	lock := NewPollLock(c.home, "discord")
	if !lock.TryAcquire() {
		return false
	}
	c.mu.Lock()
	c.lock = lock
	c.mu.Unlock()
	c.wg.Add(1)
	go c.gatewayLoop(ctx)
	log.Printf("discord: gateway task scheduled")
	return true
}

func (c *DiscordChannel) lockRetryLoop(ctx context.Context) {
	defer c.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(20 * time.Second):
		}
		if c.tryStartGateway(ctx) {
			log.Printf("discord: gateway acquired after retry")
			return
		}
	}
}

func (c *DiscordChannel) gatewayLoop(ctx context.Context) {
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
		if err := c.sessionOnce(ctx); err != nil {
			if ctx.Err() != nil {
				return
			}
			log.Printf("discord: gateway error: %s", SafeErr(err))
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
		}
	}
}

func (c *DiscordChannel) sessionOnce(ctx context.Context) error {
	ws, err := dialWS(discordGatewayURL, nil, 20*time.Second)
	if err != nil {
		return err
	}
	defer ws.Close()

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

	identifySent := false
	var hbCancel context.CancelFunc
	defer func() {
		if hbCancel != nil {
			hbCancel()
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
		if op != 0x1 { // text
			continue
		}
		var packet map[string]any
		if err := json.Unmarshal(payload, &packet); err != nil {
			continue
		}
		opcode, _ := asInt(packet["op"])
		t, _ := packet["t"].(string)
		if s, ok := asInt(packet["s"]); ok {
			c.mu.Lock()
			c.seq = &s
			c.mu.Unlock()
		}
		d, _ := packet["d"].(map[string]any)

		switch opcode {
		case 10: // Hello
			if ms, ok := asInt(d["heartbeat_interval"]); ok && ms > 0 {
				c.heartbeatMS = ms
			}
			if !identifySent {
				identify := map[string]any{
					"op": 2,
					"d": map[string]any{
						"token":   c.token,
						"intents": discordIntents,
						"properties": map[string]any{
							"os":      "windows",
							"browser": "remedy",
							"device":  "remedy",
						},
					},
				}
				raw, _ := json.Marshal(identify)
				if err := ws.WriteText(raw); err != nil {
					return err
				}
				identifySent = true
			}
			if hbCancel != nil {
				hbCancel()
			}
			var hbCtx context.Context
			hbCtx, hbCancel = context.WithCancel(ctx)
			go c.heartbeat(hbCtx, ws)
		case 0:
			if t == "READY" {
				if sid, _ := d["session_id"].(string); sid != "" {
					c.sessionID = sid
				}
				log.Printf("discord: gateway READY")
			} else if t == "MESSAGE_CREATE" {
				c.onMessage(ctx, d)
			}
		}
	}
}

func (c *DiscordChannel) heartbeat(ctx context.Context, ws *wsConn) {
	interval := time.Duration(c.heartbeatMS) * time.Millisecond
	if interval <= 0 {
		interval = 41250 * time.Millisecond
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			c.mu.Lock()
			lock := c.lock
			var seq any
			if c.seq != nil {
				seq = *c.seq
			}
			c.mu.Unlock()
			if lock != nil {
				lock.Heartbeat()
			}
			raw, _ := json.Marshal(map[string]any{"op": 1, "d": seq})
			_ = ws.WriteText(raw)
		}
	}
}

func (c *DiscordChannel) onMessage(ctx context.Context, d map[string]any) {
	author, _ := d["author"].(map[string]any)
	if bot, _ := author["bot"].(bool); bot {
		return
	}
	content, _ := d["content"].(string)
	content = strings.TrimSpace(content)
	if content == "" {
		return
	}
	chID := fmt.Sprint(d["channel_id"])
	userID := fmt.Sprint(author["id"])
	guildID := fmt.Sprint(d["guild_id"])
	if chID == "<nil>" {
		chID = ""
	}
	if userID == "<nil>" {
		userID = ""
	}
	if guildID == "<nil>" {
		guildID = ""
	}
	if !IsAllowed(c.allowed, c.allowAll, chID, userID, guildID) {
		return
	}
	go func() { _ = c.SendTyping(context.Background(), chID) }()
	username, _ := author["username"].(string)
	sourceID := userID
	if sourceID == "" {
		sourceID = chID
	}
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelDiscord,
		SourceID:  sourceID,
		SessionID: chID,
		Payload: map[string]any{
			"message":    content,
			"chat_id":    chID,
			"channel_id": chID,
			"user_id":    userID,
			"guild_id":   guildID,
			"username":   username,
		},
		Raw: trimRunes(content, 500),
		At:  time.Now().UTC(),
	}
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(ev)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, ev)
	}
}
