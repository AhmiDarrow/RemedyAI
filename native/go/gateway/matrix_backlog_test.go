package gateway

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"
)

// matrixTimeline builds a /sync body with n messages already in the room.
func matrixTimeline(roomID, sender string, bodies ...string) map[string]any {
	events := make([]any, 0, len(bodies))
	for _, text := range bodies {
		events = append(events, map[string]any{
			"type":   "m.room.message",
			"sender": sender,
			"content": map[string]any{
				"msgtype": "m.text",
				"body":    text,
			},
		})
	}
	return map[string]any{
		"next_batch": "s-after-backlog",
		"rooms": map[string]any{
			"join": map[string]any{
				roomID: map[string]any{
					"timeline": map[string]any{"events": events},
				},
			},
		},
	}
}

type matrixRecorder struct {
	mu     sync.Mutex
	events []Event
}

func (r *matrixRecorder) handle(_ context.Context, ev Event) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events = append(r.events, ev)
	return nil
}

func (r *matrixRecorder) count() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.events)
}

// Enabling Matrix on a busy room must not turn its history into a burst of
// agent turns (and outbound replies to people who wrote hours ago). On an
// empty cursor the first sync asks for a zero-limit timeline and keeps only
// next_batch — the same anchoring Telegram does in its own drainBacklog.
func TestMatrixEmptyCursorAnchorsInsteadOfReplayingHistory(t *testing.T) {
	home := t.TempDir()
	const room = "!busy:example.org"

	var mu sync.Mutex
	var queries []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		queries = append(queries, r.URL.RawQuery)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		// A homeserver may ignore the filter; the client must still discard.
		_ = json.NewEncoder(w).Encode(matrixTimeline(room, "@stranger:example.org", "old one", "old two"))
	}))
	t.Cleanup(srv.Close)

	rec := &matrixRecorder{}
	g := New(Config{RateLimitPerMin: 1000, HeartbeatInterval: time.Hour})
	g.RegisterHandler(rec.handle)
	c := NewMatrix(g, MatrixConfig{
		AccessToken: "t",
		Homeserver:  srv.URL,
		UserID:      "@bot:example.org",
		RoomID:      room,
		AllowIDs:    "@stranger:example.org",
		HomeDir:     home,
	})

	c.drainBacklog(context.Background())

	if rec.count() != 0 {
		t.Fatalf("backlog became %d agent turn(s)", rec.count())
	}
	mu.Lock()
	got := append([]string(nil), queries...)
	mu.Unlock()
	if len(got) != 1 {
		t.Fatalf("queries=%v", got)
	}
	if !strings.Contains(got[0], "filter=") || !strings.Contains(got[0], "limit") {
		t.Fatalf("first sync must carry a zero-limit timeline filter: %s", got[0])
	}
	if strings.Contains(got[0], "since=") {
		t.Fatalf("anchoring sync must not send a cursor: %s", got[0])
	}
	if c.since != "s-after-backlog" {
		t.Fatalf("cursor not advanced: %q", c.since)
	}
	if LoadStringCursor(home, "matrix_since") != "s-after-backlog" {
		t.Fatalf("cursor not persisted: %q", LoadStringCursor(home, "matrix_since"))
	}

	// Everything after the anchor is live traffic and must still be delivered.
	c.handleSync(context.Background(), matrixTimeline(room, "@stranger:example.org", "hello now"))
	if rec.count() != 1 {
		t.Fatalf("live message count=%d want 1", rec.count())
	}
}

// A room id says where the bot may answer, never who may speak to it.
func TestMatrixRoomIDIsScopeNotIdentity(t *testing.T) {
	const room = "!allowed:example.org"
	g := New(Config{RateLimitPerMin: 1000, HeartbeatInterval: time.Hour})
	rec := &matrixRecorder{}
	g.RegisterHandler(rec.handle)
	c := NewMatrix(g, MatrixConfig{
		AccessToken: "t",
		Homeserver:  "https://example.org",
		UserID:      "@bot:example.org",
		RoomID:      room,
		AllowIDs:    "@owner:example.org",
		HomeDir:     t.TempDir(),
	})

	msg := func(sender string) map[string]any {
		return map[string]any{
			"type":    "m.room.message",
			"sender":  sender,
			"content": map[string]any{"msgtype": "m.text", "body": "hi"},
		}
	}
	c.handleTimelineEvent(context.Background(), room, msg("@stranger:example.org"))
	if rec.count() != 0 {
		t.Fatal("a stranger in the allowlisted room must not be admitted")
	}
	denied := g.DeniedInbounds()
	if len(denied) != 1 || denied[0].UserID != "@stranger:example.org" || denied[0].Reason == "" {
		t.Fatalf("refusal must be visible to the owner: %+v", denied)
	}
	c.handleTimelineEvent(context.Background(), room, msg("@owner:example.org"))
	if rec.count() != 1 {
		t.Fatalf("the allowlisted owner must be admitted: %d", rec.count())
	}
	// A room the owner never allowlisted stays out of scope.
	c.handleTimelineEvent(context.Background(), "!other:example.org", msg("@owner:example.org"))
	if rec.count() != 1 {
		t.Fatal("out-of-scope room must be refused")
	}
}

// Slack and Mattermost carry a channel id beside the user id; the channel id
// is scope only, so a stranger posting in the bot's channel is still refused.
func TestSlackAndMattermostChannelIDIsScopeNotIdentity(t *testing.T) {
	g := New(Config{RateLimitPerMin: 1000, HeartbeatInterval: time.Hour})
	rec := &matrixRecorder{}
	g.RegisterHandler(rec.handle)

	slack := NewSlack(g, SlackConfig{BotToken: "t", ChannelID: "C1", AllowIDs: "U-owner"})
	slack.handleEvent(context.Background(), map[string]any{
		"type": "message", "user": "U-stranger", "text": "hi", "channel": "C1", "ts": "1",
	})
	if rec.count() != 0 {
		t.Fatal("slack: channel id must not stand in for a user id")
	}
	slack.handleEvent(context.Background(), map[string]any{
		"type": "message", "user": "U-owner", "text": "hi", "channel": "C1", "ts": "2",
	})
	if rec.count() != 1 {
		t.Fatalf("slack: owner refused (%d)", rec.count())
	}

	mm := NewMattermost(g, MattermostConfig{
		BotToken: "t", BaseURL: "https://mm.example.org",
		ChannelID: "ch1", TeamID: "team1", AllowIDs: "u-owner",
	})
	post := func(user string) string {
		raw, _ := json.Marshal(map[string]any{
			"channel_id": "ch1", "user_id": user, "message": "hi",
		})
		return string(raw)
	}
	mm.onEvent(context.Background(), map[string]any{
		"event": "posted",
		"data":  map[string]any{"post": post("u-stranger")},
	})
	if rec.count() != 1 {
		t.Fatal("mattermost: channel id must not stand in for a user id")
	}
	mm.onEvent(context.Background(), map[string]any{
		"event": "posted",
		"data":  map[string]any{"post": post("u-owner")},
	})
	if rec.count() != 2 {
		t.Fatalf("mattermost: owner refused (%d)", rec.count())
	}

	denied := g.DeniedInbounds()
	if len(denied) != 2 {
		t.Fatalf("both refusals must reach the owner: %+v", denied)
	}
}
