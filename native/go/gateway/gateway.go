package gateway

import (
	"context"
	"log"
	"sync"
	"time"
)

// Handler processes a gateway event. Prefer returning quickly; long work
// should be scheduled by the caller (httpapi turn runner).
type Handler func(ctx context.Context, ev Event) error

// Channel is a messenger (or internal) adapter owned by the gateway.
type Channel interface {
	Kind() ChannelKind
	Running() bool
	Start(ctx context.Context) error
	Stop(ctx context.Context) error
	Send(ctx context.Context, message, target string) (bool, error)
}

// TypingChannel optionally supports typing indicators.
type TypingChannel interface {
	Channel
	SendTyping(ctx context.Context, target string) error
}

// Config controls gateway hub defaults.
type Config struct {
	HeartbeatInterval time.Duration
	RateLimitPerMin   int
	HomeDir           string
}

// Stats is the public gateway snapshot (Python Gateway.stats /api/status).
type Stats struct {
	EventsProcessed int      `json:"events_processed"`
	ChannelsActive  int      `json:"channels_active"`
	Channels        []string `json:"channels"`
	Running         bool     `json:"running"`
	StartedAt       string   `json:"started_at,omitempty"`
	Uptime          string   `json:"uptime"`
	RateLimitPerMin int      `json:"rate_limit_per_min"`
}

// Gateway is the always-on multi-channel event hub.
type Gateway struct {
	mu sync.Mutex

	cfg      Config
	handlers []Handler
	channels map[ChannelKind]Channel

	running   bool
	startedAt time.Time
	events    int
	rateLimit int
	heartbeat time.Duration

	queue     chan Event
	cancel    context.CancelFunc
	wg        sync.WaitGroup
	rateBuckets map[ChannelKind][]time.Time
}

// New builds an idle gateway hub.
func New(cfg Config) *Gateway {
	if cfg.HeartbeatInterval <= 0 {
		cfg.HeartbeatInterval = 60 * time.Second
	}
	if cfg.RateLimitPerMin <= 0 {
		cfg.RateLimitPerMin = 60
	}
	return &Gateway{
		cfg:         cfg,
		channels:    make(map[ChannelKind]Channel),
		rateLimit:   cfg.RateLimitPerMin,
		heartbeat:   cfg.HeartbeatInterval,
		rateBuckets: make(map[ChannelKind][]time.Time),
	}
}

// RegisterChannel attaches an adapter (replaces same kind).
func (g *Gateway) RegisterChannel(ch Channel) {
	if g == nil || ch == nil {
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.channels[ch.Kind()] = ch
}

// RemoveChannel drops an adapter by kind (does not stop it).
func (g *Gateway) RemoveChannel(kind ChannelKind) {
	g.mu.Lock()
	defer g.mu.Unlock()
	delete(g.channels, kind)
}

// GetChannel returns a registered adapter.
func (g *Gateway) GetChannel(kind ChannelKind) Channel {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.channels[kind]
}

// Channels returns registered kinds.
func (g *Gateway) Channels() []ChannelKind {
	g.mu.Lock()
	defer g.mu.Unlock()
	out := make([]ChannelKind, 0, len(g.channels))
	for k := range g.channels {
		out = append(out, k)
	}
	return out
}

// RegisterHandler appends an event handler (idempotent by pointer equality is
// not available for funcs — callers should register once).
func (g *Gateway) RegisterHandler(h Handler) {
	if g == nil || h == nil {
		return
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	g.handlers = append(g.handlers, h)
}

// Running reports whether Start has completed and Stop has not.
func (g *Gateway) Running() bool {
	if g == nil {
		return false
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.running
}

// Stats returns the /api/status gateway object.
func (g *Gateway) Stats() Stats {
	if g == nil {
		return Stats{Running: false, Uptime: "0s"}
	}
	g.mu.Lock()
	defer g.mu.Unlock()
	chs := make([]string, 0, len(g.channels))
	for k := range g.channels {
		chs = append(chs, string(k))
	}
	st := Stats{
		EventsProcessed: g.events,
		ChannelsActive:  len(g.channels),
		Channels:        chs,
		Running:         g.running,
		Uptime:          formatUptime(time.Since(g.startedAt)),
		RateLimitPerMin: g.rateLimit,
	}
	if !g.startedAt.IsZero() {
		st.StartedAt = g.startedAt.UTC().Format(time.RFC3339)
	}
	if !g.running {
		st.Uptime = "0s"
	}
	return st
}

// StatsMap is the nested gateway object for JSON responses.
func (g *Gateway) StatsMap() map[string]any {
	st := g.Stats()
	return map[string]any{
		"events_processed":   st.EventsProcessed,
		"channels_active":    st.ChannelsActive,
		"channels":           st.Channels,
		"running":            st.Running,
		"started_at":         st.StartedAt,
		"uptime":             st.Uptime,
		"rate_limit_per_min": st.RateLimitPerMin,
	}
}

// Start launches the queue worker, heartbeat, and all registered channels.
func (g *Gateway) Start(ctx context.Context) error {
	g.mu.Lock()
	if g.running {
		g.mu.Unlock()
		return nil
	}
	runCtx, cancel := context.WithCancel(ctx)
	g.cancel = cancel
	g.queue = make(chan Event, 256)
	g.running = true
	g.startedAt = time.Now().UTC()
	channels := make([]Channel, 0, len(g.channels))
	for _, ch := range g.channels {
		channels = append(channels, ch)
	}
	g.mu.Unlock()

	g.wg.Add(2)
	go g.processQueue(runCtx)
	go g.heartbeatLoop(runCtx)

	for _, ch := range channels {
		if err := ch.Start(runCtx); err != nil {
			log.Printf("gateway: start %s: %v", ch.Kind(), err)
		}
	}
	log.Printf("gateway: started (heartbeat=%s rate_limit=%d/min channels=%d)",
		g.heartbeat, g.rateLimit, len(channels))
	return nil
}

// Stop cancels workers and stops every channel.
func (g *Gateway) Stop(ctx context.Context) error {
	g.mu.Lock()
	if !g.running {
		g.mu.Unlock()
		return nil
	}
	g.running = false
	cancel := g.cancel
	g.cancel = nil
	channels := make([]Channel, 0, len(g.channels))
	for _, ch := range g.channels {
		channels = append(channels, ch)
	}
	events := g.events
	g.mu.Unlock()

	if cancel != nil {
		cancel()
	}
	for _, ch := range channels {
		_ = ch.Stop(ctx)
	}
	g.wg.Wait()
	log.Printf("gateway: stopped (events=%d)", events)
	return nil
}

// Enqueue buffers an event for async handling (poll/WS must not block on turns).
func (g *Gateway) Enqueue(ev Event) {
	if g == nil {
		return
	}
	g.mu.Lock()
	running := g.running
	q := g.queue
	g.mu.Unlock()
	if !running || q == nil {
		_ = g.Emit(context.Background(), ev)
		return
	}
	select {
	case q <- ev:
	default:
		log.Printf("gateway: queue full; dropping %s from %s", ev.Kind, ev.Channel)
	}
}

// Emit runs handlers synchronously and increments the event counter.
func (g *Gateway) Emit(ctx context.Context, ev Event) error {
	if g == nil {
		return nil
	}
	if ev.ID == "" {
		ev.ID = NewEventID()
	}
	if ev.At.IsZero() {
		ev.At = time.Now().UTC()
	}
	if !g.checkRateLimit(ev.Channel) {
		log.Printf("gateway: rate limit exceeded for %s", ev.Channel)
		target := ""
		if ev.Payload != nil {
			if v, ok := ev.Payload["chat_id"].(string); ok {
				target = v
			} else if v, ok := ev.Payload["channel_id"].(string); ok {
				target = v
			}
		}
		if target == "" {
			target = ev.SessionID
		}
		_, _ = g.SendTo(ctx, ev.Channel,
			"You're sending messages too quickly. Wait a few seconds and try again.",
			target)
		return nil
	}

	g.mu.Lock()
	g.events++
	handlers := append([]Handler(nil), g.handlers...)
	g.mu.Unlock()

	for _, h := range handlers {
		if err := h(ctx, ev); err != nil {
			log.Printf("gateway: handler error for %s: %v", ev.ID, err)
		}
	}
	return nil
}

// SendTo delivers a message on one channel.
func (g *Gateway) SendTo(ctx context.Context, kind ChannelKind, message, target string) (bool, error) {
	ch := g.GetChannel(kind)
	if ch == nil {
		return false, nil
	}
	return ch.Send(ctx, message, target)
}

// SendTyping triggers a typing indicator when the channel supports it.
func (g *Gateway) SendTyping(ctx context.Context, kind ChannelKind, target string) {
	ch := g.GetChannel(kind)
	if tc, ok := ch.(TypingChannel); ok {
		_ = tc.SendTyping(ctx, target)
	}
}

func (g *Gateway) checkRateLimit(channel ChannelKind) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	now := time.Now()
	bucket := g.rateBuckets[channel]
	kept := bucket[:0]
	for _, t := range bucket {
		if now.Sub(t) < time.Minute {
			kept = append(kept, t)
		}
	}
	if len(kept) >= g.rateLimit {
		g.rateBuckets[channel] = kept
		return false
	}
	g.rateBuckets[channel] = append(kept, now)
	return true
}

func (g *Gateway) processQueue(ctx context.Context) {
	defer g.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case ev, ok := <-g.queue:
			if !ok {
				return
			}
			_ = g.Emit(ctx, ev)
		}
	}
}

func (g *Gateway) heartbeatLoop(ctx context.Context) {
	defer g.wg.Done()
	t := time.NewTicker(g.heartbeat)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			st := g.Stats()
			_ = g.Emit(ctx, Event{
				Kind:     EventHeartbeat,
				Channel:  ChannelCLI,
				SourceID: "system",
				Payload: map[string]any{
					"timestamp": time.Now().UTC().Format(time.RFC3339),
					"uptime":    st.Uptime,
					"events":    st.EventsProcessed,
				},
			})
		}
	}
}

func formatUptime(d time.Duration) string {
	if d < 0 {
		d = 0
	}
	secs := int(d.Seconds())
	h := secs / 3600
	m := (secs % 3600) / 60
	s := secs % 60
	if h > 0 {
		return itoa(h) + "h " + itoa(m) + "m " + itoa(s) + "s"
	}
	if m > 0 {
		return itoa(m) + "m " + itoa(s) + "s"
	}
	return itoa(s) + "s"
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [12]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}
