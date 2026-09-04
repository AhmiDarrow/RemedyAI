package connect

import (
	"encoding/json"
	"net/url"
	"strings"
)

// Fail-closed path families for the RemedyConnect proxy.
// Hard denies run before pane flags so a phone can never become the computer
// host poller or mint local_api_token.

var jobsNextPrefixes = []string{
	"/api/computer/jobs/next",
}

var hostPrefixes = []string{
	"/api/computer/host",
}

var hostPollerPrefixes = []string{
	"/api/computer/ui/command",
	"/api/computer/jobs/",
	"/api/computer/a11y/",
}

var bootstrapPrefixes = []string{
	"/api/auth/local-bootstrap",
}

// connectMePaths are phone-safe Connect routes (not management).
var connectMePaths = map[string]struct{}{
	"/connect/me":          {},
	"/api/connect/me":      {},
	"/connect/preview":     {},
	"/api/connect/preview": {},
}

var credentialPrefixes = []string{
	"/api/auth",
	"/api/providers",
	"/api/assistant",
	"/api/webhooks",
	"/api/webhook",
}

var credentialPaths = []string{
	"/api/memory/persona-wipe",
	"/api/memory/import",
	"/api/partner/identity/import",
	"/api/partner/identity/export",
	"/api/skills/import",
	"/api/sessions/import",
}

var railsPrefixes = []string{
	"/api/workspace",
	"/api/files",
	"/api/scratch",
	"/api/terminal",
	"/api/browser",
	"/api/computer/ui/command",
	"/api/media",
}

var chatPrefixes = []string{
	"/api/chat",
	"/api/hive",
}

const sessionsPrefix = "/api/sessions"

var liveUIPrefixes = []string{
	"/api/events",
	"/api/partner",
}

var blockedMethods = map[string]struct{}{
	"CONNECT": {},
	"TRACE":   {},
	"TRACK":   {},
}

// Server-control surface. Hard-denied before pane flags.
// /api/stop is NOT here: it aborts only the phone's own turn.
var serverKillPrefixes = []string{
	"/api/shutdown",
	"/api/quit",
	"/api/restart",
	"/api/exit",
	"/api/app/command",
}

// Families the phone may reach once pane checks pass. Unlisted /api prefixes
// are refused (see the tail of ConnectForbidden).
var phoneFamilies = func() []string {
	out := make([]string, 0, 32)
	out = append(out, railsPrefixes...)
	out = append(out, chatPrefixes...)
	out = append(out, liveUIPrefixes...)
	out = append(out,
		sessionsPrefix,
		"/api/approvals",
		"/api/turn-active",
		"/api/stop",
		"/api/goals",
		"/api/models",
		"/api/ping",
		"/api/health",
		"/api/status",
		"/api/providers/connected",
		"/api/providers/free",
		"/api/settings",
		"/api/computer/capture",
		"/api/connect/me",
		"/api/connect/preview",
	)
	return out
}()

var providerSafeKeys = map[string]struct{}{
	"llm_provider": {},
	"llm_model":    {},
	"provider":     {},
	"model":        {},
}

var settingsLockKeys = map[string]struct{}{
	"connect_enabled":     {},
	"connect_bind_host":   {},
	"connect_bind_port":   {},
	"connect_relay_url":   {},
	"connect_paused":      {},
	"connect_panes":       {},
	"connect_allow_ipv6":  {},
	"connect_rdv_enabled": {},
	"llm_api_key":         {},
	"api_key":             {},
	"http_bootstrap":      {},
	"provider_keys":       {},
	"messengers":          {},
	"assistant":           {},
}

// SanitizeOriginPath returns a relative URL path only.
// ok=false means refuse (absolute / traversal / undecodable).
func SanitizeOriginPath(path string) (string, bool) {
	raw := strings.TrimSpace(path)
	if raw == "" {
		raw = "/"
	}
	if i := strings.IndexByte(raw, '?'); i >= 0 {
		raw = raw[:i]
	}
	if i := strings.IndexByte(raw, '#'); i >= 0 {
		raw = raw[:i]
	}
	if strings.Contains(raw, `\`) || strings.ContainsRune(raw, 0) {
		return "", false
	}
	low := strings.ToLower(raw)
	if strings.Contains(low, "://") || strings.HasPrefix(low, "//") ||
		strings.HasPrefix(low, "http:") || strings.HasPrefix(low, "https:") {
		return "", false
	}
	for range 4 {
		nxt, err := url.PathUnescape(raw)
		if err != nil {
			return "", false
		}
		if nxt == raw {
			break
		}
		raw = nxt
		rl := strings.ToLower(raw)
		if strings.Contains(rl, "://") || strings.HasPrefix(raw, "//") {
			return "", false
		}
	}
	// A # / ? that only appeared after percent-decoding would truncate the
	// path after the deny check. Leftover % means undecodable junk.
	if strings.ContainsAny(raw, "#?%\\\x00") {
		return "", false
	}
	if !strings.HasPrefix(raw, "/") {
		raw = "/" + raw
	}
	parts := make([]string, 0, 8)
	for _, seg := range strings.Split(raw, "/") {
		if seg == "" || seg == "." {
			continue
		}
		if seg == ".." {
			if len(parts) > 0 {
				parts = parts[:len(parts)-1]
			}
			continue
		}
		parts = append(parts, seg)
	}
	return "/" + strings.Join(parts, "/"), true
}

func normPath(path string) string {
	safe, ok := SanitizeOriginPath(path)
	if !ok {
		return "/__invalid__"
	}
	return strings.ToLower(safe)
}

func normQuery(query string) string {
	return strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(query), "?"))
}

func queryKeys(query string) map[string]struct{} {
	out := make(map[string]struct{})
	q := normQuery(query)
	if q == "" {
		return out
	}
	vals, err := url.ParseQuery(q)
	if err != nil {
		for _, p := range strings.Split(q, "&") {
			if p == "" {
				continue
			}
			key := p
			if i := strings.IndexByte(p, '='); i >= 0 {
				key = p[:i]
			}
			out[strings.ToLower(key)] = struct{}{}
		}
		return out
	}
	for k := range vals {
		out[strings.ToLower(k)] = struct{}{}
	}
	return out
}

func starts(path string, prefixes []string) bool {
	for _, prefix := range prefixes {
		p := strings.TrimRight(prefix, "/")
		if path == p ||
			strings.HasPrefix(path, p+"/") ||
			strings.HasPrefix(path, p+"?") ||
			strings.HasPrefix(path, p+"#") {
			return true
		}
	}
	return false
}

func connectMgmt(path string) bool {
	if _, ok := connectMePaths[path]; ok {
		return false
	}
	if path == "/connect" || strings.HasPrefix(path, "/connect/") {
		return true
	}
	return path == "/api/connect" || strings.HasPrefix(path, "/api/connect/")
}

func wipeOrImport(path string) bool {
	for _, seg := range strings.Split(path, "/") {
		if seg == "" {
			continue
		}
		if seg == "wipe" || seg == "import" {
			return true
		}
		if strings.HasSuffix(seg, "-wipe") || strings.HasSuffix(seg, "-import") {
			return true
		}
		if strings.HasPrefix(seg, "wipe-") || strings.HasPrefix(seg, "import-") {
			return true
		}
	}
	return false
}

func credentialWriter(path string) bool {
	if starts(path, credentialPrefixes) {
		return true
	}
	if starts(path, credentialPaths) {
		return true
	}
	return wipeOrImport(path)
}

func isStopOrApproval(method, path string) bool {
	if path == "/api/stop" && strings.ToUpper(method) == "POST" {
		return true
	}
	if strings.HasPrefix(path, "/api/approvals") {
		return true
	}
	if strings.HasSuffix(path, "/abort") && strings.HasPrefix(path, "/api/sessions/") {
		return true
	}
	if strings.Contains(path, "/abort") && strings.HasPrefix(path, "/api/sessions/") {
		return true
	}
	return path == "/api/turn-active"
}

func jobsNextFamily(path, query string) bool {
	if starts(path, jobsNextPrefixes) {
		return true
	}
	if strings.Contains(path, "/computer/jobs/next") {
		return true
	}
	keys := queryKeys(query)
	if !strings.HasPrefix(path, "/api/computer/jobs") {
		return false
	}
	for _, k := range []string{"wait_ms", "driver", "only", "take"} {
		if _, ok := keys[k]; ok {
			return true
		}
	}
	return false
}

// SettingsBodySafeProvider is true when a /api/settings body only switches provider/model.
func SettingsBodySafeProvider(body []byte) bool {
	if len(body) == 0 {
		return false
	}
	var obj map[string]any
	if err := json.Unmarshal(body, &obj); err != nil {
		return false
	}
	if len(obj) == 0 {
		return false
	}
	for key, value := range obj {
		k := strings.ToLower(strings.TrimSpace(key))
		if _, ok := providerSafeKeys[k]; !ok {
			return false
		}
		if value != nil {
			if _, ok := value.(string); !ok {
				return false
			}
		}
	}
	return true
}

// SettingsWriteLocked returns "" when ok, or "settings:locked" when the body
// tries to retarget Connect / credentials even with the settings-write pane on.
func SettingsWriteLocked(body []byte) string {
	if len(body) == 0 {
		return ""
	}
	var obj map[string]any
	if err := json.Unmarshal(body, &obj); err != nil || obj == nil {
		return "settings:locked"
	}
	for key := range obj {
		k := strings.ToLower(strings.TrimSpace(key))
		if _, ok := settingsLockKeys[k]; ok {
			return "settings:locked"
		}
	}
	return ""
}

// ConnectForbidden returns a reason when the phone must get 403, else "".
func ConnectForbidden(method, path, query string, panes map[string]bool) string {
	methodU := strings.ToUpper(strings.TrimSpace(method))
	if methodU == "" {
		methodU = "GET"
	}
	if _, ok := blockedMethods[methodU]; ok {
		return "method"
	}
	norm := normPath(path)
	if norm == "/__invalid__" {
		return "path"
	}
	q := normQuery(query)
	flags := NormalizePanes(panes)

	if jobsNextFamily(norm, q) {
		return "host-poller:jobs/next"
	}
	if starts(norm, hostPrefixes) {
		return "host-poller:host"
	}
	if starts(norm, hostPollerPrefixes) {
		return "host-poller"
	}
	if starts(norm, bootstrapPrefixes) || strings.Contains(norm, "local-bootstrap") {
		return "auth:local-bootstrap"
	}
	if starts(norm, serverKillPrefixes) {
		return "server:kill"
	}
	if connectMgmt(norm) {
		return "connect:mgmt"
	}

	if isStopOrApproval(methodU, norm) {
		return ""
	}

	if methodU == "POST" && (norm == "/api/computer/capture" || strings.HasPrefix(norm, "/api/computer/capture/")) {
		if !flags["computer_preview"] {
			return "pane:computer_preview"
		}
	}

	if (methodU == "PUT" || methodU == "PATCH" || methodU == "DELETE") && strings.HasPrefix(norm, "/api/settings") {
		if !flags["settings_write"] {
			return "pane:settings_write"
		}
	}

	// Read-only provider glance for the phone Settings pane (no secrets).
	if methodU == "GET" && (norm == "/api/providers/connected" || norm == "/api/providers/free") {
		return ""
	}

	if credentialWriter(norm) && !flags["settings_write"] {
		return "pane:settings_write"
	}

	if !flags["rails"] && starts(norm, railsPrefixes) {
		return "pane:rails"
	}

	if !flags["chat"] && starts(norm, chatPrefixes) {
		return "pane:chat"
	}

	if !flags["sessions"] && strings.HasPrefix(norm, sessionsPrefix) {
		return "pane:sessions"
	}

	if !flags["live_ui"] && starts(norm, liveUIPrefixes) {
		return "pane:live_ui"
	}

	// Fail closed on everything the phone has no pane for.
	if strings.HasPrefix(norm, "/api/") &&
		!starts(norm, phoneFamilies) &&
		!credentialWriter(norm) {
		return "unknown:family"
	}

	return ""
}
