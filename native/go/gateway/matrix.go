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

// MatrixChannel: Client-Server /sync inbound + room send outbound.
type MatrixChannel struct {
	gateway    *Gateway
	token      string
	homeserver string
	userID     string
	roomID     string
	home       string
	access     Access
	client     *http.Client

	mu      sync.Mutex
	running bool
	cancel  context.CancelFunc
	wg      sync.WaitGroup
	lock    *PollLock
	since   string
}

// MatrixConfig configures a Matrix adapter.
type MatrixConfig struct {
	AccessToken string
	Homeserver  string
	UserID      string
	RoomID      string
	AllowIDs    any
	AllowAll    bool
	HomeDir     string
}

// NewMatrix builds a Matrix channel bound to a gateway hub.
func NewMatrix(g *Gateway, cfg MatrixConfig) *MatrixChannel {
	room := strings.TrimSpace(cfg.RoomID)
	return &MatrixChannel{
		gateway:    g,
		token:      strings.TrimSpace(cfg.AccessToken),
		homeserver: strings.TrimRight(strings.TrimSpace(cfg.Homeserver), "/"),
		userID:     strings.TrimSpace(cfg.UserID),
		roomID:     room,
		home:       cfg.HomeDir,
		access:     NewAccess(cfg.AllowIDs, cfg.AllowAll, room),
		client:     &http.Client{Timeout: 90 * time.Second},
	}
}

func (c *MatrixChannel) Kind() ChannelKind { return ChannelMatrix }

func (c *MatrixChannel) Running() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.running
}

func (c *MatrixChannel) Start(ctx context.Context) error {
	c.mu.Lock()
	if c.running {
		c.mu.Unlock()
		return nil
	}
	c.running = true
	runCtx, cancel := context.WithCancel(ctx)
	c.cancel = cancel
	c.mu.Unlock()

	if c.token == "" || c.homeserver == "" {
		log.Printf("matrix: stub mode (missing token or homeserver)")
		return nil
	}
	if c.userID == "" {
		if uid, err := c.whoami(runCtx); err == nil {
			c.userID = uid
		} else {
			log.Printf("matrix: whoami failed: %s", SafeErr(err))
		}
	}
	c.since = LoadStringCursor(c.home, "matrix_since")
	LogAccessSummary(ChannelMatrix, c.access, "room="+c.roomID)
	if !c.tryStartSync(runCtx) {
		log.Printf("matrix: sync deferred — another process holds the bot lock; retrying")
		c.wg.Add(1)
		go c.lockRetryLoop(runCtx)
	}
	return nil
}

func (c *MatrixChannel) Stop(ctx context.Context) error {
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

func (c *MatrixChannel) Send(ctx context.Context, message, target string) (bool, error) {
	if c.token == "" || c.homeserver == "" {
		return true, nil
	}
	room := strings.TrimSpace(target)
	if room == "" {
		room = c.roomID
	}
	if room == "" {
		return false, nil
	}
	txn := NewEventID()
	endpoint := c.homeserver + "/_matrix/client/v3/rooms/" + url.PathEscape(room) + "/send/m.room.message/" + txn
	status, err := c.apiJSON(ctx, http.MethodPut, endpoint,
		map[string]any{"msgtype": "m.text", "body": trimRunes(message, 4000)})
	if err != nil {
		log.Printf("matrix: send failed: %s", SafeErr(err))
		return false, err
	}
	return status == 200 || status == 201, nil
}

func (c *MatrixChannel) SendTyping(ctx context.Context, target string) error {
	room := strings.TrimSpace(target)
	if room == "" {
		room = c.roomID
	}
	if room == "" || c.token == "" || c.userID == "" || c.homeserver == "" {
		return nil
	}
	endpoint := c.homeserver + "/_matrix/client/v3/rooms/" + url.PathEscape(room) + "/typing/" + url.PathEscape(c.userID)
	_, _ = c.apiJSON(ctx, http.MethodPut, endpoint, map[string]any{"typing": true, "timeout": 10000})
	return nil
}

func (c *MatrixChannel) tryStartSync(ctx context.Context) bool {
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

	lock := NewPollLock(c.home, "matrix")
	if !lock.TryAcquire() {
		return false
	}
	c.mu.Lock()
	c.lock = lock
	since := c.since
	c.mu.Unlock()
	if since == "" {
		c.drainBacklog(ctx)
	}
	c.wg.Add(1)
	go c.syncLoop(ctx)
	log.Printf("matrix: sync task scheduled")
	return true
}

// matrixBacklogFilter asks the homeserver for a sync with no timeline events.
// The response still carries next_batch, which is all a first sync needs.
const matrixBacklogFilter = `{"room":{"timeline":{"limit":0}}}`

// drainBacklog anchors an empty cursor at "now".
//
// A first /sync with no since returns the recent timeline of every joined
// room, and each of those events would otherwise become an agent turn — so
// enabling Matrix on a busy allowlisted room would fire a burst of work and
// outbound replies at people who wrote hours ago. Ask for a zero-limit
// timeline, keep only next_batch, and discard anything the server sends
// anyway (Telegram does the same in its own drainBacklog).
func (c *MatrixChannel) drainBacklog(ctx context.Context) {
	data, err := c.syncRequest(ctx, 0, matrixBacklogFilter)
	if err != nil {
		log.Printf("matrix: backlog anchor failed: %s (starting from the next sync)", SafeErr(err))
		return
	}
	skipped := countTimelineEvents(data)
	c.mu.Lock()
	since := c.since
	c.mu.Unlock()
	if since == "" {
		log.Printf("matrix: no next_batch on the first sync; room history may replay")
		return
	}
	log.Printf("matrix: starting from now (skipped %d backlog event(s) already in the room)", skipped)
}

// countTimelineEvents counts events a sync response carried, for the log line
// that tells the owner what was skipped.
func countTimelineEvents(data map[string]any) int {
	rooms, _ := data["rooms"].(map[string]any)
	join, _ := rooms["join"].(map[string]any)
	n := 0
	for _, bodyAny := range join {
		body, _ := bodyAny.(map[string]any)
		timeline, _ := body["timeline"].(map[string]any)
		events, _ := timeline["events"].([]any)
		n += len(events)
	}
	return n
}

func (c *MatrixChannel) lockRetryLoop(ctx context.Context) {
	defer c.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case <-time.After(20 * time.Second):
		}
		if c.tryStartSync(ctx) {
			log.Printf("matrix: sync acquired after retry")
			return
		}
	}
}

func (c *MatrixChannel) syncLoop(ctx context.Context) {
	defer c.wg.Done()
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
		data, err := c.syncOnce(ctx)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			log.Printf("matrix: sync error: %s", SafeErr(err))
			select {
			case <-ctx.Done():
				return
			case <-time.After(3 * time.Second):
			}
			continue
		}
		c.handleSync(ctx, data)
	}
}

func (c *MatrixChannel) syncOnce(ctx context.Context) (map[string]any, error) {
	return c.syncRequest(ctx, 30000, "")
}

// syncRequest performs one /sync. filter is the inline filter JSON (empty for
// the normal streaming sync); timeoutMS is the long-poll budget.
func (c *MatrixChannel) syncRequest(ctx context.Context, timeoutMS int, filter string) (map[string]any, error) {
	q := url.Values{}
	q.Set("timeout", strconv.Itoa(timeoutMS))
	if filter != "" {
		q.Set("filter", filter)
	}
	c.mu.Lock()
	since := c.since
	c.mu.Unlock()
	if since != "" {
		q.Set("since", since)
	}
	budget := time.Duration(timeoutMS)*time.Millisecond + 15*time.Second
	reqCtx, cancel := context.WithTimeout(ctx, budget)
	defer cancel()
	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet,
		c.homeserver+"/_matrix/client/v3/sync?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode != 200 {
		log.Printf("matrix: sync %d: %s", resp.StatusCode, trimRunes(string(raw), 160))
		return nil, fmt.Errorf("matrix sync %d", resp.StatusCode)
	}
	var data map[string]any
	if err := json.Unmarshal(raw, &data); err != nil {
		return nil, err
	}
	if nb, _ := data["next_batch"].(string); nb != "" {
		c.mu.Lock()
		c.since = nb
		c.mu.Unlock()
		SaveStringCursor(c.home, "matrix_since", nb)
	}
	return data, nil
}

func (c *MatrixChannel) handleSync(ctx context.Context, data map[string]any) {
	rooms, _ := data["rooms"].(map[string]any)
	join, _ := rooms["join"].(map[string]any)
	for roomID, bodyAny := range join {
		body, _ := bodyAny.(map[string]any)
		timeline, _ := body["timeline"].(map[string]any)
		events, _ := timeline["events"].([]any)
		for _, evAny := range events {
			ev, _ := evAny.(map[string]any)
			c.handleTimelineEvent(ctx, roomID, ev)
		}
	}
}

func (c *MatrixChannel) handleTimelineEvent(ctx context.Context, roomID string, ev map[string]any) {
	if ev == nil {
		return
	}
	if anyString(ev["type"]) != "m.room.message" {
		return
	}
	sender := anyString(ev["sender"])
	if c.userID != "" && sender == c.userID {
		return
	}
	content, _ := ev["content"].(map[string]any)
	if anyString(content["msgtype"]) != "m.text" {
		return
	}
	text := strings.TrimSpace(anyString(content["body"]))
	if text == "" {
		return
	}
	if ok, reason := c.access.Permit(sender, roomID); !ok {
		logDeny(ChannelMatrix, reason, sender, roomID)
		if c.gateway != nil {
			c.gateway.recordDenied(ChannelMatrix, reason, sender, roomID)
		}
		return
	}
	go func() {
		tctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = c.SendTyping(tctx, roomID)
	}()
	sourceID := sender
	if sourceID == "" {
		sourceID = roomID
	}
	out := Event{
		ID:        NewEventID(),
		Kind:      EventMessage,
		Channel:   ChannelMatrix,
		SourceID:  sourceID,
		SessionID: roomID,
		Payload: map[string]any{
			"message":  text,
			"chat_id":  roomID,
			"room_id":  roomID,
			"user_id":  sender,
			"username": sender,
		},
		Raw: trimRunes(text, 500),
		At:  time.Now().UTC(),
	}
	if c.gateway != nil && c.gateway.Running() {
		c.gateway.Enqueue(out)
	} else if c.gateway != nil {
		_ = c.gateway.Emit(ctx, out)
	}
}

func (c *MatrixChannel) whoami(ctx context.Context) (string, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		c.homeserver+"/_matrix/client/v3/account/whoami", nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("matrix whoami %d", resp.StatusCode)
	}
	var data struct {
		UserID string `json:"user_id"`
	}
	if err := json.Unmarshal(raw, &data); err != nil {
		return "", err
	}
	return strings.TrimSpace(data.UserID), nil
}

func (c *MatrixChannel) apiJSON(ctx context.Context, method, endpoint string, body any) (int, error) {
	raw, err := json.Marshal(body)
	if err != nil {
		return 0, err
	}
	req, err := http.NewRequestWithContext(ctx, method, endpoint, strings.NewReader(string(raw)))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/json")
	resp, err := c.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(resp.Body, 1<<20))
	return resp.StatusCode, nil
}
