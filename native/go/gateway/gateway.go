package gateway

import (
	"context"
	"log"
	"sync"
	"time"
)

// Handler processes a gateway event. Handlers run on a per-session worker, so
// a slow turn in one chat never blocks another chat or the hub loop.
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
	// QueueSize bounds the hub intake queue (default 256).
	QueueSize int
	// WorkerQueueSize bounds each per-session worker queue (default 8).
	WorkerQueueSize int
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

const (
	defaultQueueSize       = 256
	defaultWorkerQueueSize = 8
	workerIdleReap         = 10 * time.Minute
	rateLimitWindow        = time.Minute
	busyReplyWindow        = 30 * time.Second

	rateLimitReply = "You are sending messages too quickly. Wait a few seconds and try again."
	queueBusyReply = "I am handling a lot of messages right now. Please try again in a moment."
)

// Gateway is the always-on multi-channel event hub.
type Gateway struct {
	mu sync.Mutex

	cfg      Config
	handlers []Handler
	channels map[ChannelKind]Channel

	running   bool
	startedAt time.Time
	events    int
	dropped   int
	rateLimit int
	heartbeat time.Duration

	queue  chan Event
	cancel context.CancelFunc
	wg     sync.WaitGroup

	workersMu sync.Mutex
	workers   map[string]*sessionWorker

	// rateBuckets / rateWarned / busyWarned are keyed by channel|sender.
	rateBuckets map[string][]time.Time
	rateWarned  map[string]time.Time
	busyWarned  map[string]time.Time
	now         func() time.Time

	// deniedMu guards the refusal ring. A refused message is silent to its
	// sender by design, so the owner needs somewhere to see that it happened.
	deniedMu sync.Mutex
	denied   []DeniedInbound
}

// DeniedInbound is one refused inbound message, kept so Settings can tell the
// owner that somebody tried to reach Remedy and why it was ignored.
type DeniedInbound struct {
	Channel  string  `json:"channel"`
	UserID   string  `json:"user_id"`
	ScopeID  string  `json:"scope_id"`
	Reason   string  `json:"reason"`
	Count    int     `json:"count"`
	LastSeen float64 `json:"last_seen"`
}

// deniedRingSize bounds the refusal ring; distinct (channel,user,scope,reason)
// rows collapse into a count so a chatty stranger cannot evict the owner's row.
const deniedRingSize = 32

// recordDenied notes a refused inbound message for the owner-facing list.
func (g *Gateway) recordDenied(channel ChannelKind, reason, userID, scopeID string) {
	if g == nil {
		return
	}
	now := float64(time.Now().UnixNano()) / 1e9
	g.deniedMu.Lock()
	defer g.deniedMu.Unlock()
	for i := range g.denied {
		d := &g.denied[i]
		if d.Channel == string(channel) && d.UserID == userID && d.ScopeID == scopeID && d.Reason == reason {
			d.Count++
			d.LastSeen = now
			return
		}
	}
	if len(g.denied) >= deniedRingSize {
		g.denied = g.denied[1:]
	}
	g.denied = append(g.denied, DeniedInbound{
		Channel: string(channel), UserID: userID, ScopeID: scopeID,
		Reason: reason, Count: 1, LastSeen: now,
	})
}

// DeniedInbounds returns the refusal ring, oldest first.
func (g *Gateway) DeniedInbounds() []DeniedInbound {
	if g == nil {
		return nil
	}
	g.deniedMu.Lock()
	defer g.deniedMu.Unlock()
	return append([]DeniedInbound(nil), g.denied...)
}

type sessionWorker struct {
	ch chan Event
}

// New builds an idle gateway hub.
func New(cfg Config) *Gateway {
	if cfg.HeartbeatInterval <= 0 {
		cfg.HeartbeatInterval = 60 * time.Second
	}
	if cfg.RateLimitPerMin <= 0 {
		cfg.RateLimitPerMin = 60
	}
	if cfg.QueueSize <= 0 {
		cfg.QueueSize = defaultQueueSize
	}
	if cfg.WorkerQueueSize <= 0 {
		cfg.WorkerQueueSize = defaultWorkerQueueSize
	}
	return &Gateway{
		cfg:         cfg,
		channels:    make(map[ChannelKind]Channel),
		rateLimit:   cfg.RateLimitPerMin,
		heartbeat:   cfg.HeartbeatInterval,
		workers:     make(map[string]*sessionWorker),
		rateBuckets: make(map[string][]time.Time),
		rateWarned:  make(map[string]time.Time),
		busyWarned:  make(map[string]time.Time),
		now:         time.Now,
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

// RegisterHandler appends an event handler (callers should register once).
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
		// Refused inbound messages are silent to their sender, so the owner
		// sees them here (Settings can offer "add to allowlist").
		"denied_inbound": g.DeniedInbounds(),
	}
}

// Start launches the hub loop, heartbeat, and all registered channels.
func (g *Gateway) Start(ctx context.Context) error {
	g.mu.Lock()
	if g.running {
		g.mu.Unlock()
		return nil
	}
	runCtx, cancel := context.WithCancel(ctx)
	g.cancel = cancel
	g.queue = make(chan Event, g.cfg.QueueSize)
	g.running = true
	g.startedAt = time.Now().UTC()
	channels := make([]Channel, 0, len(g.channels))
	for _, ch := range g.channels {
		channels = append(channels, ch)
	}
	g.mu.Unlock()

	g.wg.Add(2)
	go g.hubLoop(runCtx)
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
	g.workersMu.Lock()
	g.workers = make(map[string]*sessionWorker)
	g.workersMu.Unlock()
	log.Printf("gateway: stopped (events=%d)", events)
	return nil
}

// Enqueue buffers an event for async handling. See TryEnqueue.
func (g *Gateway) Enqueue(ev Event) {
	_ = g.TryEnqueue(ev)
}

// TryEnqueue buffers an event and reports whether it was accepted. When the
// hub is not running the event is handled synchronously. When the intake
// queue is full the sender is told once per window and false is returned so
// pollers can hold their cursor and retry instead of losing the message.
func (g *Gateway) TryEnqueue(ev Event) bool {
	if g == nil {
		return false
	}
	g.mu.Lock()
	running := g.running
	q := g.queue
	g.mu.Unlock()
	if !running || q == nil {
		_ = g.Emit(context.Background(), ev)
		return true
	}
	select {
	case q <- ev:
		return true
	default:
		g.noteQueueFull(ev, "hub")
		return false
	}
}

func (g *Gateway) noteQueueFull(ev Event, where string) {
	g.mu.Lock()
	g.dropped++
	g.mu.Unlock()
	log.Printf("gateway: %s queue full; refusing %s from %s (sender=%s)", where, ev.Kind, ev.Channel, ev.SourceID)
	if ev.Kind != EventMessage || !IsMessenger(ev.Channel) {
		return
	}
	key := string(ev.Channel) + "|" + ev.SourceID
	now := g.now()
	g.mu.Lock()
	last, seen := g.busyWarned[key]
	if seen && now.Sub(last) < busyReplyWindow {
		g.mu.Unlock()
		return
	}
	g.busyWarned[key] = now
	g.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	_, _ = g.SendTo(ctx, ev.Channel, queueBusyReply, replyTarget(ev))
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
	if ev.Kind == EventMessage {
		if ok, warn := g.checkRateLimit(ev); !ok {
			if warn {
				log.Printf("gateway: rate limit exceeded for %s sender=%s", ev.Channel, ev.SourceID)
				_, _ = g.SendTo(ctx, ev.Channel, rateLimitReply, replyTarget(ev))
			}
			return nil
		}
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

func replyTarget(ev Event) string {
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
	return target
}

// checkRateLimit applies a sliding one-minute window per (channel, sender).
// The second result is true when the caller should send the single
// "too fast" reply for this window.
func (g *Gateway) checkRateLimit(ev Event) (allowed bool, warn bool) {
	key := string(ev.Channel) + "|" + ev.SourceID
	g.mu.Lock()
	defer g.mu.Unlock()
	now := g.now()
	bucket := g.rateBuckets[key]
	kept := bucket[:0]
	for _, t := range bucket {
		if now.Sub(t) < rateLimitWindow {
			kept = append(kept, t)
		}
	}
	if len(kept) >= g.rateLimit {
		g.rateBuckets[key] = kept
		last, seen := g.rateWarned[key]
		if !seen || now.Sub(last) >= rateLimitWindow {
			g.rateWarned[key] = now
			return false, true
		}
		return false, false
	}
	g.rateBuckets[key] = append(kept, now)
	return true, false
}

func workerKey(ev Event) string {
	sid := ev.SessionID
	if sid == "" {
		sid = ev.SourceID
	}
	return string(ev.Channel) + "|" + sid
}

// hubLoop drains the intake queue and hands each event to its session worker.
// It never blocks on a handler: a full worker queue is reported to the sender
// and the event is refused.
func (g *Gateway) hubLoop(ctx context.Context) {
	defer g.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case ev, ok := <-g.queue:
			if !ok {
				return
			}
			g.dispatch(ctx, ev)
		}
	}
}

func (g *Gateway) dispatch(ctx context.Context, ev Event) {
	key := workerKey(ev)
	g.workersMu.Lock()
	w := g.workers[key]
	if w == nil {
		w = &sessionWorker{ch: make(chan Event, g.cfg.WorkerQueueSize)}
		g.workers[key] = w
		g.wg.Add(1)
		go g.runWorker(ctx, key, w)
	}
	select {
	case w.ch <- ev:
		g.workersMu.Unlock()
	default:
		g.workersMu.Unlock()
		g.noteQueueFull(ev, "session")
	}
}

func (g *Gateway) runWorker(ctx context.Context, key string, w *sessionWorker) {
	defer g.wg.Done()
	idle := time.NewTimer(workerIdleReap)
	defer idle.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case ev := <-w.ch:
			_ = g.Emit(ctx, ev)
			if !idle.Stop() {
				select {
				case <-idle.C:
				default:
				}
			}
			idle.Reset(workerIdleReap)
		case <-idle.C:
			g.workersMu.Lock()
			if len(w.ch) == 0 && g.workers[key] == w {
				delete(g.workers, key)
				g.workersMu.Unlock()
				return
			}
			g.workersMu.Unlock()
			idle.Reset(workerIdleReap)
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
