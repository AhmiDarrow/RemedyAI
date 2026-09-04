package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func TestTailscaleStatusRouteLoopbackOnly(t *testing.T) {
	prev := tailscaleStatus
	t.Cleanup(func() { tailscaleStatus = prev })
	tailscaleStatus = func() connect.TailscaleStatus {
		return connect.TailscaleStatus{Error: "canned"}
	}

	s, _ := newConnectTestServer(t)
	code, _, text := doConnectJSON(t, s, http.MethodGet, "/api/connect/tailscale/status", nil, mapHeader(
		"Authorization", "Bearer "+connectTestToken,
		"X-Remedy-Connect-Hop", "1",
	))
	if code != http.StatusForbidden {
		t.Fatalf("hop status %d %s", code, text)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/connect/tailscale/status", nil)
	req.Header.Set("Authorization", "Bearer "+connectTestToken)
	req.RemoteAddr = "10.9.8.7:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusForbidden {
		t.Fatalf("non-loopback status %d body=%s", rr.Code, rr.Body.String())
	}

	code, body, text := doConnectJSON(t, s, http.MethodGet, "/api/connect/tailscale/status", nil, connectAuthHeader())
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	for _, k := range []string{"installed", "running", "logged_in", "tailnet_ipv4", "version", "error"} {
		if _, ok := body[k]; !ok {
			t.Fatalf("missing %s in %s", k, text)
		}
	}
	if errStr, _ := body["error"].(string); errStr != "canned" {
		t.Fatalf("error=%v", body["error"])
	}
}

func TestTailscaleInstallAndLoginLoopbackOnly(t *testing.T) {
	prevEnsure := tailscaleEnsure
	prevLogin := tailscaleLogin
	t.Cleanup(func() {
		tailscaleEnsure = prevEnsure
		tailscaleLogin = prevLogin
	})
	tailscaleEnsure = func(string) connect.TailscaleAction {
		return connect.TailscaleAction{Status: "error", Message: "canned"}
	}
	tailscaleLogin = func() connect.TailscaleAction {
		return connect.TailscaleAction{Status: "needs_login", Message: "canned"}
	}

	s, _ := newConnectTestServer(t)
	for _, path := range []string{"/api/connect/tailscale/install", "/api/connect/tailscale/login"} {
		code, _, text := doConnectJSON(t, s, http.MethodPost, path, nil, mapHeader(
			"Authorization", "Bearer "+connectTestToken,
			"X-Remedy-Connect-Hop", "1",
		))
		if code != http.StatusForbidden {
			t.Fatalf("%s hop status %d %s", path, code, text)
		}
		code, _, text = doConnectJSON(t, s, http.MethodPost, path, nil, connectAuthHeader())
		if code != http.StatusOK && code != http.StatusInternalServerError {
			t.Fatalf("%s status %d %s", path, code, text)
		}
	}
}

func TestTailscaleInstallErrorMapsTo500(t *testing.T) {
	prev := tailscaleEnsure
	t.Cleanup(func() { tailscaleEnsure = prev })
	tailscaleEnsure = func(string) connect.TailscaleAction {
		return connect.TailscaleAction{Status: "error", Message: "boom"}
	}
	s, _ := newConnectTestServer(t)
	code, body, text := doConnectJSON(t, s, http.MethodPost, "/api/connect/tailscale/install", nil, connectAuthHeader())
	if code != http.StatusInternalServerError {
		t.Fatalf("status %d %s", code, text)
	}
	if detail, _ := body["detail"].(string); !strings.Contains(detail, "boom") {
		raw, _ := json.Marshal(body)
		t.Fatalf("body=%s", raw)
	}
}

func mapHeader(kv ...string) http.Header {
	h := make(http.Header)
	for i := 0; i+1 < len(kv); i += 2 {
		h.Set(kv[i], kv[i+1])
	}
	return h
}
