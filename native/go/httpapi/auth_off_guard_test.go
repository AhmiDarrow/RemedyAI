package httpapi

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// With REMEDY_API_AUTH=0 the API has no token, so a DNS-rebinding page
// (Host: attacker name) or a cross-site no-cors POST must not reach handlers.
func TestAuthOffRefusesRebindingAndCrossSite(t *testing.T) {
	t.Setenv("REMEDY_API_AUTH", "0")
	t.Setenv("REMEDY_API_KEY", "")
	reached := false
	s := &Server{}
	h := s.withAuth(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	}))

	cases := []struct {
		name, host, origin string
		want               int
	}{
		{"rebinding host", "rebind.attacker.example:7400", "", http.StatusForbidden},
		{"rebinding host with own origin", "rebind.attacker.example:7400", "http://rebind.attacker.example:7400", http.StatusForbidden},
		{"cross-site post to loopback", "127.0.0.1:7400", "https://attacker.example", http.StatusForbidden},
		{"null origin", "127.0.0.1:7400", "null", http.StatusForbidden},
		{"bracket prefix is not loopback", "[2001:db8::1]:7400", "", http.StatusForbidden},
		{"cli on loopback", "127.0.0.1:7400", "", http.StatusOK},
		{"web ui same origin", "localhost:7400", "http://localhost:7400", http.StatusOK},
		{"ipv6 loopback", "[::1]:7400", "http://[::1]:7400", http.StatusOK},
		{"desktop webview", "127.0.0.1:7400", "tauri://localhost", http.StatusOK},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			reached = false
			req := httptest.NewRequest(http.MethodPost, "/api/chat", nil)
			req.Host = tc.host
			if tc.origin != "" {
				req.Header.Set("Origin", tc.origin)
			}
			rec := httptest.NewRecorder()
			h.ServeHTTP(rec, req)
			if rec.Code != tc.want {
				t.Fatalf("status = %d, want %d", rec.Code, tc.want)
			}
			if reached != (tc.want == http.StatusOK) {
				t.Fatalf("handler reached = %v", reached)
			}
		})
	}
}

// Auth on with a token is unchanged: the token is the barrier, Host is not.
func TestAuthOnStillUsesTokenOnly(t *testing.T) {
	t.Setenv("REMEDY_API_AUTH", "1")
	s := &Server{token: "tok-not-a-secret-123456"}
	h := s.withAuth(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusOK) }))
	req := httptest.NewRequest(http.MethodGet, "/api/sessions", nil)
	req.Header.Set("Authorization", "Bearer tok-not-a-secret-123456")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
}
