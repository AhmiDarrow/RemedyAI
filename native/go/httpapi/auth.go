package httpapi

import (
	"crypto/subtle"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// PublicPaths need no Bearer token (health / readiness).
var PublicPaths = map[string]struct{}{
	"/api/ping":        {},
	"/api/status":      {},
	"/api/turn-active": {},
}

const unauthorizedDetail = "Missing or invalid Bearer token. " +
	"Desktop loads it automatically; CLI: REMEDY_API_KEY."

// AuthEnabled mirrors Python local_auth.auth_enabled().
func AuthEnabled() bool {
	flag := strings.ToLower(strings.TrimSpace(os.Getenv("REMEDY_API_AUTH")))
	switch flag {
	case "0", "false", "no", "off", "disable", "disabled":
		return false
	default:
		return true
	}
}

// ResolveToken returns REMEDY_API_KEY or the on-disk local API token
// (plaintext, DPAPI v2 / legacy envelopes, or local_api_token.posix fallback).
func ResolveToken(homeDir string) string {
	if !AuthEnabled() {
		return ""
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_API_KEY")); env != "" {
		return env
	}
	home := strings.TrimSpace(homeDir)
	if home == "" {
		home = strings.TrimSpace(os.Getenv("REMEDY_HOME"))
	}
	if home == "" {
		userHome, err := os.UserHomeDir()
		if err != nil || userHome == "" {
			return ""
		}
		home = filepath.Join(userHome, ".remedy")
	}
	return secret.ReadLocalAPIToken(home)
}

func (s *Server) withAuth(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions {
			next.ServeHTTP(w, r)
			return
		}
		if _, ok := PublicPaths[r.URL.Path]; ok {
			next.ServeHTTP(w, r)
			return
		}
		// No token configured ⇒ auth off (matches Python when api_key is empty).
		if s.token == "" {
			next.ServeHTTP(w, r)
			return
		}
		if !pathNeedsAPIAuth(r.URL.Path) {
			next.ServeHTTP(w, r)
			return
		}
		if !requestAuthorized(r, s.token) {
			writeUnauthorized(w)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func pathNeedsAPIAuth(path string) bool {
	return strings.HasPrefix(path, "/api/")
}

func requestAuthorized(r *http.Request, token string) bool {
	auth := r.Header.Get("Authorization")
	want := "Bearer " + token
	if secretEquals(auth, want) {
		return true
	}
	alt := r.Header.Get("X-Remedy-Token")
	return alt != "" && secretEquals(alt, token)
}

func secretEquals(a, b string) bool {
	if len(a) != len(b) {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}

func writeUnauthorized(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusUnauthorized)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"error":  "Unauthorized",
		"detail": unauthorizedDetail,
	})
}
