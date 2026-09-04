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
