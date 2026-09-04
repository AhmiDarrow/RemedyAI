package httpapi

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

func waitAtomic(t *testing.T, n *atomic.Int32, want int32) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if n.Load() == want {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("atomic=%d want %d", n.Load(), want)
}

func newWebhookTestServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	s, err := New(Config{
		Token:   "test-token-not-a-secret-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	s.startMessengerGateway()
	return s
}

func TestWhatsAppWebhookRoutes(t *testing.T) {
	s := newWebhookTestServer(t)
	var n atomic.Int32
	s.messengerGW.RegisterHandler(func(ctx context.Context, ev gateway.Event) error {
		if ev.Kind == gateway.EventMessage {
			n.Add(1)
		}
		return nil
	})
	wa := gateway.NewWhatsApp(s.messengerGW, gateway.WhatsAppConfig{
		AccessToken:   "tok",
		PhoneNumberID: "phone1",
		VerifyToken:   "verify-secret",
		AppSecret:     "app-secret",
		AllowAll:      true,
	})
	s.messengerGW.RegisterChannel(wa)
	h := s.Handler()

	// GET verify success
	req := httptest.NewRequest(http.MethodGet, "/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify-secret&hub.challenge=42", nil)
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK || rec.Body.String() != "42" {
		t.Fatalf("verify: status=%d body=%q", rec.Code, rec.Body.String())
	}

	// GET verify failure (no API bearer needed)
	req = httptest.NewRequest(http.MethodGet, "/api/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=42", nil)
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("bad verify status=%d", rec.Code)
	}

	payload := map[string]any{
		"entry": []any{
			map[string]any{
				"changes": []any{
					map[string]any{
						"value": map[string]any{
							"messages": []any{
								map[string]any{
									"from": "15551234567",
									"type": "text",
									"text": map[string]any{"body": "hello"},
								},
							},
						},
					},
				},
			},
		},
	}
	raw, _ := json.Marshal(payload)
	mac := hmac.New(sha256.New, []byte("app-secret"))
	_, _ = mac.Write(raw)
	sig := "sha256=" + hex.EncodeToString(mac.Sum(nil))

	req = httptest.NewRequest(http.MethodPost, "/api/webhooks/whatsapp", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Hub-Signature-256", sig)
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("events status=%d body=%s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["ok"] != true || body["handled"] != float64(1) {
		t.Fatalf("events body=%#v", body)
	}
	waitAtomic(t, &n, 1)

	// Bad signature
	req = httptest.NewRequest(http.MethodPost, "/api/webhooks/whatsapp", bytes.NewReader(raw))
	req.Header.Set("X-Hub-Signature-256", "sha256=deadbeef")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("bad sig status=%d", rec.Code)
	}
}

func TestWhatsAppWebhookRejectsOversized(t *testing.T) {
	s := newWebhookTestServer(t)
	s.messengerGW.RegisterChannel(gateway.NewWhatsApp(s.messengerGW, gateway.WhatsAppConfig{
		AccessToken: "t", PhoneNumberID: "p", AppSecret: "s", AllowAll: true,
	}))
	h := s.Handler()
	big := bytes.Repeat([]byte("x"), webhookMaxBytes+8)
	req := httptest.NewRequest(http.MethodPost, "/api/webhooks/whatsapp", bytes.NewReader(big))
	req.ContentLength = int64(len(big))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("status=%d want 413", rec.Code)
	}
}

func TestTeamsWebhookAuthAndActivity(t *testing.T) {
	s := newWebhookTestServer(t)
	t.Setenv("REMEDY_TEAMS_SKIP_JWT", "1")
	var n atomic.Int32
	s.messengerGW.RegisterHandler(func(ctx context.Context, ev gateway.Event) error {
		if ev.Kind == gateway.EventMessage {
			n.Add(1)
		}
		return nil
	})
	s.messengerGW.RegisterChannel(gateway.NewTeams(s.messengerGW, gateway.TeamsConfig{
		AppID: "app-id", AppPassword: "pw", AllowAll: true,
	}))
	h := s.Handler()

	activity := map[string]any{
		"type":       "message",
		"text":       "hi teams",
		"serviceUrl": "https://smba.trafficmanager.net/amer/",
		"conversation": map[string]any{
			"id": "conv1",
		},
		"from": map[string]any{"id": "u1", "name": "User"},
	}
	raw, _ := json.Marshal(activity)
	req := httptest.NewRequest(http.MethodPost, "/api/webhooks/teams", bytes.NewReader(raw))
	req.Header.Set("Authorization", "Bearer ignored-when-skip")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	waitAtomic(t, &n, 1)

	// Missing auth when skip off
	t.Setenv("REMEDY_TEAMS_SKIP_JWT", "0")
	req = httptest.NewRequest(http.MethodPost, "/api/webhooks/teams", bytes.NewReader(raw))
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("unauth status=%d", rec.Code)
	}
}

func TestGoogleChatWebhookChallengeAndAuth(t *testing.T) {
	s := newWebhookTestServer(t)
	s.messengerGW.RegisterChannel(gateway.NewGoogleChat(s.messengerGW, gateway.GoogleChatConfig{
		AccessToken: "gchat-tok", AllowAll: true,
	}))
	h := s.Handler()

	// Challenge-only skips auth
	raw, _ := json.Marshal(map[string]any{"challenge": "abc", "type": "URL_VERIFICATION"})
	req := httptest.NewRequest(http.MethodPost, "/api/webhooks/google_chat", bytes.NewReader(raw))
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("challenge status=%d", rec.Code)
	}
	var body map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &body)
	if body["challenge"] != "abc" {
		t.Fatalf("challenge body=%#v", body)
	}

	// MESSAGE with challenge key still requires auth
	raw, _ = json.Marshal(map[string]any{
		"type":      "MESSAGE",
		"challenge": "probe",
		"message":   map[string]any{"text": "hi", "sender": map[string]any{"name": "users/1", "displayName": "A"}},
		"space":     map[string]any{"name": "spaces/s1"},
	})
	req = httptest.NewRequest(http.MethodPost, "/api/webhooks/google_chat", bytes.NewReader(raw))
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("message+challenge without auth status=%d", rec.Code)
	}

	var n atomic.Int32
	s.messengerGW.RegisterHandler(func(ctx context.Context, ev gateway.Event) error {
		if ev.Kind == gateway.EventMessage {
			n.Add(1)
		}
		return nil
	})
	req = httptest.NewRequest(http.MethodPost, "/api/webhooks/google_chat", bytes.NewReader(raw))
	req.Header.Set("Authorization", "Bearer gchat-tok")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("authed status=%d body=%s", rec.Code, rec.Body.String())
	}
	waitAtomic(t, &n, 1)

	// Oversized Content-Length
	req = httptest.NewRequest(http.MethodPost, "/api/webhooks/google_chat", strings.NewReader("hi"))
	req.ContentLength = webhookMaxBytes + 1
	req.Body = io.NopCloser(strings.NewReader("hi"))
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("oversized status=%d", rec.Code)
	}
}

func TestWebhookPathsSkipAPIBearer(t *testing.T) {
	s := newWebhookTestServer(t)
	// No channel → 503, not 401 (public at middleware)
	req := httptest.NewRequest(http.MethodPost, "/api/webhooks/whatsapp", bytes.NewReader([]byte(`{}`)))
	rec := httptest.NewRecorder()
	s.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusServiceUnavailable {
		t.Fatalf("status=%d want 503", rec.Code)
	}
}

func TestGenericWebhookHappyPath(t *testing.T) {
	s := newWebhookTestServer(t)
	var n atomic.Int32
	var mu sync.Mutex
	var got gateway.Event
	s.messengerGW.RegisterHandler(func(ctx context.Context, ev gateway.Event) error {
		if ev.Kind == gateway.EventWebhook {
			mu.Lock()
			got = ev
			mu.Unlock()
			n.Add(1)
		}
		return nil
	})
	h := s.Handler()
	raw := []byte(`{"source":"ci","event":"push","data":{"x":1}}`)
	req := httptest.NewRequest(http.MethodPost, "/api/webhook/ci", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer test-token-not-a-secret-16")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rec.Code, rec.Body.String())
	}
	var body map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if body["status"] != "accepted" || body["source"] != "ci" {
		t.Fatalf("body=%#v", body)
	}
	waitAtomic(t, &n, 1)
	mu.Lock()
	ev := got
	mu.Unlock()
	if ev.Channel != gateway.ChannelAPI || ev.SourceID != "ci" {
		t.Fatalf("event meta channel=%s source=%s", ev.Channel, ev.SourceID)
	}
	if ev.Payload["event"] != "push" {
		t.Fatalf("payload event=%v", ev.Payload["event"])
	}
	data, _ := ev.Payload["data"].(map[string]any)
	switch x := data["x"].(type) {
	case float64:
		if x != 1 {
			t.Fatalf("payload data=%#v", ev.Payload["data"])
		}
	case json.Number:
		if x.String() != "1" {
			t.Fatalf("payload data=%#v", ev.Payload["data"])
		}
	default:
		t.Fatalf("payload data=%#v", ev.Payload["data"])
	}
	rawKeep, _ := ev.Payload["raw"].(string)
	if !strings.Contains(rawKeep, `"event":"push"`) {
		t.Fatalf("raw snippet=%q", rawKeep)
	}
}

func TestGenericWebhookPublicExemptionAndHandlerAuth(t *testing.T) {
	s := newWebhookTestServer(t)
	t.Setenv("REMEDY_WEBHOOK_SECRET", "whsec-test-secret")
	h := s.Handler()
	raw := []byte(`{"source":"ci","event":"push","data":{"x":1}}`)

	// Middleware must not 401: path is public. Handler still requires auth.
	req := httptest.NewRequest(http.MethodPost, "/api/webhook/ci", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("no-auth status=%d want 401", rec.Code)
	}
	var errBody map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &errBody)
	if detail, _ := errBody["detail"].(string); detail != "Webhook auth required" {
		t.Fatalf("want handler detail, got %#v (middleware would differ)", errBody)
	}

	var n atomic.Int32
	s.messengerGW.RegisterHandler(func(ctx context.Context, ev gateway.Event) error {
		if ev.Kind == gateway.EventWebhook {
			n.Add(1)
		}
		return nil
	})

	req = httptest.NewRequest(http.MethodPost, "/api/webhook/ci", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Remedy-Webhook-Secret", "whsec-test-secret")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("secret-auth status=%d body=%s", rec.Code, rec.Body.String())
	}
	waitAtomic(t, &n, 1)

	req = httptest.NewRequest(http.MethodPost, "/api/webhook/ci", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Remedy-Webhook-Secret", "wrong")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusUnauthorized {
		t.Fatalf("wrong-secret status=%d", rec.Code)
	}
}

func TestGenericWebhookFailClosedWithoutSecret(t *testing.T) {
	t.Setenv("REMEDY_API_AUTH", "1")
	t.Setenv("REMEDY_API_KEY", "")
	t.Setenv("REMEDY_WEBHOOK_SECRET", "")
	home := t.TempDir()
	s, err := New(Config{
		Token:   "temp-token-will-clear-16",
		HomeDir: home,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	s.token = "" // no API token and no webhook secret → 503
	s.startMessengerGateway()

	req := httptest.NewRequest(http.MethodPost, "/api/webhook/ci",
		bytes.NewReader([]byte(`{"source":"ci","event":"push","data":{"x":1}}`)))
	req.Header.Set("Content-Type", "application/json")
	rec := httptest.NewRecorder()
	s.Handler().ServeHTTP(rec, req)
	if rec.Code != http.StatusServiceUnavailable && rec.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d want 503 or 401", rec.Code)
	}
}

func TestGenericWebhookBadBodyFamily(t *testing.T) {
	s := newWebhookTestServer(t)
	h := s.Handler()
	auth := func(req *http.Request) {
		req.Header.Set("Authorization", "Bearer test-token-not-a-secret-16")
		req.Header.Set("Content-Type", "application/json")
	}
	s.messengerGW.RegisterHandler(func(ctx context.Context, ev gateway.Event) error {
		if ev.Kind == gateway.EventWebhook {
			t.Errorf("must not enqueue bad body (kind=%s)", ev.Kind)
		}
		return nil
	})

	cases := []struct {
		name string
		body []byte
		cl   int64
		want int
	}{
		{"truncated_json", []byte(`{"event": 5`), 0, http.StatusUnprocessableEntity},
		{"missing_source", []byte(`{"event":"push","data":{}}`), 0, http.StatusUnprocessableEntity},
		{"event_wrong_type", []byte(`{"source":"ci","event":5}`), 0, http.StatusUnprocessableEntity},
		{"not_object", []byte(`["ci"]`), 0, http.StatusUnprocessableEntity},
		{"empty_body", []byte(``), 0, http.StatusUnprocessableEntity},
		{"oversized", bytes.Repeat([]byte("x"), webhookMaxBytes+8), int64(webhookMaxBytes + 8), http.StatusRequestEntityTooLarge},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodPost, "/api/webhook/ci", bytes.NewReader(tc.body))
			auth(req)
			if tc.cl > 0 {
				req.ContentLength = tc.cl
			}
			rec := httptest.NewRecorder()
			h.ServeHTTP(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("status=%d want %d body=%s", rec.Code, tc.want, rec.Body.String())
			}
		})
	}
}
