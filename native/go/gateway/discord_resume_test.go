package gateway

import (
	"bufio"
	"context"
	"encoding/binary"
	"encoding/json"
	"math/rand"
	"net"
	"net/http"
	"testing"
	"time"
)

// wsPipe returns two wsConns wired to each other over a loopback TCP pair:
// one stands in for the dialled gateway socket, one for the server driving
// the test. A real socket (not net.Pipe) so a close frame written with nobody
// reading is buffered rather than deadlocking the session loop.
func wsPipe(t *testing.T) (client, server *wsConn, closeFn func()) {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	defer ln.Close()
	accepted := make(chan net.Conn, 1)
	go func() {
		conn, aerr := ln.Accept()
		if aerr != nil {
			close(accepted)
			return
		}
		accepted <- conn
	}()
	a, err := net.Dial("tcp", ln.Addr().String())
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	b, ok := <-accepted
	if !ok {
		t.Fatal("accept failed")
	}
	client = &wsConn{conn: a, reader: bufio.NewReader(a)}
	server = &wsConn{conn: b, reader: bufio.NewReader(b)}
	return client, server, func() {
		_ = a.Close()
		_ = b.Close()
	}
}

func (w *wsConn) writeJSONFrame(v any) error {
	raw, err := json.Marshal(v)
	if err != nil {
		return err
	}
	return w.WriteText(raw)
}

// readJSONFrame decodes one text frame. Safe to call from a goroutine: it
// reports failures as an error rather than through *testing.T.
func (w *wsConn) readJSONFrame() (map[string]any, error) {
	_ = w.conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	_, payload, err := w.ReadMessage()
	if err != nil {
		return nil, err
	}
	_ = w.conn.SetReadDeadline(time.Time{})
	var out map[string]any
	if err := json.Unmarshal(payload, &out); err != nil {
		return nil, err
	}
	return out, nil
}

// writeCloseFrame sends a WS close with a Discord 4xxx status code.
func (w *wsConn) writeCloseFrame(code int) error {
	var body [2]byte
	binary.BigEndian.PutUint16(body[:], uint16(code))
	return w.writeFrame(0x8, body[:])
}

func discordHello(interval int) map[string]any {
	return map[string]any{
		"op": discordOpHello,
		"d":  map[string]any{"heartbeat_interval": interval},
	}
}

func newTestDiscord(t *testing.T) *DiscordChannel {
	t.Helper()
	g := New(Config{RateLimitPerMin: 1000, HeartbeatInterval: time.Hour})
	return NewDiscord(g, DiscordConfig{
		BotToken: "t",
		AllowIDs: "42",
		HomeDir:  t.TempDir(),
	})
}

// stubDial hands sessionOnce a prepared socket and records the URL it dialled.
func stubDial(t *testing.T, conn *wsConn, gotURL *string) {
	t.Helper()
	prev := dialWS
	t.Cleanup(func() { dialWS = prev })
	dialWS = func(rawURL string, _ http.Header, _ time.Duration) (*wsConn, error) {
		if gotURL != nil {
			*gotURL = rawURL
		}
		return conn, nil
	}
}

// A flapping link must RESUME, not re-IDENTIFY: every fresh IDENTIFY spends
// from the bot daily identify budget, and running that out invalidates the
// token, which takes inbound Discord dark until the owner re-issues it.
func TestDiscordReconnectResumesInsteadOfReIdentifying(t *testing.T) {
	c := newTestDiscord(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	// --- first connection: HELLO -> IDENTIFY -> READY -> op 7 reconnect ---
	client, server, closeFn := wsPipe(t)
	defer closeFn()
	var firstURL string
	stubDial(t, client, &firstURL)

	identified := make(chan map[string]any, 1)
	go func() {
		_ = server.writeJSONFrame(discordHello(60000))
		packet, err := server.readJSONFrame()
		if err != nil {
			close(identified)
			return
		}
		identified <- packet
		_ = server.writeJSONFrame(map[string]any{
			"op": discordOpDispatch, "s": 5, "t": "READY",
			"d": map[string]any{
				"session_id":         "sess-1",
				"resume_gateway_url": "wss://resume.example.com",
			},
		})
		_ = server.writeJSONFrame(map[string]any{"op": discordOpReconnect})
	}()

	if err := c.sessionOnce(ctx); err != errDiscordReconnect {
		t.Fatalf("op 7 must ask for a reconnect, got %v", err)
	}
	first := <-identified
	if op, _ := asInt(first["op"]); op != discordOpIdentify {
		t.Fatalf("first packet op=%v want IDENTIFY (%d)", first["op"], discordOpIdentify)
	}
	closeFn()
	if c.sessionID != "sess-1" || c.seq == nil || *c.seq != 5 {
		t.Fatalf("resume state lost: session=%q seq=%v", c.sessionID, c.seq)
	}
	if firstURL != discordGatewayURL {
		t.Fatalf("first dial=%q", firstURL)
	}

	// --- second connection: must RESUME on the resume URL ---
	client2, server2, closeFn2 := wsPipe(t)
	defer closeFn2()
	var secondURL string
	stubDial(t, client2, &secondURL)

	resumed := make(chan map[string]any, 1)
	go func() {
		_ = server2.writeJSONFrame(discordHello(60000))
		packet, err := server2.readJSONFrame()
		if err != nil {
			close(resumed)
			return
		}
		resumed <- packet
		// A resumable 4xxx close keeps the session for the next attempt.
		_ = server2.writeCloseFrame(4000)
	}()

	if err := c.sessionOnce(ctx); err != errDiscordReconnect {
		t.Fatalf("resumable close must reconnect, got %v", err)
	}
	packet := <-resumed
	if op, _ := asInt(packet["op"]); op != discordOpResume {
		t.Fatalf("op=%v want RESUME (%d): %v", packet["op"], discordOpResume, packet)
	}
	d, _ := packet["d"].(map[string]any)
	if d["session_id"] != "sess-1" {
		t.Fatalf("resume session_id=%v", d["session_id"])
	}
	if seq, _ := asInt(d["seq"]); seq != 5 {
		t.Fatalf("resume seq=%v", d["seq"])
	}
	if secondURL == discordGatewayURL {
		t.Fatalf("resume must use resume_gateway_url, dialled %q", secondURL)
	}
	if c.sessionID != "sess-1" {
		t.Fatal("code 4000 is resumable; the session was dropped anyway")
	}
}

// op 9 honours the resumable flag: false means the next attempt must be a
// fresh IDENTIFY, true means keep the session.
func TestDiscordInvalidSessionHonoursResumableFlag(t *testing.T) {
	prevWait := discordInvalidSessionWait
	t.Cleanup(func() { discordInvalidSessionWait = prevWait })
	discordInvalidSessionWait = func(*rand.Rand) time.Duration { return 0 }

	for _, tc := range []struct {
		name      string
		resumable bool
		wantKept  bool
	}{
		{"not resumable", false, false},
		{"resumable", true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := newTestDiscord(t)
			c.sessionID = "sess-9"
			seq := 7
			c.seq = &seq

			client, server, closeFn := wsPipe(t)
			defer closeFn()
			stubDial(t, client, nil)
			go func() {
				_ = server.writeJSONFrame(discordHello(60000))
				if _, err := server.readJSONFrame(); err != nil {
					return
				}
				_ = server.writeJSONFrame(map[string]any{
					"op": discordOpInvalidSession, "d": tc.resumable,
				})
			}()

			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			defer cancel()
			if err := c.sessionOnce(ctx); err != errDiscordInvalidSes {
				t.Fatalf("err=%v", err)
			}
			if kept := c.sessionID != ""; kept != tc.wantKept {
				t.Fatalf("session kept=%v want %v", kept, tc.wantKept)
			}
		})
	}
}

// A close code that cannot be fixed by reconnecting (bad token, missing
// privileged intents) must not spin: it drops the session and backs off hard.
func TestDiscordFatalCloseDropsSessionAndBacksOffHard(t *testing.T) {
	c := newTestDiscord(t)
	c.sessionID = "sess-x"
	seq := 3
	c.seq = &seq

	client, server, closeFn := wsPipe(t)
	defer closeFn()
	stubDial(t, client, nil)
	go func() {
		_ = server.writeJSONFrame(discordHello(60000))
		if _, err := server.readJSONFrame(); err != nil {
			return
		}
		_ = server.writeCloseFrame(4004) // authentication failed
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := c.sessionOnce(ctx); err != errDiscordFatalClose {
		t.Fatalf("err=%v", err)
	}
	if c.sessionID != "" || c.seq != nil {
		t.Fatalf("fatal close must drop the session: %q %v", c.sessionID, c.seq)
	}
	if got := c.nextBackoff(true); got < discordFatalBackoff {
		t.Fatalf("fatal backoff=%s want >= %s", got, discordFatalBackoff)
	}
}

func TestDiscordCloseCodeClassification(t *testing.T) {
	for _, tc := range []struct {
		code      int
		resumable bool
		fatal     bool
	}{
		{4000, true, false},  // unknown error
		{4004, false, true},  // authentication failed
		{4013, false, true},  // invalid intents
		{4014, false, true},  // disallowed intents
		{4007, false, false}, // invalid seq
		{4009, false, false}, // session timed out
		{1000, false, false}, // normal close
	} {
		resumable, fatal := discordCloseCodeResumable(tc.code)
		if resumable != tc.resumable || fatal != tc.fatal {
			t.Fatalf("code %d: resumable=%v fatal=%v want %v/%v",
				tc.code, resumable, fatal, tc.resumable, tc.fatal)
		}
	}
}

// Backoff must grow and stay bounded, and carry jitter so a fleet of restarts
// does not stampede the gateway on the same second.
func TestDiscordBackoffGrowsWithJitterAndIsBounded(t *testing.T) {
	c := newTestDiscord(t)
	sawJitter := false
	for i := 0; i < 12; i++ {
		base := c.backoff
		got := c.nextBackoff(false)
		if got < base || got > base+base/2 {
			t.Fatalf("attempt %d: delay %s outside [%s, %s]", i, got, base, base+base/2)
		}
		if got != base {
			sawJitter = true
		}
		if c.backoff > discordBackoffMax {
			t.Fatalf("attempt %d: base %s exceeds the cap %s", i, c.backoff, discordBackoffMax)
		}
	}
	if !sawJitter {
		t.Fatal("backoff never jittered")
	}
	c.resetBackoff()
	if c.backoff != discordBackoffMin {
		t.Fatalf("reset backoff=%s", c.backoff)
	}
}

// A gateway that stops acknowledging heartbeats is a dead link that still
// looks open. The heartbeat loop must notice and force a reconnect.
func TestDiscordMissedHeartbeatACKMarksZombie(t *testing.T) {
	c := newTestDiscord(t)
	c.heartbeatMS = 5
	client, server, closeFn := wsPipe(t)
	defer closeFn()
	// Drain whatever the heartbeat loop writes; never ACK.
	go func() {
		for {
			if _, _, err := server.ReadMessage(); err != nil {
				return
			}
		}
	}()

	hb := &discordHeartbeat{ws: client, acked: true}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	go c.heartbeatLoop(ctx, hb)

	select {
	case <-hb.zombie():
	case <-ctx.Done():
		t.Fatal("an unacknowledged heartbeat never marked the link dead")
	}
}

// The mirror image: a link that keeps acknowledging is left alone.
func TestDiscordAcknowledgedHeartbeatKeepsLinkAlive(t *testing.T) {
	c := newTestDiscord(t)
	c.heartbeatMS = 5
	client, server, closeFn := wsPipe(t)
	defer closeFn()
	hb := &discordHeartbeat{ws: client, acked: true}
	go func() {
		for {
			if _, _, err := server.ReadMessage(); err != nil {
				return
			}
			hb.ack()
		}
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	go c.heartbeatLoop(ctx, hb)
	select {
	case <-hb.zombie():
		t.Fatal("an acknowledged link must not be marked dead")
	case <-ctx.Done():
	}
}
