package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"runtime"
	"strings"
	"testing"
	"time"
)

const sameUserTestToken = "bootstrap-same-user-tok"

func peerLookupSupported() bool {
	return runtime.GOOS == "linux" || runtime.GOOS == "windows"
}

func getJSON(t *testing.T, req *http.Request) (int, map[string]any, string) {
	t.Helper()
	resp, err := (&http.Client{Timeout: 3 * time.Second}).Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	return resp.StatusCode, body, string(raw)
}

func bootstrapGet(t *testing.T, base, nonce string) (int, map[string]any, string) {
	t.Helper()
	u := base + "/api/auth/local-bootstrap"
	if nonce != "" {
		u += "?nonce=" + url.QueryEscape(nonce)
	}
	req, _ := http.NewRequest(http.MethodGet, u, nil)
	return getJSON(t, req)
}

func mintLink(t *testing.T, base string) string {
	t.Helper()
	req, _ := http.NewRequest(http.MethodPost, base+"/api/auth/bootstrap-link", nil)
	req.Header.Set("Authorization", "Bearer "+sameUserTestToken)
	code, body, text := getJSON(t, req)
	if code != http.StatusOK {
		t.Fatalf("mint link: %d %s", code, text)
	}
	link, _ := body["url"].(string)
	u, err := url.Parse(link)
	if err != nil || u.Query().Get("bootstrap") == "" {
		t.Fatalf("bad link %q", link)
	}
	if !strings.HasPrefix(link, base+"/?bootstrap=") {
		t.Fatalf("link %q should point at this server", link)
	}
	return u.Query().Get("bootstrap")
}

func startBootstrapServer(t *testing.T) string {
	t.Helper()
	t.Setenv("REMEDY_HTTP_BOOTSTRAP", "1")
	t.Setenv("REMEDY_API_AUTH", "1")
	base, shutdown := startTestServer(t, Config{Token: sameUserTestToken, HomeDir: t.TempDir()})
	t.Cleanup(shutdown)
	return base
}

func stubPeer(t *testing.T, same bool, err error) {
	t.Helper()
	orig := peerIsSameUser
	peerIsSameUser = func(*http.Request) (bool, error) { return same, err }
	t.Cleanup(func() { peerIsSameUser = orig })
}

// The real lookup: this test process is both server and client, so the peer
// is the same OS user (Linux /proc/net/tcp, Windows GetExtendedTcpTable).
func TestBootstrapRealPeerLookupSameUser(t *testing.T) {
	base := startBootstrapServer(t)
	code, body, text := bootstrapGet(t, base, "")
	if peerLookupSupported() {
		if code != http.StatusOK || body["token"] != sameUserTestToken {
			t.Fatalf("same-user bootstrap: %d %s", code, text)
		}
		return
	}
	if code != http.StatusForbidden || body["error"] != "bootstrap_link_required" {
		t.Fatalf("unsupported OS must need a link: %d %s", code, text)
	}
}

func TestBootstrapRefusesAnotherOSUser(t *testing.T) {
	base := startBootstrapServer(t)
	nonce := mintLink(t, base)
	stubPeer(t, false, nil)
	// Even a valid link does not override a known different user.
	code, body, text := bootstrapGet(t, base, nonce)
	if code != http.StatusForbidden || body["error"] != "different_os_user" {
		t.Fatalf("other user got: %d %s", code, text)
	}
	if strings.Contains(text, sameUserTestToken) {
		t.Fatal("token leaked in refusal")
	}
}

func TestBootstrapLinkIsSingleUseWhenPeerUnknown(t *testing.T) {
	base := startBootstrapServer(t)
	stubPeer(t, false, errPeerUnsupported)

	if code, body, _ := bootstrapGet(t, base, ""); code != http.StatusForbidden || body["error"] != "bootstrap_link_required" {
		t.Fatalf("no link: %d %v", code, body)
	}
	if code, _, _ := bootstrapGet(t, base, strings.Repeat("A", 43)); code != http.StatusForbidden {
		t.Fatalf("guessed link accepted: %d", code)
	}
	nonce := mintLink(t, base)
	code, body, text := bootstrapGet(t, base, nonce)
	if code != http.StatusOK || body["token"] != sameUserTestToken {
		t.Fatalf("valid link: %d %s", code, text)
	}
	if code, _, _ := bootstrapGet(t, base, nonce); code != http.StatusForbidden {
		t.Fatalf("link reused: %d", code)
	}
}

func TestBootstrapLinkNeedsTokenToMint(t *testing.T) {
	base := startBootstrapServer(t)
	req, _ := http.NewRequest(http.MethodPost, base+"/api/auth/bootstrap-link", nil)
	if code, _, _ := getJSON(t, req); code != http.StatusUnauthorized {
		t.Fatalf("unauthenticated mint: %d", code)
	}
}

func TestBootstrapLinkExpires(t *testing.T) {
	var b bootstrapLinks
	now := time.Now()
	nonce := b.mint(now)
	if b.consume(nonce, now.Add(bootstrapLinkTTL+time.Second)) {
		t.Fatal("expired link accepted")
	}
	if b.consume(nonce, now) {
		t.Fatal("expired link must also be burned")
	}
	for i := 0; i < 100; i++ {
		b.mint(now)
	}
	if len(b.nonces) > 32 {
		t.Fatalf("links unbounded: %d", len(b.nonces))
	}
}

func TestBootstrapHostOddities(t *testing.T) {
	base := startBootstrapServer(t)
	stubPeer(t, true, nil)
	for _, host := range []string{"[2001:db8::1]:7400", "testserver", "testclient:7400", "evil.example"} {
		req, _ := http.NewRequest(http.MethodGet, base+"/api/auth/local-bootstrap", nil)
		req.Host = host
		if code, _, text := getJSON(t, req); code != http.StatusForbidden {
			t.Fatalf("Host %q: %d %s", host, code, text)
		}
	}
	req, _ := http.NewRequest(http.MethodGet, base+"/api/auth/local-bootstrap", nil)
	req.Host = "[::1]:7400"
	if code, _, text := getJSON(t, req); code != http.StatusOK {
		t.Fatalf("[::1] host: %d %s", code, text)
	}
}
