package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

const googleTestToken = "tok-google-test-not-a-secret"

func newGoogleTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_GOOGLE_OAUTH_CLIENT_ID", "")
	t.Setenv("REMEDY_GOOGLE_OAUTH_CLIENT_SECRET", "")
	t.Setenv("GOOGLE_OAUTH_CLIENT_ID", "")
	t.Setenv("GOOGLE_OAUTH_CLIENT_SECRET", "")
	t.Setenv("REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_ID", "")
	t.Setenv("REMEDY_GOOGLE_OAUTH_DEFAULT_CLIENT_SECRET", "")
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   googleTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doGoogleJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+googleTestToken)
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, rr.Body.String()
}

func TestGoogleStatusNotConfigured(t *testing.T) {
	s, _ := newGoogleTestServer(t)
	code, body, text := doGoogleJSON(t, s, http.MethodGet, "/api/assistant/google", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d %s", code, text)
	}
	if body["connected"] != false || body["provider"] != "google" {
		t.Fatalf("body=%v", body)
	}
	if body["sign_in_ready"] != false || body["setup_hint"] != "not_configured" {
		t.Fatalf("setup=%v", body)
	}
}

func TestGoogleSaveAppAndOAuthGates(t *testing.T) {
	s, _ := newGoogleTestServer(t)
	code, body, text := doGoogleJSON(t, s, http.MethodPut, "/api/assistant/google/app", map[string]any{
		"client_id":     "cid-from-api",
		"client_secret": "sec",
	})
	if code != http.StatusOK {
		t.Fatalf("save app %d %s", code, text)
	}
	app, _ := body["app"].(map[string]any)
	if app["client_id_set"] != true || app["client_secret_set"] != true {
		t.Fatalf("app=%v", app)
	}

	code, body, text = doGoogleJSON(t, s, http.MethodPost, "/api/assistant/google/oauth/start", map[string]any{})
	if code != http.StatusForbidden {
		t.Fatalf("oauth without consent want 403 got %d %s", code, text)
	}

	code, _, text = doGoogleJSON(t, s, http.MethodPut, "/api/settings", map[string]any{
		"assistant": map[string]any{
			"privacy_ai_accepted":     true,
			"account_access_accepted": true,
		},
	})
	if code != http.StatusOK {
		t.Fatalf("settings %d %s", code, text)
	}

	code, body, text = doGoogleJSON(t, s, http.MethodPost, "/api/assistant/google/oauth/start", map[string]any{})
	if code != http.StatusOK {
		t.Fatalf("oauth start %d %s", code, text)
	}
	if body["auth_url"] == nil || body["state"] == nil || body["status"] != "pending" {
		t.Fatalf("start body=%v", body)
	}
	authURL, _ := body["auth_url"].(string)
	if !strings.Contains(authURL, "accounts.google.com") || !strings.Contains(authURL, "code_challenge") {
		t.Fatalf("auth_url=%s", authURL)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/assistant/google/callback?code=x&state=bogus", nil)
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("callback status=%d", rr.Code)
	}
	ct := rr.Header().Get("Content-Type")
	if !strings.Contains(ct, "text/html") {
		t.Fatalf("content-type=%q", ct)
	}
}

func TestGoogleOAuthStartFailClosedWithoutClient(t *testing.T) {
	s, _ := newGoogleTestServer(t)
	_, _, text := doGoogleJSON(t, s, http.MethodPut, "/api/settings", map[string]any{
		"assistant": map[string]any{
			"privacy_ai_accepted":     true,
			"account_access_accepted": true,
		},
	})
	_ = text
	code, body, text := doGoogleJSON(t, s, http.MethodPost, "/api/assistant/google/oauth/start", map[string]any{})
	if code != http.StatusBadRequest {
		t.Fatalf("want 400 got %d %s", code, text)
	}
	detail, _ := body["detail"].(string)
	if !strings.Contains(detail, "REMEDY_GOOGLE_OAUTH_CLIENT_ID") {
		t.Fatalf("detail=%q", detail)
	}
}

func TestGoogleCallbackPublicNoBearer(t *testing.T) {
	s, _ := newGoogleTestServer(t)
	req := httptest.NewRequest(http.MethodGet, "/api/assistant/google/callback?error=access_denied", nil)
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status=%d", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "Google sign-in failed") {
		t.Fatalf("body=%s", rr.Body.String())
	}
}

func TestGoogleDisconnect(t *testing.T) {
	s, home := newGoogleTestServer(t)
	_ = saveGoogleTokens(home, googleTokens{
		AccessToken:  "ya29.not-real",
		RefreshToken: "1//not-real",
		ExpiresAt:    9999999999,
		Email:        "user@example.com",
	})
	code, body, text := doGoogleJSON(t, s, http.MethodDelete, "/api/assistant/google", nil)
	if code != http.StatusOK {
		t.Fatalf("disconnect %d %s", code, text)
	}
	google, _ := body["google"].(map[string]any)
	if google["connected"] != false {
		t.Fatalf("google=%v", google)
	}
}
