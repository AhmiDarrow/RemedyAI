package httpapi

import (
	"net"
	"net/http"
	"os"
	"strings"
)

const bootstrapNote = "loopback-only; same Windows user can call this"

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
	hostHdr := strings.ToLower(strings.TrimSpace(strings.Split(r.Host, ":")[0]))
	if hostHdr != "" && !loopbackHostName(hostHdr) {
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
	case "127.0.0.1", "::1", "localhost", "testclient", "testserver", "[", "":
		return true
	default:
		// IPv6 literals may arrive as [::1]
		if strings.HasPrefix(host, "[") && strings.HasSuffix(host, "]") {
			return loopbackHostName(strings.Trim(host, "[]"))
		}
		return false
	}
}
