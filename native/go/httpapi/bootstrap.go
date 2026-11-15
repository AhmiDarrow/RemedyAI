package httpapi

import (
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"log"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

const bootstrapNote = "loopback-only; handed only to a process of the same OS user, " +
	"or to a browser holding a one-time link from remedy serve / remedy open"

// bootstrapLinkTTL bounds how long a printed one-time link stays usable.
const bootstrapLinkTTL = 10 * time.Minute

// bootstrapLinks holds single-use nonces for /api/auth/local-bootstrap?nonce=.
type bootstrapLinks struct {
	mu     sync.Mutex
	nonces map[string]time.Time
}

func (b *bootstrapLinks) mint(now time.Time) string {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return ""
	}
	nonce := base64.RawURLEncoding.EncodeToString(raw)
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.nonces == nil {
		b.nonces = map[string]time.Time{}
	}
	for n, exp := range b.nonces {
		if now.After(exp) {
			delete(b.nonces, n)
		}
	}
	if len(b.nonces) >= 32 {
		// Bound memory; the oldest links are the least likely to be used.
		var oldest string
		var at time.Time
		for n, exp := range b.nonces {
			if oldest == "" || exp.Before(at) {
				oldest, at = n, exp
			}
		}
		delete(b.nonces, oldest)
	}
	b.nonces[nonce] = now.Add(bootstrapLinkTTL)
	return nonce
}

// consume reports whether nonce is a live link, and burns it either way.
func (b *bootstrapLinks) consume(nonce string, now time.Time) bool {
	nonce = strings.TrimSpace(nonce)
	if len(nonce) < 32 {
		return false
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	for n, exp := range b.nonces {
		if subtle.ConstantTimeCompare([]byte(n), []byte(nonce)) == 1 {
			delete(b.nonces, n)
			return now.Before(exp)
		}
	}
	return false
}

// HTTPBootstrapEnabled mirrors Python local_auth.http_bootstrap_enabled.
// Priority: REMEDY_HTTP_BOOTSTRAP > config.toml http_bootstrap >
// default false for desktop sidecar, true for plain serve.
func HTTPBootstrapEnabled(homeDir string) bool {
	if env, ok := envTruthy("REMEDY_HTTP_BOOTSTRAP"); ok {
		return env
	}
	cfg := LoadConfig(homeDir)
	if _, ok := cfg["http_bootstrap"]; ok {
		return cfgBool(cfg, "http_bootstrap", true)
	}
	return !desktopSidecarContext()
}

func desktopSidecarContext() bool {
	return envOn("REMEDY_DESKTOP_SIDECAR") || envOn("REMEDY_DESKTOP")
}

func envOn(name string) bool {
	switch strings.ToLower(strings.TrimSpace(os.Getenv(name))) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func envTruthy(name string) (bool, bool) {
	if _, ok := os.LookupEnv(name); !ok {
		return false, false
	}
	flag := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
	switch flag {
	case "0", "false", "no", "off", "disable", "disabled":
		return false, true
	case "1", "true", "yes", "on", "enable", "enabled":
		return true, true
	default:
		return false, false
	}
}

func (s *Server) handleLocalBootstrap(w http.ResponseWriter, r *http.Request) {
	if !clientIsLoopback(r) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "loopback only"})
		return
	}
	if strings.TrimSpace(r.Host) != "" && !loopbackHostPort(r.Host) {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"error": "host not loopback (rebinding blocked)",
		})
		return
	}
	if !HTTPBootstrapEnabled(s.homeDir) {
		writeJSON(w, http.StatusForbidden, map[string]any{
			"error": "http_bootstrap_disabled",
			"detail": "Browser token bootstrap is off. Desktop still uses IPC " +
				"(full power). Enable Settings → Allow browser token " +
				"bootstrap, or set REMEDY_HTTP_BOOTSTRAP=1 for Web UI.",
		})
		return
	}
	if s.token == "" {
		writeJSON(w, http.StatusForbidden, map[string]string{
			"error": "no_api_token",
		})
		return
	}
	// Loopback is not a user boundary: on a shared machine every local
	// account can reach 127.0.0.1. Hand the token only to a process of the
	// same OS user, or to a browser holding a one-time link.
	same, err := peerIsSameUser(r)
	switch {
	case err == nil && !same:
		log.Printf("auth: token bootstrap refused for a process of another OS user")
		writeJSON(w, http.StatusForbidden, map[string]string{
			"error":  "different_os_user",
			"detail": "The API token is only handed to programs of the OS user running Remedy.",
		})
		return
	case err != nil && !s.bootLinks.consume(r.URL.Query().Get("nonce"), time.Now()):
		writeJSON(w, http.StatusForbidden, map[string]string{
			"error": "bootstrap_link_required",
			"detail": "Open the one-time link printed by `remedy serve`, or run " +
				"`remedy open` for a new one.",
		})
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]any{
		"token":         s.token,
		"auth_required": true,
		"note":          bootstrapNote,
	})
}

func clientIsLoopback(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	host = strings.TrimSpace(strings.ToLower(host))
	if loopbackHostName(host) {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func loopbackHostName(host string) bool {
	switch strings.TrimSpace(strings.ToLower(host)) {
	case "127.0.0.1", "::1", "localhost", "":
		return true
	default:
		// IPv6 literals may arrive as [::1]
		if strings.HasPrefix(host, "[") && strings.HasSuffix(host, "]") {
			return loopbackHostName(strings.Trim(host, "[]"))
		}
		return false
	}
}

// handleBootstrapLink mints a one-time browser link. It sits behind the API
// token, so only a caller that can already read the token (the owner's CLI)
// can create one.
func (s *Server) handleBootstrapLink(w http.ResponseWriter, r *http.Request) {
	if !clientIsLoopback(r) {
		writeJSON(w, http.StatusForbidden, map[string]string{"error": "loopback only"})
		return
	}
	nonce := s.bootLinks.mint(time.Now())
	if nonce == "" {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "rng"})
		return
	}
	host := strings.TrimSpace(r.Host)
	if !loopbackHostPort(host) {
		host = "127.0.0.1:7400"
	}
	w.Header().Set("Cache-Control", "no-store")
	writeJSON(w, http.StatusOK, map[string]any{
		"url":        "http://" + host + "/?bootstrap=" + nonce,
		"expires_in": int(bootstrapLinkTTL.Seconds()),
		"single_use": true,
	})
}
