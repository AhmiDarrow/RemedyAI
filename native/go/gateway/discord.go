package gateway

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"log"
	"math/rand"
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

	discordOpDispatch       = 0
	discordOpHeartbeat      = 1
	discordOpIdentify       = 2
	discordOpResume         = 6
	discordOpReconnect      = 7
	discordOpInvalidSession = 9
	discordOpHello          = 10
	discordOpHeartbeatACK   = 11

	discordBackoffMin = time.Second
	discordBackoffMax = 60 * time.Second
	// discordFatalBackoff is used after a close code that must not be retried
	// blindly (bad token, bad intents, sharding). The loop keeps running so a
	// fixed token after a settings reload is picked up eventually.
	discordFatalBackoff = 5 * time.Minute
)

// discordInvalidSessionWait is the pause Discord requires before a new
// IDENTIFY after INVALID SESSION. A var so tests need not sleep through it.
var discordInvalidSessionWait = func(rng *rand.Rand) time.Duration {
	return time.Second + time.Duration(rng.Int63n(int64(4*time.Second)))
}

var (
	errDiscordReconnect  = errors.New("discord: gateway asked for reconnect")
	errDiscordZombie     = errors.New("discord: heartbeat not acknowledged")
	errDiscordInvalidSes = errors.New("discord: invalid session")
	errDiscordFatalClose = errors.New("discord: non-resumable close")
)

// discordCloseCodeResumable classifies gateway close codes.
// ok=false means the connection must not be resumed; fatal=true means the
// bot cannot recover by reconnecting (token / intents / shards).
func discordCloseCodeResumable(code int) (resumable bool, fatal bool) {
	switch code {
	case 4004, 4010, 4011, 4012, 4013, 4014:
		return false, true
	case 4007, 4009:
		return false, false
	case 1000, 1001:
		return false, false
	default:
		return true, false
	}
}

// DiscordChannel connects via Gateway WS inbound + REST outbound.
type DiscordChannel struct {
	gateway   *Gateway
	token     string
	home      string
	channelID string
	guildID   string
	access    Access
	client    *http.Client

	mu          sync.Mutex
	running     bool
	cancel      context.CancelFunc
	wg          sync.WaitGroup
	lock        *PollLock
	seq         *int
	heartbeatMS int
	sessionID   string
	resumeURL   string
	backoff     time.Duration
	rng         *rand.Rand
}

// DiscordConfig configures a Discord adapter. ChannelID / GuildID are scope
// (where the bot answers); AllowIDs is the user identity allowlist.
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
	chID := strings.TrimSpace(cfg.ChannelID)
	guild := strings.TrimSpace(cfg.GuildID)
	return &DiscordChannel{
		gateway:     g,
		token:       tok,
		home:        cfg.HomeDir,
		channelID:   chID,
		guildID:     guild,
		access:      NewAccess(cfg.AllowIDs, cfg.AllowAll, chID, guild),
		client:      &http.Client{Timeout: 30 * time.Second},
		heartbeatMS: 41250,
		backoff:     discordBackoffMin,
		rng:         rand.New(rand.NewSource(time.Now().UnixNano())),
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
	log.Printf("discord: active (default_channel=%s allowlist=%d allow_all=%v)", c.channelID, len(c.access.Users), c.access.AllowAll)
	if !c.tryStartGateway(runCtx) {
		log.Printf("discord: gateway deferred: another process holds the bot lock; retrying")
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

	lock := NewPollLockForToken(c.home, "discord", c.token)
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

// nextBackoff returns the current delay with jitter and doubles the base.
func (c *DiscordChannel) nextBackoff(fatal bool) time.Duration {
	c.mu.Lock()
	defer c.mu.Unlock()
	base := c.backoff
	if fatal {
		base = discordFatalBackoff
	}
	if base < discordBackoffMin {
		base = discordBackoffMin
	}
	jitter := time.Duration(c.rng.Int63n(int64(base)/2 + 1))
	if !fatal {
		c.backoff = base * 2
		if c.backoff > discordBackoffMax {
			c.backoff = discordBackoffMax
		}
	}
	return base + jitter
}

func (c *DiscordChannel) resetBackoff() {
	c.mu.Lock()
	c.backoff = discordBackoffMin
	c.mu.Unlock()
}

func (c *DiscordChannel) clearSession() {
	c.mu.Lock()
	c.sessionID = ""
	c.resumeURL = ""
	c.seq = nil
	c.mu.Unlock()
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
		err := c.sessionOnce(ctx)
		if ctx.Err() != nil {
			return
		}
		fatal := errors.Is(err, errDiscordFatalClose)
		if err != nil {
			log.Printf("discord: gateway error: %s", SafeErr(err))
		}
		delay := c.nextBackoff(fatal)
		if errors.Is(err, errDiscordReconnect) {
			// Server-requested reconnect: resume promptly with light jitter.
			delay = 500*time.Millisecond + time.Duration(c.rng.Int63n(int64(time.Second)))
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(delay):
		}
	}
}

func (c *DiscordChannel) sessionOnce(ctx context.Context) error {
	c.mu.Lock()
	resumeURL := c.resumeURL
	sessionID := c.sessionID
	canResume := sessionID != "" && c.seq != nil
	c.mu.Unlock()

	dialURL := discordGatewayURL
	if canResume && resumeURL != "" {
		dialURL = resumeURL
		if !strings.Contains(dialURL, "?") {
			dialURL += "/?v=10&encoding=json"
		}
	}
	ws, err := dialWS(dialURL, nil, 20*time.Second)
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

	hbCtx, hbCancel := context.WithCancel(ctx)
	defer hbCancel()
	hb := &discordHeartbeat{ws: ws, acked: true}
	heartbeatStarted := false
	startedAt := time.Now()

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-hb.zombie():
			return errDiscordZombie
		default:
		}
		_ = ws.conn.SetReadDeadline(time.Now().Add(120 * time.Second))
		op, payload, err := ws.ReadMessage()
		if op == 0x8 {
			code := wsCloseCode(payload)
			resumable, fatal := discordCloseCodeResumable(code)
			if !resumable {
				c.clearSession()
			}
			if fatal {
				log.Printf("discord: gateway closed with code %d (check bot token / privileged intents)", code)
				return errDiscordFatalClose
			}
			log.Printf("discord: gateway closed with code %d (resumable=%v)", code, resumable)
			return errDiscordReconnect
		}
		if err != nil {
			select {
			case <-hb.zombie():
				return errDiscordZombie
			default:
			}
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

		switch opcode {
		case discordOpHello:
			d, _ := packet["d"].(map[string]any)
			if ms, ok := asInt(d["heartbeat_interval"]); ok && ms > 0 {
				c.mu.Lock()
				c.heartbeatMS = ms
				c.mu.Unlock()
			}
			if err := c.sendIdentifyOrResume(ws); err != nil {
				return err
			}
			if !heartbeatStarted {
				heartbeatStarted = true
				go c.heartbeatLoop(hbCtx, hb)
			}
		case discordOpHeartbeat:
			c.sendHeartbeat(ws, hb)
		case discordOpHeartbeatACK:
			hb.ack()
		case discordOpReconnect:
			// Session stays resumable; reconnect to the resume URL.
			return errDiscordReconnect
		case discordOpInvalidSession:
			resumable, _ := packet["d"].(bool)
			if !resumable {
				c.clearSession()
			}
			// Discord asks clients to wait 1-5 s before a new IDENTIFY.
			wait := discordInvalidSessionWait(c.rng)
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(wait):
			}
			return errDiscordInvalidSes
		case discordOpDispatch:
			d, _ := packet["d"].(map[string]any)
			switch t {
			case "READY":
				c.mu.Lock()
				if sid, _ := d["session_id"].(string); sid != "" {
					c.sessionID = sid
				}
				if ru, _ := d["resume_gateway_url"].(string); ru != "" {
					c.resumeURL = ru
				}
				c.mu.Unlock()
				c.resetBackoff()
				log.Printf("discord: gateway READY")
			case "RESUMED":
				c.resetBackoff()
				log.Printf("discord: gateway RESUMED")
			case "MESSAGE_CREATE":
				if time.Since(startedAt) > time.Minute {
					c.resetBackoff()
				}
				c.onMessage(ctx, d)
			}
		}
	}
}

func (c *DiscordChannel) sendIdentifyOrResume(ws *wsConn) error {
	c.mu.Lock()
	sessionID := c.sessionID
	var seq any
	if c.seq != nil {
		seq = *c.seq
	}
	c.mu.Unlock()
	var packet map[string]any
	if sessionID != "" && seq != nil {
		packet = map[string]any{
			"op": discordOpResume,
			"d": map[string]any{
				"token":      c.token,
				"session_id": sessionID,
				"seq":        seq,
			},
		}
	} else {
		packet = map[string]any{
			"op": discordOpIdentify,
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
	}
	raw, _ := json.Marshal(packet)
	return ws.WriteText(raw)
}

// discordHeartbeat tracks heartbeat acknowledgement for one WS session.
type discordHeartbeat struct {
	mu     sync.Mutex
	ws     *wsConn
	acked  bool
	zombCh chan struct{}
	zombed bool
}

func (h *discordHeartbeat) ack() {
	h.mu.Lock()
	h.acked = true
	h.mu.Unlock()
}

// zombie returns a channel that is closed once a heartbeat went unanswered.
func (h *discordHeartbeat) zombie() <-chan struct{} {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.zombCh == nil {
		h.zombCh = make(chan struct{})
	}
	return h.zombCh
}

func (h *discordHeartbeat) markZombie() {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.zombCh == nil {
		h.zombCh = make(chan struct{})
	}
	if !h.zombed {
		h.zombed = true
		close(h.zombCh)
	}
	// Wake the reader so the session loop observes the zombie state.
	_ = h.ws.conn.SetReadDeadline(time.Now())
}

func (c *DiscordChannel) sendHeartbeat(ws *wsConn, hb *discordHeartbeat) {
	c.mu.Lock()
	var seq any
	if c.seq != nil {
		seq = *c.seq
	}
	c.mu.Unlock()
	hb.mu.Lock()
	hb.acked = false
	hb.mu.Unlock()
	raw, _ := json.Marshal(map[string]any{"op": discordOpHeartbeat, "d": seq})
	_ = ws.WriteText(raw)
}

func (c *DiscordChannel) heartbeatLoop(ctx context.Context, hb *discordHeartbeat) {
	c.mu.Lock()
	interval := time.Duration(c.heartbeatMS) * time.Millisecond
	c.mu.Unlock()
	if interval <= 0 {
		interval = 41250 * time.Millisecond
	}
	// First heartbeat after interval * jitter (Discord recommendation).
	first := time.Duration(float64(interval) * c.rng.Float64())
	select {
	case <-ctx.Done():
		return
	case <-time.After(first):
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		hb.mu.Lock()
		acked := hb.acked
		hb.mu.Unlock()
		if !acked {
			hb.markZombie()
			return
		}
		c.mu.Lock()
		lock := c.lock
		c.mu.Unlock()
		if lock != nil {
			lock.Heartbeat()
		}
		c.sendHeartbeat(hb.ws, hb)
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// wsCloseCode extracts the status code from a close frame payload.
func wsCloseCode(payload []byte) int {
	if len(payload) < 2 {
		return 0
	}
	return int(binary.BigEndian.Uint16(payload[:2]))
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
	chID := anyString(d["channel_id"])
	userID := anyString(author["id"])
	guildID := anyString(d["guild_id"])
	if ok, reason := c.access.Permit(userID, chID, guildID); !ok {
		logDeny(ChannelDiscord, reason, userID, chID)
		c.gateway.recordDenied(ChannelDiscord, reason, userID, chID)
		return
	}
	go func() { _ = c.SendTyping(context.Background(), chID) }()
	username, _ := author["username"].(string)
	ev := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelDiscord,
		SourceID:  userID,
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
